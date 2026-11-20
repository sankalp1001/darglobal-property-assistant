"""Embeddings (fastembed, ONNX) + in-process Qdrant index built from the JSONL at startup."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from fastembed import TextEmbedding
from qdrant_client import QdrantClient, models

DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data" / "processed"))
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "  # bge retrieval instruction
LISTINGS, PAGES = "listings", "pages"


@lru_cache(maxsize=1)
def embedder() -> TextEmbedding:
    return TextEmbedding(EMBED_MODEL, cache_dir=os.getenv("FASTEMBED_CACHE", None))


def embed_docs(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in embedder().embed(texts, batch_size=64)]


def embed_query(text: str) -> list[float]:
    return next(iter(embedder().embed([QUERY_PREFIX + text]))).tolist()


def _read(name: str) -> list[dict]:
    p = DATA_DIR / name
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _vectors(name: str, rows: list[dict]):
    """Document vectors precomputed by `python -m app.index` (vectors_<name>.npy). Embedding
    every document at startup took ~2.4 GB of RAM (ONNX batch buffers), so the server only
    embeds queries. Falls back to embedding at startup if the file is missing or stale."""
    import numpy as np

    f = DATA_DIR / f"vectors_{name}.npy"
    if f.exists():
        v = np.load(f)
        if len(v) == len(rows):
            return v.tolist()
    return embed_docs([r["embed_text"] for r in rows])


def build_vectors() -> None:
    import numpy as np

    for name, file in ((LISTINGS, "listings.jsonl"), (PAGES, "pages.jsonl")):
        rows = _read(file)
        v = np.array(embed_docs([r["embed_text"] for r in rows]), dtype=np.float32)
        np.save(DATA_DIR / f"vectors_{name}.npy", v)
        print(f"{name}: {v.shape}")


class Index:
    def __init__(self):
        self.client = QdrantClient(":memory:")
        self.listings = _read("listings.jsonl")
        self.pages = _read("pages.jsonl")
        meta_path = DATA_DIR / "meta.json"
        self.meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        dim = len(embed_query("probe"))
        for name, rows in ((LISTINGS, self.listings), (PAGES, self.pages)):
            self.client.create_collection(name, vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE))
            vecs = _vectors(name, rows)
            self.client.upload_points(name, [
                models.PointStruct(id=i, vector=v, payload={k: val for k, val in r.items() if k != "embed_text"})
                for i, (r, v) in enumerate(zip(rows, vecs))
            ])

    def search(self, collection: str, query: str, flt: models.Filter | None = None, limit: int = 8) -> list[dict]:
        res = self.client.query_points(collection, query=embed_query(query), query_filter=flt,
                                       limit=limit, with_payload=True)
        return [{**p.payload, "_score": p.score} for p in res.points]

    def count(self) -> dict:
        return {LISTINGS: len(self.listings), PAGES: len(self.pages)}


@lru_cache(maxsize=1)
def get_index() -> Index:
    return Index()


if __name__ == "__main__":
    build_vectors()
