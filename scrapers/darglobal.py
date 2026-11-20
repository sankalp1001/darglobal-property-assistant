"""DarGlobal (darglobal.co.uk) scraper.

How the site works
------------------
* sitemap.xml is fetchable and lists ~480 URLs; we keep project pages and
  company pages (About, FAQ, Why invest, Hospitality, ...), skipping blog/press.
* Pages are Next.js + Strapi CMS: every page embeds its full CMS record in
  <script id="__NEXT_DATA__">, so no HTML parsing is needed.
* BUT all HTML pages sit behind Imperva/Incapsula, which answers non-browser
  clients with a JavaScript challenge. We deliberately do not try to defeat it
  from a script. Instead `capture_darglobal.js` is run once in a normal browser
  session on darglobal.co.uk: it fetches each page same-origin (1.5s apart),
  reads __NEXT_DATA__, condenses it to the fields we need, and the result is
  saved to data/raw/darglobal/_projects.json and _index_and_pages.json.
* parse() below turns that raw cache into Listing/Page records. Re-parsing never
  touches the site.

DarGlobal does not publish prices on project pages (StartingPrice is null
everywhere), so price is None with price_text "Price on request".
"""
from __future__ import annotations

import json
import re

from scrapers.common import RAW_DIR, fetch_cached
from scrapers.schema import Listing, Page

SITE = "darglobal"
BASE = "https://darglobal.co.uk"
SITEMAP = f"{BASE}/sitemap.xml"
SKIP_PREFIXES = ("/blog", "/press", "/insights", "/partners", "/landing", "/pay-online")
SQFT_TO_SQM = 0.092903
NOT_PROJECTS = {"/sohar-islamic"}  # mortgage partnership page -> indexed as a company page

COUNTRY_ALIASES = {
    "uae": "United Arab Emirates", "united arab emirates": "United Arab Emirates", "rak": "United Arab Emirates",
    "ksa": "Saudi Arabia", "saudi arabia": "Saudi Arabia",
    "oman": "Oman", "qatar": "Qatar", "spain": "Spain",
    "united kingdom": "United Kingdom", "england": "United Kingdom", "uk": "United Kingdom",
}
# location keyword -> (city, country)
CITY_HINTS = [
    ("al marjan", ("Ras Al Khaimah", "United Arab Emirates")),
    ("ras al khaimah", ("Ras Al Khaimah", "United Arab Emirates")),
    ("rak", ("Ras Al Khaimah", "United Arab Emirates")),
    ("dubai", ("Dubai", "United Arab Emirates")),
    ("jumeirah", ("Dubai", "United Arab Emirates")),
    ("aida", ("Muscat", "Oman")),
    ("muscat", ("Muscat", "Oman")),
    ("simaisma", ("Simaisma", "Qatar")),
    ("doha", ("Doha", "Qatar")),
    ("wadi safar", ("Riyadh", "Saudi Arabia")),
    ("rayana", ("Riyadh", "Saudi Arabia")),
    ("diriyah", ("Riyadh", "Saudi Arabia")),
    ("riyadh", ("Riyadh", "Saudi Arabia")),
    ("amaya", ("Jeddah", "Saudi Arabia")),
    ("trump plaza", ("Jeddah", "Saudi Arabia")),
    ("jeddah", ("Jeddah", "Saudi Arabia")),
    ("london", ("London", "United Kingdom")),
    ("old park lane", ("London", "United Kingdom")),
    ("benahav", ("Benahavís", "Spain")),
    ("cortesin", ("Casares (Costa del Sol)", "Spain")),
    ("marbella", ("Marbella", "Spain")),
    ("oman", ("Muscat", "Oman")),
]
WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
MONTHS = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"


def get_urls() -> list[str]:
    """Project + company page URLs from the sitemap (sitemap itself is not challenged)."""
    xml = fetch_cached(SITE, SITEMAP, "xml")
    urls = re.findall(r"<loc>([^<]+)</loc>", xml)
    return [u for u in dict.fromkeys(urls) if not u.replace(BASE, "").startswith(SKIP_PREFIXES)]


