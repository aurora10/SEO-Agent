#!/bin/sh
set -e

echo "[seo-agent] starting — writing config.yaml + credentials from env"

python /app/scripts/build_config.py
python /app/scripts/build_credentials.py

# Install the in-container cron schedule.
# cron on Debian treats files in /etc/cron.d/ as system crontabs (user = next field).
cp /app/crontab /etc/cron.d/seo-agent
chmod 0644 /etc/cron.d/seo-agent

# Ensure the cron log dir exists for the jobs that append to it.
mkdir -p /app/data

echo "[seo-agent] starting cron in foreground (container stays up, self-schedules)"
exec cron -f
