"""Run both scrapers and write data/processed/{listings,pages}.jsonl.

    python -m scrapers.run            # both sites
    python -m scrapers.run wasalt     # one site
"""
from __future__ import annotations

import json
import sys

from scrapers import darglobal, wasalt
from scrapers.common import PROCESSED_DIR

SITES = {"darglobal": darglobal, "wasalt": wasalt}


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.model_dump(), ensure_ascii=False) + "\n")


def main(sites: list[str]):
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    for name in sites:
        mod = SITES[name]
        listings = mod.scrape()
        write_jsonl(PROCESSED_DIR / f"listings_{name}.jsonl", listings)
        pages = mod.scrape_pages() if hasattr(mod, "scrape_pages") else []
        write_jsonl(PROCESSED_DIR / f"pages_{name}.jsonl", pages)
        print(f"[{name}] {len(listings)} listings, {len(pages)} pages")


if __name__ == "__main__":
    main(sys.argv[1:] or list(SITES))
