"""FastAPI API + Gradio chat UI on one port.

    uvicorn app.main:app --host 0.0.0.0 --port 7860
"""
from __future__ import annotations

import logging
import os

import gradio as gr
from fastapi import FastAPI
from pydantic import BaseModel

from app.index import get_index
from app.rag import answer

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="DarGlobal & Wasalt property assistant")
index = get_index()  # build embeddings + Qdrant at startup (a few seconds)


class ChatIn(BaseModel):
    message: str
    history: list[dict] = []


class Source(BaseModel):
    title: str | None
    url: str | None
    source: str | None
    price: str | None


class ChatOut(BaseModel):
    answer: str
    sources: list[Source]


@app.get("/health")
def health():
    return {"status": "ok", "indexed": index.count(), "scraped_on": index.meta.get("scraped_on")}


@app.post("/chat", response_model=ChatOut)
def chat(body: ChatIn):
    r = answer(body.message, body.history)
    return {"answer": r["answer"], "sources": r["sources"]}


def _ui_reply(message: str, history: list[dict]) -> str:
    r = answer(message, history)
    text = r["answer"]
    if r["sources"]:
        rows = [f"- [{s['title']}]({s['url']}) · {s['source']}" + (f" · {s['price']}" if s.get("price") else "")
                for s in r["sources"][:8]]
        text += "\n\n**Sources**\n" + "\n".join(rows)
    return text


meta = index.meta
counts = meta.get("listings", {})
header = (f"Data scraped from DarGlobal and Wasalt on {meta.get('scraped_on', 'n/a')}: "
          f"{counts.get('darglobal', 0)} DarGlobal projects, {counts.get('wasalt', 0)} Wasalt listings, "
          f"{meta.get('page_chunks', 0)} company-page chunks. Answers come only from this data.")

ui = gr.ChatInterface(
    fn=_ui_reply,
    title="Property Assistant: DarGlobal + Wasalt",
    description=header,
    examples=[
        "Who is DarGlobal and which countries do they have projects in?",
        "Show me 3-bedroom apartments for sale in Riyadh under 1.5M SAR",
        "What about villas in Jeddah instead?",
        "Which DarGlobal projects are in Oman and when is handover?",
        "Cheapest apartments for rent in Dammam",
        "What residency benefits come with buying property in Saudi Arabia?",
    ],
    fill_height=True,
)

app = gr.mount_gradio_app(app, ui, path="/")
