#!/bin/sh
set -e

echo "[seo-agent] starting — writing config.yaml + credentials from env"

python /app/scripts/build_config.py
python /app/scripts/build_credentials.py

# Refresh the constructief site checkout (input for Agent 3 + index monitor).
# Clone on first run (google-sheets = the deployed branch), pull afterwards.
REPO_PATH="${REPO_PATH:-/app/repo}"
CONSTRUCTIEF_URL="${CONSTRUCTIEF_URL:-https://github.com/aurora10/constructief.git}"
CONSTRUCTIEF_BRANCH="${CONSTRUCTIEF_BRANCH:-google-sheets}"
if [ -d "$REPO_PATH/.git" ]; then
    echo "[seo-agent] pulling constructief ($CONSTRUCTIEF_BRANCH)"
    git -C "$REPO_PATH" pull -q --ff-only || echo "[seo-agent] WARN: constructief pull failed"
else
    echo "[seo-agent] cloning constructief ($CONSTRUCTIEF_BRANCH) -> $REPO_PATH"
    git clone -q -b "$CONSTRUCTIEF_BRANCH" "$CONSTRUCTIEF_URL" "$REPO_PATH" \
        || echo "[seo-agent] WARN: constructief clone failed (Agent 3 / monitor will error)"
fi

# Install the in-container cron schedule.
# cron on Debian treats files in /etc/cron.d/ as system crontabs (user = next field).
cp /app/crontab /etc/cron.d/seo-agent
chmod 0644 /etc/cron.d/seo-agent

# Ensure the cron log dir exists for the jobs that append to it.
mkdir -p /app/data

echo "[seo-agent] starting cron in foreground (container stays up, self-schedules)"
exec cron -f
