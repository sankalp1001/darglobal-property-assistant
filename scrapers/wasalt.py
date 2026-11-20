"""Wasalt (wasalt.sa) scraper.

Wasalt is a Next.js app that server-renders every page with the full page data
embedded in <script id="__NEXT_DATA__">, so plain httpx + json.loads is enough.

* get_urls(): category search pages (e.g. /en/apartments-for-sale-in-riyadh)
  each embed 32 listings; we collect listing URLs from a few categories in
  3 cities. (The site ignores ?page=N in SSR, so we vary category instead.)
* fetch(url): disk-cached GET with polite delay and retries.
* parse(raw, url): property detail page -> Listing.

robots.txt allows everything except /search, which we never touch.
Agent/owner names and phone details in the payload are deliberately not kept.
"""
from __future__ import annotations

import json
import re
import sys

from scrapers.common import fetch_cached
from scrapers.schema import Listing

SITE = "wasalt"
BASE = "https://wasalt.sa/en"
CITIES = ["riyadh", "jeddah", "dammam"]
CATEGORIES = ["apartments-for-sale", "villas-for-sale", "properties-for-rent"]
NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
PHONE_RE = re.compile(r"(?:\+?966|00966|\b0)5\d[\d\s-]{7,10}\d|\b9200\d{5}\b")


def next_data(raw: str) -> dict:
    m = NEXT_RE.search(raw)
    if not m:
        raise ValueError("no __NEXT_DATA__ in page")
    return json.loads(m.group(1))["props"]["pageProps"]


def fetch(url: str) -> str:
    return fetch_cached(SITE, url, "html")


def get_urls(limit_per_page: int = 32) -> list[str]:
    urls: list[str] = []
    for city in CITIES:
        for cat in CATEGORIES:
            srp = f"{BASE}/{cat}-in-{city}"
            try:
                pp = next_data(fetch(srp))
            except Exception as e:  # noqa: BLE001
                print(f"  ! {srp}: {e}", file=sys.stderr)
                continue
            props = (pp.get("searchResult") or {}).get("properties") or []
            # some category pages group units in nested lists; flatten them
            props = [x for p in props for x in (p if isinstance(p, list) else [p]) if isinstance(x, dict)]
            for p in props[:limit_per_page]:
                pf = (p.get("propertyInfo") or {}).get("propertyFor") or "sale"
                slug = (p.get("propertyInfo") or {}).get("slug") or p.get("slug")
                if slug:
                    urls.append(f"{BASE}/property/{pf}/{slug}")
            print(f"  {srp}: {len(props)} listings")
    return list(dict.fromkeys(urls))


def _clean(html: str | None) -> str | None:
    if not html:
        return None
    t = TAG_RE.sub(" ", html).replace("&nbsp;", " ").replace("&amp;", "&")
    t = PHONE_RE.sub("[phone removed]", t)  # don't store agents' personal numbers
    return re.sub(r"\s+", " ", t).strip() or None


def _int(v) -> int | None:
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return None


def parse(raw: str, url: str) -> Listing:
    pd = next_data(raw)["propertyDetailsV3"]
    info = pd.get("propertyInfo") or {}
    attrs = {a.get("key"): a for a in pd.get("attributes") or []}
    add = {a.get("key"): a.get("value") for a in pd.get("additionalAttributes") or []}
    details = next_data(raw).get("infoDetails") or {}

    listing_for = info.get("propertyFor")
    freq = None
    if listing_for == "rent":
        price = info.get("expectedRent") or info.get("conversionPrice")
        rf = info.get("rentFreq")
        if isinstance(rf, dict) and rf:
            chosen = next((v for v in rf.values() if v.get("default_freq")), next(iter(rf.values())))
            price, freq = chosen.get("amount") or price, chosen.get("freq")
        elif isinstance(rf, str) and rf:
            freq = rf
        freq = freq or "yearly"
        price_text = f"{info.get('currencyType', 'SAR')} {price} / {freq}" if price else None
    else:
        price = info.get("salePrice") or info.get("conversionPrice")
        price_text = f"{info.get('currencyType', 'SAR')} {price}" if price else None

    area = None
    area_key = None
    for k in ("builtUpArea", "carpetArea", "plotArea"):
        if k in attrs and attrs[k].get("value") not in (None, ""):
            area, area_key = attrs[k]["value"], attrs[k].get("name")
            break

    amenities = [a.get("name") or a.get("value") for a in details.get("amenities") or []]
    amenities = [a for a in amenities if isinstance(a, str)]

    beds = _int((attrs.get("noOfBedrooms") or {}).get("value"))
    return Listing(
        id=f"wasalt-{pd.get('id')}",
        source=SITE,
        title=info.get("title") or info.get("propertyName") or "Untitled",
        property_type=info.get("propertySubType"),
        listing_type=listing_for,
        city=info.get("city"),
        district=info.get("district") or info.get("zone"),
        country=info.get("country") or "Saudi Arabia",
        price=_int(price),
        currency=info.get("currencyType") or "SAR",
        price_text=price_text,
        bedrooms_min=beds,
        bedrooms_max=beds,
        bathrooms=_int((attrs.get("noOfBathrooms") or {}).get("value")),
        area_sqm_min=_int(area),
        area_sqm_max=_int(area),
        area_text=f"{area_key}: {area} sqm" if area else None,
        amenities=amenities,
        description=_clean(info.get("description")),
        status="Listed",
        handover_date=None,
        developer=None,
        url=url,
        extra={
            "age": add.get("completionYear"),
            "furnishing": info.get("furnishingType"),
            "rent_frequency": freq,
            "territory": info.get("territory"),
            "published_at": pd.get("publishedAt"),
        },
    )


def scrape(max_listings: int = 300) -> list[Listing]:
    print("[wasalt] collecting listing URLs from category pages")
    urls = get_urls()[:max_listings]
    print(f"[wasalt] {len(urls)} listing URLs; fetching detail pages")
    out: list[Listing] = []
    for i, u in enumerate(urls, 1):
        try:
            out.append(parse(fetch(u), u))
        except Exception as e:  # noqa: BLE001
            print(f"  ! {u}: {e}", file=sys.stderr)
        if i % 25 == 0:
            print(f"  {i}/{len(urls)}")
    return out
