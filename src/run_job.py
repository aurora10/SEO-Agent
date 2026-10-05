"""Run a pipeline job manually, with the SAME logging + notifications as the scheduler.

Use this instead of calling the agent script directly when you want the
"what changed + what to do" email (or the failure email).

Usage (from /app in the container):
    python src/run_job.py <job_name> <command...>

Example:
    docker compose exec seo-agent python src/run_job.py analyze_market \
        python src/analyze_market.py --config /app/config.yaml

On success, reporting jobs (analyze_market, gsc_monitor) email a summary with
what changed + what to do; every failure emails the log tail. All runs are
recorded in data/jobs.log.

The job name is looked up in scheduler.SCHEDULE first, then in
scheduler.ON_DEMAND (jobs that are no longer scheduled because they cost money
or need a human decision — generate_content, publish_drafts). So the paid,
on-demand jobs keep exactly the same logging and email behaviour:

    docker compose exec seo-agent python src/run_job.py generate_content \
        python src/generate_content.py --config /app/config.yaml --repo /app/repo

A name in neither table still runs, with the historical defaults
(timeout 3600, report False, no action text).
"""
import sys

import yaml

import scheduler


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: run_job.py <job_name> <command...>")
        return 2
    name = sys.argv[1]
    cmd = sys.argv[2:]

    cfg = yaml.safe_load(open("config.yaml"))
    timeout, report, action = scheduler.lookup_job(name)

    return scheduler.run_one(cfg, name, cmd, timeout, report, action)


if __name__ == "__main__":
    sys.exit(main())