def fetch(url: str) -> dict:
    """Return the captured, condensed __NEXT_DATA__ for a page (see module docstring)."""
    path = url.replace(BASE, "")
    projects = _load("_projects.json")["projects"]
    if path in projects:
        return projects[path]
    return {"text": _load("_index_and_pages.json")["pages"].get(path)}


def _load(name: str) -> dict:
    return json.loads((RAW_DIR / SITE / name).read_text(encoding="utf-8"))


def _locate(*texts: str | None) -> tuple[str | None, str | None]:
    """Most specific text first: the first text that names a known place wins."""
    for t in texts:
        if not t:
            continue
        low = t.lower()
        for key, cc in CITY_HINTS:
            # "riyadh air" is an airline, not a location
            if re.search(rf"\b{re.escape(key)}\b(?! air)", low):
                return cc
    return None, None


_SEP = r"\s*(?:,|&|and|to|-|–|or)\s*"
_DIGIT_BEDS = re.compile(rf"(\d(?:(?:{_SEP})+\d)*)\s*-?\s*(?:bed|br\b)", re.I)
_W = "|".join(WORD_NUM)
_WORD_BEDS = re.compile(rf"((?:\b(?:{_W})\b-?,?\s*(?:and\s+|or\s+|to\s+)?)+)-?\s*bed", re.I)


def _bedrooms(*texts: str | None) -> tuple[int | None, int | None]:
    for t in texts:
        if not t:
            continue
        nums = [int(d) for m in _DIGIT_BEDS.finditer(t) for d in re.findall(r"\d", m.group(1))]
        nums += [WORD_NUM[w.lower()] for m in _WORD_BEDS.finditer(t) for w in re.findall(rf"\b(?:{_W})\b", m.group(1), re.I)]
        nums = [n for n in nums if 0 < n < 10]
        if nums:
            return min(nums), max(nums)
    return None, None


def _area(details: list[str]) -> tuple[float | None, float | None, str | None]:
    for d in details:
        m = re.match(r"Area \((SQFT|SQM)\):\s*(.+)", d, re.I)
        if not m:
            continue
        vals = [float(v.replace(",", "")) for v in re.findall(r"\d[\d,]*\.?\d*", m.group(2))]
        if not vals:
            continue
        f = SQFT_TO_SQM if m.group(1).upper() == "SQFT" else 1.0
        return round(min(vals) * f, 1), round(max(vals) * f, 1), d
    return None, None, None


def _detail(details: list[str], *names: str) -> str | None:
    for d in details:
        k, _, v = d.partition(":")
        if k.strip().lower() in names:
            return v.strip()
    return None


def _handover(card: dict, rec: dict) -> str | None:
    if card.get("completion"):
        return card["completion"]
    for _, a in rec.get("faqs", []):
        m = re.search(rf"(?:handover|completion)[^.]*?({MONTHS} \d{{4}})", a)
        if m:
            return m.group(1)
    return None


def _is_generic(rec: dict) -> bool:
    # one-of-one sub-pages render the shared collection text, not their own record
    return (rec.get("fallback") or "").startswith("1of1 is a bespoke collection")


