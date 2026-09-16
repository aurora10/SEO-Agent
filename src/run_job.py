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
    timeout, report, action = 3600, False, ""
    for _m, _h, _d, sname, _c, s_timeout, s_report, s_action in scheduler.SCHEDULE:
        if sname == name:
            timeout, report, action = s_timeout, s_report, s_action
            break

    return scheduler.run_one(cfg, name, cmd, timeout, report, action)


if __name__ == "__main__":
    sys.exit(main())
