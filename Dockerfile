# One container that runs everything: Ollama (Gemma), the Temporal dev server,
# the Temporal worker and the web app. Meant for a single hosted instance with
# a persistent disk mounted at /var/data (see render.yaml).

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates zstd procps \
    && rm -rf /var/lib/apt/lists/*

# Ollama and the Temporal CLI, from their official install scripts.
RUN curl -fsSL https://ollama.com/install.sh | sh
RUN curl -sSf https://temporal.download/cli.sh | sh \
    && ln -s /root/.temporalio/bin/temporal /usr/local/bin/temporal

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY deploy/start.sh ./start.sh
RUN sed -i 's/\r$//' start.sh && chmod +x start.sh

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/var/data/app \
    OLLAMA_MODELS=/var/data/ollama \
    OLLAMA_HOST=127.0.0.1:11434 \
    OLLAMA_BASE_URL=http://127.0.0.1:11434 \
    TEMPORAL_ADDRESS=127.0.0.1:7233

CMD ["./start.sh"]
