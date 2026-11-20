FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTEMBED_CACHE=/home/user/.cache/fastembed \
    HOME=/home/user

# Hugging Face Spaces runs containers as UID 1000
RUN useradd -m -u 1000 user
WORKDIR /home/user/app

COPY --chown=user requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

USER user
# Bake the embedding model into the image so cold starts don't download it
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/home/user/.cache/fastembed')"

# Scraping happens offline; only the processed snapshot ships in the image
COPY --chown=user app/ app/
COPY --chown=user data/processed/listings.jsonl data/processed/pages.jsonl data/processed/meta.json data/processed/vectors_*.npy data/processed/

EXPOSE 7860
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
