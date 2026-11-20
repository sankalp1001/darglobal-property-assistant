"""Sanity report: record count per source and null % per field.

    python -m scrapers.report
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from scrapers.common import PROCESSED_DIR


def is_null(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def main(path=PROCESSED_DIR / "listings.jsonl"):
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    by_src = defaultdict(list)
    for r in rows:
        by_src[r["source"]].append(r)
    print(f"{path.name}: {len(rows)} records")
    for src, rs in by_src.items():
        print(f"\n== {src}: {len(rs)} records")
        print("   cities:", dict(Counter(r.get("city") for r in rs).most_common(8)))
        print("   types: ", dict(Counter(r.get("property_type_norm") or r.get("property_type") for r in rs).most_common(8)))
        fields = [k for k in rs[0] if k not in ("extra", "embed_text")]
        for k in fields:
            pct = 100 * sum(is_null(r.get(k)) for r in rs) / len(rs)
            bar = "#" * int(pct / 5)
            print(f"   {k:18} {pct:5.1f}% null {bar}")
    pages = PROCESSED_DIR / "pages.jsonl"
    if pages.exists():
        n = sum(1 for _ in open(pages, encoding="utf-8"))
        print(f"\npages.jsonl: {n} chunks")


if __name__ == "__main__":
    main()