def parse(rec: dict, url: str, card: dict | None = None) -> Listing:
    card = card or {}
    details = rec.get("details", [])
    title = rec.get("title") or card.get("title") or url.rsplit("/", 1)[-1].replace("-", " ").title()
    about = rec.get("about") or rec.get("fallback") or card.get("desc")
    location_text = card.get("location") or rec.get("location")
    city, country = _locate(location_text, rec.get("location"), title, about)
    if not country and card.get("tab"):
        country = COUNTRY_ALIASES.get(card["tab"].lower(), card["tab"])
    district = None
    if location_text and city:
        first = re.split(r"[,|]", location_text)[0].strip()
        if first.lower() not in (city.lower(), "trump plaza") and first.lower() not in COUNTRY_ALIASES:
            district = first.title() if first.isupper() or first.islower() else first
    unit_text = _detail(details, "unit type", "units", "bedrooms") or card.get("bedrooms")
    ptype = _detail(details, "property type") or card.get("type")
    bmin, bmax = _bedrooms(unit_text, ptype, about)
    amin, amax, atext = _area(details)
    status = _detail(details, "status") or (card.get("completion") == "Completed" and "Completed") or None
    return Listing(
        id=f"darglobal-{url.rsplit('/', 1)[-1].lower()}",
        source=SITE,
        title=title,
        property_type=ptype,
        listing_type="off-plan / project",
        city=city,
        district=district,
        country=country,
        price=None,
        currency=None,
        price_text="Price on request (DarGlobal does not publish prices online)",
        bedrooms_min=bmin,
        bedrooms_max=bmax,
        area_sqm_min=amin,
        area_sqm_max=amax,
        area_text=atext,
        amenities=rec.get("amenities", []),
        description=about,
        status=status,
        handover_date=_handover(card, rec),
        developer="DarGlobal",
        url=url,
        extra={
            "headline": rec.get("aboutTitle"),
            "details": details,
            "nearby": rec.get("nearby"),
            "location_description": rec.get("locationDesc"),
            "faqs": rec.get("faqs", []),
            "unit_mix": unit_text,
        },
    )


def scrape() -> list[Listing]:
    projects = _load("_projects.json")["projects"]
    cards = {c["url"]: c for c in _load("_index_and_pages.json")["cards"] if c.get("url")}
    out: list[Listing] = []
    for path, rec in projects.items():
        card = cards.get(path, {})
        if _is_generic(rec):
            if not card:
                continue
            rec = {"location": card.get("location")}
        if path in NOT_PROJECTS or (path == "/exclusive-plots" and not rec.get("about")):
            continue  # financing partner page / stub landing page
        out.append(parse(rec, BASE + path, card))
    print(f"[darglobal] parsed {len(out)} projects")
    return out


def scrape_pages() -> list[Page]:
    data = _load("_index_and_pages.json")
    pages: list[Page] = []
    for path, texts in data["pages"].items():
        pages.append(Page(id=f"darglobal-page{path.replace('/', '-')}", source=SITE, url=BASE + path,
                          title=f"DarGlobal {path.strip('/').replace('-', ' ').title()}",
                          text="\n".join(texts)))
    # project index: one compact page listing every project and its country
    lines = []
    for c in data["cards"]:
        bits = [c["title"], c.get("location") or c["tab"]]
        bits += [c[k] for k in ("type", "bedrooms", "completion") if c.get(k)]
        lines.append(f"{c['tab']}: " + " | ".join(bits))
    pages.append(Page(id="darglobal-page-projects", source=SITE, url=BASE + "/projects",
                      title="DarGlobal projects by country", text="\n".join(lines)))
    projects = _load("_projects.json")["projects"]
    for path in NOT_PROJECTS:
        rec = projects.get(path) or {}
        text = rec.get("about") or rec.get("fallback")
        if text:
            pages.append(Page(id=f"darglobal-page{path.replace('/', '-')}", source=SITE, url=BASE + path,
                              title=f"DarGlobal {path.strip('/').replace('-', ' ').title()}", text=text))
    # project FAQs are rich company/project knowledge; index them as pages too
    for path, rec in projects.items():
        faqs = rec.get("faqs") or []
        if faqs:
            name = rec.get("title") or path.strip("/")
            text = "\n".join(f"Q: {q}\nA: {a}" for q, a in faqs)
            pages.append(Page(id=f"darglobal-faq{path.replace('/', '-')}", source=SITE, url=BASE + path,
                              title=f"{name} - FAQ", text=text))
    return pages
