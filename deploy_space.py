"""Create/update the Hugging Face Space and upload the app.

    hf auth login            # once, with a token that has write access
    python deploy_space.py   # prints the public URL

Reads OPENROUTER_API_KEY from the environment or .env and stores it as a Space secret
(it is never uploaded as a file).
"""
from __future__ import annotations

import os
import sys

from huggingface_hub import HfApi

import app.llm  # noqa: F401  (loads .env)

NAME = os.getenv("SPACE_NAME", "darglobal-property-assistant")
FILES = ["Dockerfile", ".dockerignore", "requirements.txt", "README.md",
         "app/*.py", "data/processed/listings.jsonl", "data/processed/pages.jsonl",
         "data/processed/meta.json", "data/processed/vectors_*.npy"]


def main() -> None:
    api = HfApi()
    user = api.whoami()["name"]
    repo = f"{user}/{NAME}"
    api.create_repo(repo, repo_type="space", space_sdk="docker", exist_ok=True)
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY not set")
    api.add_space_secret(repo, "OPENROUTER_API_KEY", key)
    api.upload_folder(repo_id=repo, repo_type="space", folder_path=".", allow_patterns=FILES,
                      commit_message="Deploy property assistant")
    print(f"Space:    https://huggingface.co/spaces/{repo}")
    print(f"App URL:  https://{repo.replace('/', '-').replace('_', '-').lower()}.hf.space")


if __name__ == "__main__":
    main()
