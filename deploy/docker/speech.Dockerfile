FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CUDA_VISIBLE_DEVICES="" OMP_NUM_THREADS=6 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HOME=/results/cache HOME=/results
RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 libgomp1 ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Explicit CPU wheels keep CUDA libraries out of this isolated runtime.
RUN pip install --no-cache-dir torch==2.11.0+cpu torchaudio==2.11.0+cpu --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir voxcpm==2.0.3 'transformers>=4.57,<5' 'fastapi>=0.115,<1' 'uvicorn>=0.30,<1' \
    && pip check && pip freeze > /app/runtime-packages.txt
COPY backend/speech_runtime.py backend/speech_worker.py backend/speech_contracts.py backend/speech_assets.py /app/backend/
RUN useradd --uid 1000 --create-home speech && mkdir -p /results && chown speech:speech /results
USER 1000:1000
EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/health', timeout=3)"
CMD ["uvicorn", "backend.speech_worker:create_app", "--factory", "--host", "0.0.0.0", "--port", "8091", "--workers", "1"]
