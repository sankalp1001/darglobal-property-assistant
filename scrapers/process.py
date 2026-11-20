"""Step 2: normalise, dedupe, build embed_text, and chunk pages.

Reads  data/processed/listings_<site>.jsonl, pages_<site>.jsonl
Writes data/processed/listings.jsonl, pages.jsonl, meta.json

    python -m scrapers.process
"""
from __future__ import annotations

import json
import re
from datetime import date

from scrapers.common import PROCESSED_DIR

# Fixed conversion rates to USD (snapshot, see README). Pegged currencies are exact-ish.
USD_RATES = {
    "USD": 1.0, "SAR": 0.2667, "AED": 0.2723, "QAR": 0.2747, "OMR": 2.6008,
    "GBP": 1.27, "EUR": 1.08,
}
CITY_CANON = {
    "riyadh": "Riyadh", "الرياض": "Riyadh",
    "jeddah": "Jeddah", "jedda": "Jeddah", "جدة": "Jeddah", "جده": "Jeddah",
    "dammam": "Dammam", "aldammam": "Dammam", "al dammam": "Dammam", "al-dammam": "Dammam", "الدمام": "Dammam",
    "khobar": "Al Khobar", "al khobar": "Al Khobar", "الخبر": "Al Khobar",
    "dubai": "Dubai", "دبي": "Dubai",
    "muscat": "Muscat", "مسقط": "Muscat",
    "doha": "Doha", "الدوحة": "Doha",
}
PTYPE_CANON = [
    (r"apartment|flat|residen|tower|penthouse|duplex", "apartment"),
    (r"villa|mansion|townhouse", "villa"),
    (r"land|plot", "land"),
    (r"hotel|room", "hotel"),
    (r"floor", "floor"),
    (r"building", "building"),
    (r"rest ?house|istiraha|chalet", "rest house"),
    (r"office|shop|showroom|warehouse|commercial", "commercial"),
]
CHUNK_CHARS = 1600   # ~400 tokens
OVERLAP_CHARS = 200


def canon_city(c: str | None) -> str | None:
    if not c:
        return None
    c = c.strip()
    return CITY_CANON.get(c.lower(), CITY_CANON.get(c, c.title() if c.isupper() or c.islower() else c))


def canon_ptype(p: str | None, title: str = "", description: str = "") -> str | None:
    # most specific text first: declared type, then title, then description
    for blob in (p or "", title, (description or "")[:300]):
        for pat, name in PTYPE_CANON:
            if re.search(pat, blob.lower()):
                return name
    return None


def parse_price(text: str | None) -> float | None:
    """'From AED 1.2M' -> 1200000; 'Price on request' -> None."""
    if not text or re.search(r"request|call|tbc|n/a", text, re.I):
        return None
    m = re.search(r"(\d[\d,]*\.?\d*)\s*([kKmMbB])?", text)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return v * {"k": 1e3, "m": 1e6, "b": 1e9}.get((m.group(2) or "").lower(), 1)


def fmt_money(v: float | None, cur: str | None) -> str:
    return f"{cur} {v:,.0f}" if v is not None and cur else "price on request"


def rng(a, b, unit="") -> str | None:
    if a is None:
        return None
    return f"{a:g}{unit}" if a == b or b is None else f"{a:g}-{b:g}{unit}"


def embed_text(r: dict) -> str:
    beds = rng(r.get("bedrooms_min"), r.get("bedrooms_max"))
    area = rng(r.get("area_sqm_min"), r.get("area_sqm_max"), " sqm")
    where = ", ".join(x for x in (r.get("district"), r.get("city"), r.get("country")) if x)
    kind = r.get("property_type") or r.get("property_type_norm") or "property"
    parts = [f"{r['title']}.", f"{beds} bedroom {kind}" if beds else kind.capitalize(), f"in {where}." if where else "."]
    s = " ".join(parts)
    lt = r.get("listing_type")
    if lt == "rent":
        s += f" For rent: {r.get('price_text') or fmt_money(r.get('price'), r.get('currency'))}."
    elif lt == "sale":
        s += f" For sale: {fmt_money(r.get('price'), r.get('currency'))}."
    else:
        s += f" Off-plan / branded project by {r.get('developer') or 'the developer'}; price on request."
    if area:
        s += f" Area {area}."
    if r.get("status"):
        s += f" Status: {r['status']}."
    if r.get("handover_date"):
        s += f" Handover: {r['handover_date']}."
    if r.get("amenities"):
        s += " Amenities: " + ", ".join(r["amenities"][:12]) + "."
    if r.get("description"):
        s += " " + r["description"][:700]
    extra = r.get("extra") or {}
    if extra.get("nearby"):
        s += f" Nearby: {extra['nearby']}."
    return s.strip()


def chunk(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) + 1 > size:
            chunks.append(cur)
            cur = cur[-overlap:] + "\n" + p  # small overlap for context continuity
        else:
            cur = f"{cur}\n{p}" if cur else p
        while len(cur) > size * 1.5:  # very long paragraph: hard split
            chunks.append(cur[:size])
            cur = cur[size - overlap:]
    if cur:
        chunks.append(cur)
    return chunks


def load(pattern: str) -> list[dict]:
    rows = []
    for f in sorted(PROCESSED_DIR.glob(pattern)):
        rows += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows


def main():
    listings = load("listings_*.jsonl")
    pages = load("pages_*.jsonl")

    seen_url, seen_key, out = set(), set(), []
    for r in listings:
        if r["url"] in seen_url:
            continue
        key = (r["title"].strip().lower(), r.get("price"), r.get("area_sqm_min"))
        if r.get("price") is not None and key in seen_key:
            continue
        seen_url.add(r["url"])
        seen_key.add(key)

        r["city"] = canon_city(r.get("city"))
        if r.get("price") is None and r.get("price_text"):
            r["price"] = parse_price(r["price_text"])
        rate = USD_RATES.get((r.get("currency") or "").upper())
        r["price_usd"] = round(r["price"] * rate) if r.get("price") is not None and rate else None
        r["property_type_norm"] = canon_ptype(r.get("property_type"), r["title"], r.get("description"))
        r["embed_text"] = embed_text(r)
        out.append(r)

    chunks = []
    for p in pages:
        for i, c in enumerate(chunk(p["text"])):
            chunks.append({**{k: p[k] for k in ("source", "url", "title", "scraped_at")},
                           "id": f"{p['id']}-{i}", "text": c, "embed_text": f"{p['title']}\n{c}"})

    with open(PROCESSED_DIR / "listings.jsonl", "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(PROCESSED_DIR / "pages.jsonl", "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    counts = {}
    for r in out:
        counts[r["source"]] = counts.get(r["source"], 0) + 1
    scraped = max((r["scraped_at"] for r in out), default=None)
    meta = {"scraped_on": (scraped or str(date.today()))[:10], "listings": counts,
            "page_chunks": len(chunks), "usd_rates": USD_RATES}
    (PROCESSED_DIR / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"{len(listings)} -> {len(out)} listings after dedupe; {len(pages)} pages -> {len(chunks)} chunks")
    print(meta)


if __name__ == "__main__":
    main()
