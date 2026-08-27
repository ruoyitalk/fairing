FROM python:3.12-slim@sha256:7a8b475003c4fe15a2cd4e55e5cfc2f3560bdc9333d624f24cdd6d4340fd7a17

ARG SOURCE_REVISION="unknown"
ARG SOURCE_URL="https://github.com/ruoyitalk/fairing"

WORKDIR /app

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock .
RUN pip install \
        "https://pypi.tuna.tsinghua.edu.cn/packages/e5/35/0c52d708144c2deb595cd22819a609f78fdd699b95ff6f0ebcd456e3c7c1/torch-2.6.0-cp312-cp312-manylinux1_x86_64.whl#sha256=2bb8987f3bb1ef2675897034402373ddfc8f5ef0e156e2d8cfc47cacafdda4a9" \
        --index-url "https://pypi.tuna.tsinghua.edu.cn/simple/" \
    && pip install -r requirements.lock \
        --index-url "https://pypi.tuna.tsinghua.edu.cn/simple/"

LABEL org.opencontainers.image.source="$SOURCE_URL" \
      org.opencontainers.image.revision="$SOURCE_REVISION"

COPY fairing/ ./fairing/
COPY config/ ./config/
COPY main.py streamlit_app.py ./

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "streamlit_app.py"]
