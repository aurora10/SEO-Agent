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

# Ensure the data dir exists (persistent volume).
mkdir -p /app/data

# Run the self-scheduling Python daemon as the foreground process (PID 1).
# It fires the pipeline jobs on schedule, logs to data/jobs.log, and emails on
# failure — more reliable than system cron inside a slim container.
echo "[seo-agent] starting scheduler in foreground (container stays up, self-schedules)"
exec python src/scheduler.py
