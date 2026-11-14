ARG BASE_IMAGE=python:3.11-slim-bookworm
FROM ${BASE_IMAGE}

ARG INSTALL_LOCAL_RUNTIME=0
ARG TORCH_VERSION=2.7.1
ARG VLLM_VERSION=0.27.1+cpu
ARG VLLM_TORCH_VERSION=2.13.0
ARG VLLM_TORCHVISION_VERSION=0.28.0
ARG VLLM_VIRTUAL_ENV=/opt/venvs/vllm
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venvs/api \
    PATH=/opt/venvs/api/bin:$PATH \
    HOME=/workspace \
    TZ=Asia/Shanghai \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_FIND_LINKS=http://110.42.51.234:19000/wheelhouse/index.html \
    PIP_TRUSTED_HOST=110.42.51.234 \
    PIP_RETRIES=10 \
    PIP_TIMEOUT=60

# Production only runs API, storage and SSH orchestration.
RUN sed -i "s/deb.debian.org/mirrors.aliyun.com/g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git tzdata \
    && if [ "$INSTALL_LOCAL_RUNTIME" = "1" ]; then apt-get install -y --no-install-recommends libgomp1 libnuma1 libtcmalloc-minimal4; fi \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv "$VIRTUAL_ENV"

WORKDIR /app
COPY requirements-storage.txt requirements-llamafactory-cpu.txt requirements-vllm-cpu.txt deploy/docker/torch-constraints.txt ./
RUN pip install --no-cache-dir -r requirements-storage.txt && pip check

RUN if [ "$INSTALL_LOCAL_RUNTIME" = "1" ]; then python -m venv /opt/venvs/llamafactory && /opt/venvs/llamafactory/bin/pip install --no-cache-dir --find-links "$PIP_FIND_LINKS" "torch==${TORCH_VERSION}+cpu" "torchvision==0.22.1+cpu" "torchaudio==${TORCH_VERSION}+cpu" && /opt/venvs/llamafactory/bin/pip install --no-cache-dir --constraint torch-constraints.txt -r requirements-storage.txt -r requirements-llamafactory-cpu.txt \
    && /opt/venvs/llamafactory/bin/pip check \
    && /opt/venvs/llamafactory/bin/python -c "import torch; assert not torch.cuda.is_available(); print(torch.__version__)" \
    && /opt/venvs/llamafactory/bin/llamafactory-cli version; fi

# requirements-vllm-cpu.txt pins the official +cpu wheel via a GitHub release
# URL, which is throttled from the deployment host; install by name so the
# MinIO wheelhouse (PIP_FIND_LINKS) serves it instead. Anything missing falls
# back to the configured indexes.
RUN if [ "$INSTALL_LOCAL_RUNTIME" = "1" ]; then python -m venv "$VLLM_VIRTUAL_ENV" \
    && "$VLLM_VIRTUAL_ENV/bin/pip" install --no-cache-dir --upgrade pip setuptools wheel \
    && "$VLLM_VIRTUAL_ENV/bin/pip" install --no-cache-dir \
       --find-links "$PIP_FIND_LINKS" \
       "torch==${VLLM_TORCH_VERSION}+cpu" \
       "torchvision==${VLLM_TORCHVISION_VERSION}+cpu" \
    && "$VLLM_VIRTUAL_ENV/bin/pip" install --no-cache-dir "vllm==${VLLM_VERSION}" \
    && "$VLLM_VIRTUAL_ENV/bin/pip" install --no-cache-dir --find-links "$PIP_FIND_LINKS" --no-deps --force-reinstall "torchaudio==2.11.0+cpu" "torchcodec==0.16.0+cpu" \
    && "$VLLM_VIRTUAL_ENV/bin/pip" check \
    && test -f /usr/lib/x86_64-linux-gnu/libtcmalloc_minimal.so.4 \
    && LIBIOMP_PATH="$(find "$VLLM_VIRTUAL_ENV" -type f -name 'libiomp5.so' -print -quit)" \
    && test -n "$LIBIOMP_PATH" \
    && if [ "$LIBIOMP_PATH" != "$VLLM_VIRTUAL_ENV/lib/libiomp5.so" ]; then ln -sf "$LIBIOMP_PATH" "$VLLM_VIRTUAL_ENV/lib/libiomp5.so"; fi \
    && find "$VLLM_VIRTUAL_ENV" -name 'libiomp5.so' -print \
    && LD_PRELOAD="/usr/lib/x86_64-linux-gnu/libtcmalloc_minimal.so.4:$VLLM_VIRTUAL_ENV/lib/libiomp5.so" \
       "$VLLM_VIRTUAL_ENV/bin/python" -c "import torch, vllm; assert not torch.cuda.is_available(); print(f'vLLM {vllm.__version__}; torch {torch.__version__}')"; fi

COPY backend ./backend
COPY scripts ./scripts
# SDXL runtime loads its pinned script and integrity manifest from deploy/.
COPY deploy ./deploy
COPY data ./data

RUN mkdir -p /workspace/runtime /workspace/artifacts /workspace/datasets \
    /workspace/uploads /workspace/model-cache /workspace/training-work /workspace/.modelscope \
    && chown -R 1000:1000 /workspace

EXPOSE 8010 8011
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/api/health', timeout=3).read()"

CMD ["python", "scripts/run_backend_linux.py"]
