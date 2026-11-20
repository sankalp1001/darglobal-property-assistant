"""RAG pipeline: query understanding -> filtered vector retrieval -> grounded answer."""
from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError, field_validator
from qdrant_client import models as qm

from app import llm
from app.index import LISTINGS, PAGES, get_index

log = logging.getLogger(__name__)

USD_RATES = {"USD": 1.0, "SAR": 0.2667, "AED": 0.2723, "QAR": 0.2747, "OMR": 2.6008, "GBP": 1.27, "EUR": 1.08}
CITY_CURRENCY = {"riyadh": "SAR", "jeddah": "SAR", "dammam": "SAR", "dubai": "AED", "ras al khaimah": "AED",
                 "muscat": "OMR", "doha": "QAR", "simaisma": "QAR", "london": "GBP", "marbella": "EUR"}
PTYPES = ["apartment", "villa", "land", "hotel", "floor", "building", "rest house", "commercial"]


class QueryPlan(BaseModel):
    intent: Literal["listing_search", "company_info", "chitchat"] = "listing_search"
    city: Optional[str] = None
    country: Optional[str] = None
    property_type: Optional[str] = None
    listing_type: Optional[Literal["sale", "rent", "project"]] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    currency: Optional[str] = None
    bedrooms: Optional[int] = None
    source: Optional[Literal["darglobal", "wasalt"]] = None
    sort: Optional[Literal["cheapest", "most_expensive", "largest", "smallest"]] = None
    rewritten_query: str | None = None  # empty/null from the model -> filled with the raw message

    @field_validator("*", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return None if v in ("", "null", "None", "any", "n/a") else v

    @field_validator("property_type")
    @classmethod
    def _ptype(cls, v):
        if not v:
            return None
        v = v.lower().rstrip("s")
        for p in PTYPES:
            if p in v or v in p:
                return p
        return {"flat": "apartment", "plot": "land", "townhouse": "villa", "mansion": "villa",
                "penthouse": "apartment", "office": "commercial", "shop": "commercial"}.get(v)


UNDERSTAND_PROMPT = """You turn a user's real-estate question into a JSON search plan.
Data available: (1) DarGlobal (developer) luxury/branded off-plan projects in UAE (Dubai, Ras Al Khaimah), Oman (Muscat/AIDA), Qatar (Doha, Simaisma), Saudi Arabia (Riyadh, Jeddah), UK (London), Spain (Marbella area) — no published prices; (2) Wasalt (Saudi listings portal) apartments, villas, land for sale and rent in Riyadh, Jeddah, Dammam with SAR prices; (3) DarGlobal company pages (about, FAQ, why invest, residency/visa info, hospitality, tokenization, AIDA services, project FAQs).

Return ONLY a JSON object with these keys:
{"intent": "listing_search" | "company_info" | "chitchat",
 "city": string|null, "country": string|null,
 "property_type": "apartment"|"villa"|"land"|"hotel"|"floor"|"building"|"rest house"|"commercial"|null,
 "listing_type": "sale"|"rent"|"project"|null,
 "min_price": number|null, "max_price": number|null, "currency": "SAR"|"AED"|"USD"|"GBP"|"EUR"|"OMR"|"QAR"|null,
 "bedrooms": integer|null, "source": "darglobal"|"wasalt"|null,
 "sort": "cheapest"|"most_expensive"|"largest"|"smallest"|null,
 "rewritten_query": string}

Rules:
- rewritten_query must be a standalone search query that resolves follow-ups using the chat history (e.g. "what about cheaper ones in Jeddah?" -> "apartments for sale in Jeddah cheaper than 1,000,000 SAR").
- Expand "1.2M" to 1200000 and "800k" to 800000. "under X" sets max_price; "over/above X" sets min_price.
- Questions about DarGlobal as a company, its countries/presence, investing, visas/residency, payment, services -> "company_info". Questions asking for specific properties/projects -> "listing_search".
- "DarGlobal projects" / "branded residences" -> source "darglobal". Rentals or Saudi resale listings -> source "wasalt" unless stated otherwise.
- Greetings or thanks -> "chitchat". Unknown fields -> null. No prose, no markdown fences."""

ANSWER_PROMPT = """You are a helpful real-estate assistant for property data scraped from DarGlobal (darglobal.co.uk, a luxury developer) and Wasalt (wasalt.sa, a Saudi listings portal).

Rules:
- Answer ONLY from the CONTEXT below. If the answer is not in the context, say plainly that it isn't in the scraped data. Never invent prices, sizes, dates or listings.
- DarGlobal does not publish prices; say "price on request" for DarGlobal projects, never guess.
- When you mention a listing or project, cite it with a markdown link to its URL, e.g. [W Residences](https://...).
- Prefer a short intro sentence then a compact bullet list (name, location, beds, size, price). Keep it under ~250 words.
- Prices: keep the original currency; you may add the approximate USD value given in the context.
- If a NOTE says the search was broadened, mention it briefly.
- Rent prices are per the stated period (monthly/yearly).
- For greetings or "what can you do?", skip the data disclaimer: in two or three sentences say you can find DarGlobal projects (UAE, Saudi Arabia, Oman, Qatar, UK, Spain) and Wasalt sale/rent listings in Riyadh, Jeddah and Dammam, filter by city, type, price and bedrooms, and answer questions about DarGlobal as a company."""


def _extract_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object found")
    return json.loads(m.group(0))


def _history_text(history: list[dict], n: int = 6) -> str:
    lines = []
    for h in history[-n:]:
        content = h.get("content")
        if isinstance(content, list):  # gradio multimodal message format
            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        lines.append(f"{h.get('role', 'user')}: {str(content)[:500]}")
    return "\n".join(lines)


_CITIES = ["riyadh", "jeddah", "dammam", "dubai", "ras al khaimah", "muscat", "doha", "london", "marbella"]
_COUNTRIES = {"saudi": "Saudi Arabia", "ksa": "Saudi Arabia", "uae": "United Arab Emirates", "emirates": "United Arab Emirates",
              "oman": "Oman", "qatar": "Qatar", "spain": "Spain", "uk": "United Kingdom", "england": "United Kingdom"}
_COMPANY_RE = re.compile(r"\b(who is|who are|about (dar ?global|the company)|company|countries|offices|presence|residen(cy|ce permit)|"
                         r"visa|why invest|tokeni[sz]|aida 360|ceo|founder|mortgage|payment plan|buying process|hospitality)\b", re.I)


def heuristic_plan(message: str) -> QueryPlan:
    """Rule-based plan used when the LLM is unreachable or keeps returning bad JSON."""
    low = message.lower()
    if re.fullmatch(r"\s*(hi|hello|hey|thanks|thank you|salam)[\s!.?,]*(what can you do\??|how can you help\??)?[\s!.?]*", low):
        return QueryPlan(intent="chitchat", rewritten_query=message)
    p = QueryPlan(rewritten_query=message)
    if _COMPANY_RE.search(low) and not re.search(r"\b(apartment|villa|flat|for sale|for rent|bedroom)", low):
        p.intent = "company_info"
        return p
    p.city = next((c.title() for c in _CITIES if c in low), None)
    if not p.city:
        p.country = next((v for k, v in _COUNTRIES.items() if re.search(rf"\b{k}", low)), None)
    p.property_type = QueryPlan._ptype(next((w for w in ("apartment", "flat", "villa", "land", "plot", "townhouse",
                                                          "penthouse", "office", "building") if w in low), None))
    p.listing_type = "rent" if re.search(r"\brent|lease", low) else ("sale" if re.search(r"\b(buy|for sale|sale)\b", low) else None)
    if m := re.search(r"(\d+)\s*-?\s*(?:bed|br\b|bedroom)", low):
        p.bedrooms = int(m.group(1))
    if m := re.search(r"(under|below|less than|max(?:imum)?|up to|over|above|more than|min(?:imum)?)\s*([\d.,]+)\s*([km])?", low):
        v = float(m.group(2).replace(",", "")) * {"k": 1e3, "m": 1e6}.get(m.group(3) or "", 1)
        if m.group(1).startswith(("over", "above", "more", "min")):
            p.min_price = v
        else:
            p.max_price = v
    if m := re.search(r"\b(sar|aed|usd|gbp|eur|omr|qar)\b", low):
        p.currency = m.group(1).upper()
    dg, ws = ("darglobal" in low.replace(" ", "") or "branded" in low), "wasalt" in low
    p.source = "darglobal" if dg and not ws else "wasalt" if ws and not dg else None
    p.sort = ("cheapest" if re.search(r"cheap|lowest price|least expensive", low) else
              "most_expensive" if re.search(r"most expensive|priciest", low) else
              "largest" if re.search(r"largest|biggest", low) else
              "smallest" if "smallest" in low else None)
    return p


def understand(message: str, history: list[dict]) -> tuple[QueryPlan, bool]:
    """Return (plan, ok). On repeated failure, fall back to a rule-based plan (ok=False).

    With PLANNER=hybrid (useful on free models, which have a daily request quota), a
    first-turn question that the rule-based planner understands confidently skips the
    LLM planning call. The default (llm) always asks the model."""
    if not history and os.getenv("PLANNER", "llm") == "hybrid":
        hp = heuristic_plan(message)
        if hp.intent in ("company_info", "chitchat") or hp.city or hp.country:
            return hp, True
    hist = _history_text(history)
    user = f"Chat history:\n{hist or '(none)'}\n\nLatest user message: {message}\n\nJSON:"
    msgs = [{"role": "system", "content": UNDERSTAND_PROMPT}, {"role": "user", "content": user}]
    for attempt in range(2):
        try:
            text, _ = llm.chat(msgs, temperature=0, max_tokens=400)
            plan = QueryPlan.model_validate(_extract_json(text))
            if not plan.rewritten_query:
                plan.rewritten_query = message
            return plan, True
        except llm.LLMUnavailable:
            break
        except (ValueError, ValidationError) as e:
            log.warning("query plan parse failed (attempt %d): %s", attempt + 1, e)
            msgs.append({"role": "assistant", "content": text})
            msgs.append({"role": "user", "content": "That was not valid JSON for the schema. Reply with the JSON object only."})
    return heuristic_plan(message), False


def _to_usd(v: float | None, cur: str | None) -> float | None:
    if v is None:
        return None
    return v * USD_RATES.get((cur or "USD").upper(), 1.0)


def build_filter(p: QueryPlan, drop: set[str] = frozenset()) -> qm.Filter | None:
    must = []
    if p.source and "source" not in drop:
        must.append(qm.FieldCondition(key="source", match=qm.MatchValue(value=p.source)))
    if p.city and "city" not in drop:
        must.append(qm.FieldCondition(key="city", match=qm.MatchText(text=p.city.title())))
    elif p.country and "country" not in drop:
        must.append(qm.FieldCondition(key="country", match=qm.MatchText(text=p.country.title())))
    if p.property_type and "property_type" not in drop:
        must.append(qm.FieldCondition(key="property_type_norm", match=qm.MatchValue(value=p.property_type)))
    if p.listing_type and "listing_type" not in drop:
        lt = "off-plan / project" if p.listing_type == "project" else p.listing_type
        must.append(qm.FieldCondition(key="listing_type", match=qm.MatchValue(value=lt)))
    if p.bedrooms and "bedrooms" not in drop:
        must.append(qm.FieldCondition(key="bedrooms_min", range=qm.Range(lte=p.bedrooms)))
        must.append(qm.FieldCondition(key="bedrooms_max", range=qm.Range(gte=p.bedrooms)))
    if (p.min_price or p.max_price) and "price" not in drop:
        cur = p.currency or CITY_CURRENCY.get((p.city or "").lower()) or ("SAR" if p.source == "wasalt" else "USD")
        must.append(qm.FieldCondition(key="price_usd", range=qm.Range(gte=_to_usd(p.min_price, cur),
                                                                      lte=_to_usd(p.max_price, cur))))
    return qm.Filter(must=must) if must else None


RELAX_ORDER = [("bedrooms", "bedroom count"), ("price", "price range"), ("property_type", "property type"),
               ("listing_type", "sale/rent type")]


def retrieve_listings(p: QueryPlan, k: int = 8) -> tuple[list[dict], str | None]:
    idx = get_index()
    query = p.rewritten_query
    limit = 40 if p.sort else k
    drop: set[str] = set()
    hits = idx.search(LISTINGS, query, build_filter(p), limit)
    relaxed = []
    for key, label in RELAX_ORDER:
        if hits:
            break
        if getattr(p, {"price": "max_price"}.get(key, key), None) is None and not (key == "price" and p.min_price):
            continue
        drop.add(key)
        relaxed.append(label)
        hits = idx.search(LISTINGS, query, build_filter(p, drop), limit)
    if p.sort and hits:
        keyf = {"cheapest": lambda r: r.get("price_usd") or float("inf"),
                "most_expensive": lambda r: -(r.get("price_usd") or 0),
                "largest": lambda r: -(r.get("area_sqm_max") or 0),
                "smallest": lambda r: r.get("area_sqm_min") or float("inf")}[p.sort]
        hits = sorted(hits, key=keyf)
    note = f"No exact matches, so the search was broadened by ignoring the {', '.join(relaxed)}." if relaxed else None
    return hits[:k], note


def _period(r: dict) -> str:
    f = (r.get("extra") or {}).get("rent_frequency") or "period"
    return {"monthly": "month", "yearly": "year", "daily": "day", "weekly": "week"}.get(f, f)


def _fmt_listing(r: dict) -> str:
    beds = r.get("bedrooms_min")
    if beds is not None and r.get("bedrooms_max") not in (None, beds):
        beds = f"{beds}-{r['bedrooms_max']}"
    area = r.get("area_sqm_min")
    if area is not None and r.get("area_sqm_max") not in (None, area):
        area = f"{area:g}-{r['area_sqm_max']:g}"
    price = r.get("price_text") if r.get("price") is None else f"{r.get('currency')} {r['price']:,.0f}"
    if r.get("listing_type") == "rent" and r.get("price") is not None:
        price += f" per {_period(r)}"
    usd = f" (~USD {r['price_usd']:,.0f})" if r.get("price_usd") else ""
    lines = [
        f"- {r['title']} [{r['source']}] {r.get('url')}",
        f"  type: {r.get('property_type')} | {r.get('listing_type')} | location: "
        + ", ".join(x for x in (r.get('district'), r.get('city'), r.get('country')) if x),
        f"  price: {price}{usd} | bedrooms: {beds} | area_sqm: {area} | status: {r.get('status')} | handover: {r.get('handover_date')}",
    ]
    if r.get("amenities"):
        lines.append("  amenities: " + ", ".join(r["amenities"][:10]))
    if r.get("description"):
        lines.append("  description: " + r["description"][:450])
    return "\n".join(lines)


def _fmt_page(c: dict) -> str:
    return f"- [{c['title']}] {c['url']}\n  {c['text'][:1400]}"


def _source(r: dict) -> dict:
    price = r.get("price_text") if r.get("price") is None else f"{r.get('currency')} {r['price']:,.0f}"
    return {"title": r.get("title"), "url": r.get("url"), "source": r.get("source"), "price": price}


def _fallback_answer(hits: list[dict], chunks: list[dict], note: str | None, intent: str = "listing_search") -> str:
    """Used when no LLM is reachable: still return the retrieved data, clearly labelled."""
    if not hits and not chunks:
        return ("Hi! I answer questions about DarGlobal's projects and company info, and Wasalt listings in "
                "Riyadh, Jeddah and Dammam: e.g. *apartments for sale in Riyadh under 1M SAR* or *who is DarGlobal?*")
    out = ["(The language model is unavailable right now, so here are the closest matches from the data.)"]
    if note:
        out.append(note)
    page_lines = [f"- [{c['title']}]({c['url']}): {c['text'][:300]}…" for c in chunks[:3]]
    hit_lines = [f"- [{r['title']}]({r['url']}) — {', '.join(x for x in (r.get('district'), r.get('city')) if x)}; "
                 f"{_source(r)['price']}"
                 + (f" per {_period(r)}" if r.get("listing_type") == "rent" else "")
                 + (f"; {r['bedrooms_min']} bed" if r.get("bedrooms_min") else "")
                 + (f"; {r['area_sqm_min']:g} sqm" if r.get("area_sqm_min") else "")
                 for r in hits[:6]]
    out += (page_lines + hit_lines) if intent == "company_info" else (hit_lines + page_lines)
    return "\n".join(out)


@lru_cache(maxsize=1)
def _overview(idx) -> str:
    """One line describing the whole snapshot, so 'what is Wasalt / what do you cover' has an answer."""
    from collections import Counter

    ws = [r for r in idx.listings if r.get("source") == "wasalt"]
    dg = [r for r in idx.listings if r.get("source") == "darglobal"]
    cities = ", ".join(f"{c} {n}" for c, n in Counter(r.get("city") for r in ws).most_common())
    kinds = ", ".join(f"{k} {n}" for k, n in Counter(r.get("listing_type") for r in ws).most_common())
    countries = ", ".join(c for c, _ in Counter(r.get("country") for r in dg).most_common())
    return (f"Wasalt (wasalt.sa) is a Saudi property listings portal; this snapshot has {len(ws)} Wasalt "
            f"listings ({kinds}; by city: {cities}) with SAR prices. DarGlobal (darglobal.co.uk) is a "
            f"luxury branded-residence developer; {len(dg)} DarGlobal projects ({countries}), prices on request, "
            f"plus {len(idx.pages)} chunks of DarGlobal company pages. Scraped on {idx.meta.get('scraped_on', 'n/a')}.")


def answer(message: str, history: list[dict] | None = None) -> dict:
    history = history or []
    plan, plan_ok = understand(message, history)
    log.info("plan=%s ok=%s", plan.model_dump(exclude_none=True), plan_ok)
    idx = get_index()

    # Only DarGlobal has company pages; a general question about Wasalt is answered from its listings
    if plan.intent == "company_info" and plan.source == "wasalt":
        plan.intent = "listing_search"

    if plan.intent == "chitchat":
        hits, chunks, note = [], [], None
    elif plan.intent == "company_info":
        chunks = idx.search(PAGES, plan.rewritten_query, limit=5)
        flt = qm.Filter(must=[qm.FieldCondition(key="source", match=qm.MatchValue(value="darglobal"))])
        hits, note = idx.search(LISTINGS, plan.rewritten_query, flt, 4), None
    else:
        hits, note = retrieve_listings(plan)
        chunks = idx.search(PAGES, plan.rewritten_query, limit=2) if plan.source == "darglobal" else []

    context = [f"DATA OVERVIEW: {_overview(idx)}"]
    if note:
        context.append(f"NOTE: {note}")
    if hits and plan.sort and plan.intent == "listing_search":
        context.append(f"NOTE: LISTINGS are already sorted ({plan.sort} first). The first one is the answer; "
                       "report it even if its data looks unusual (you may point that out).")
    if hits:
        context.append("LISTINGS:\n" + "\n".join(_fmt_listing(r) for r in hits))
    if chunks:
        context.append("PAGES:\n" + "\n".join(_fmt_page(c) for c in chunks))
    ctx = "\n\n".join(context) or ("(greeting: no records needed)" if plan.intent == "chitchat" else "(no matching records)")

    msgs = [{"role": "system", "content": ANSWER_PROMPT}]
    for h in history[-6:]:
        if h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
            msgs.append({"role": h["role"], "content": h["content"][:1500]})
    msgs.append({"role": "user", "content": f"CONTEXT:\n{ctx}\n\nQUESTION: {message}"})
    try:
        text, model = llm.chat(msgs, temperature=0.2)
    except llm.LLMUnavailable as e:
        log.warning("answer LLM unavailable: %s", e)
        text, model = _fallback_answer(hits, chunks, note, plan.intent), None

    seen, sources = set(), []
    for r in hits + chunks:
        if r.get("url") not in seen:
            seen.add(r.get("url"))
            sources.append(_source(r) if "price_text" in r or "price" in r else
                           {"title": r.get("title"), "url": r.get("url"), "source": r.get("source"), "price": None})
    return {"answer": text, "sources": sources if plan.intent != "chitchat" else [],
            "plan": plan.model_dump(exclude_none=True), "plan_ok": plan_ok, "model": model, "note": note}
