# == SEO Agent Docker image ==
# Self-contained batch pipeline (GSC collector, market analyzer, content writer,
# index monitor) that runs its own cron schedule inside the container.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Brussels

# cron for the in-container scheduler + curl for healthchecks / compat
RUN apt-get update \
    && apt-get install -y --no-install-recommends cron curl git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app code + runtime scripts
COPY src ./src
COPY config.example.yaml ./
COPY entrypoint.sh ./
COPY crontab ./crontab
COPY scripts ./scripts
RUN chmod +x entrypoint.sh scripts/*.py

ENTRYPOINT ["./entrypoint.sh"]
