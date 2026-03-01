FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install nexus-core from the parent repo (editable so we stay in sync)
COPY ../../pyproject.toml /nexus-core/pyproject.toml
COPY ../../nexus /nexus-core/nexus
COPY ../../README.md /nexus-core/README.md
RUN pip install --no-cache-dir "/nexus-core[telegram-bot]"

# Install the telegram-bot package (console scripts: nexus-bot, etc.)
COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
RUN pip install --no-cache-dir /app

COPY config /app/config
COPY .env.example /app/.env.example

RUN mkdir -p /app/data /app/logs

CMD ["nexus-bot"]
