"""Run the eval questions through the RAG pipeline and write a markdown report to eyeball.

    OPENROUTER_API_KEY=... python -m eval.run_eval
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from app.rag import answer

HERE = Path(__file__).parent


def main():
    cases = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
    out = ["# Eval results\n"]
    for c in cases:
        history = []
        for turn in c["turns"]:
            t0 = time.time()
            r = answer(turn, history)
            dt = time.time() - t0
            out.append(f"## [{c['category']}] {turn}\n")
            out.append(f"*plan:* `{json.dumps(r['plan'], ensure_ascii=False)}` (parsed={r['plan_ok']}, model={r['model']}, {dt:.1f}s)\n")
            if r["note"]:
                out.append(f"*note:* {r['note']}\n")
            out.append(r["answer"] + "\n")
            out.append("*sources:* " + "; ".join(f"{s['title']} ({s['source']})" for s in r["sources"][:6]) + "\n")
            out.append(f"*expectation:* {c['expect']}\n\n---\n")
            history += [{"role": "user", "content": turn}, {"role": "assistant", "content": r["answer"]}]
            print(f"[{c['category']}] {turn[:60]}  ({dt:.1f}s, {len(r['sources'])} sources, model={r['model']})", flush=True)
            time.sleep(float(os.getenv("EVAL_SLEEP", "1")))  # raise for free models (per-minute quota)
    path = HERE / "results.md"
    path.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
