"""Run a pipeline job, log it centrally, and email you on failure.

Each cron job is wrapped in this so:
  - every run is recorded (timestamped) in the central logs (data/jobs.log),
  - if a job exits non-zero, you get an email with the log tail so you know
    WHAT failed and WHY.

Usage (from cron):
    python src/run_job.py <job_name> <command...>
"""
import datetime as dt
import os
import subprocess
import sys

import yaml

import emailer


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: run_job.py <job_name> <command...>")
        return 2
    job = sys.argv[1]
    cmd = sys.argv[2:]

    cfg = yaml.safe_load(open("config.yaml"))
    ts = dt.datetime.now().isoformat(timespec="seconds")

    # Run the job, capturing its output.
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    status = "OK" if proc.returncode == 0 else "FAIL"

    # Central, persistent, timestamped log of every run.
    os.makedirs("data", exist_ok=True)
    with open("data/jobs.log", "a") as f:
        f.write(f"[{ts}] {job} -> {status} (exit {proc.returncode})\n")
        if out.strip():
            f.write(out[-4000:].rstrip() + "\n")
        f.write("-" * 60 + "\n")

    print(f"[{ts}] {job} -> {status} (exit {proc.returncode})")

    # Alerter: email on failure with the log tail so you can debug.
    if proc.returncode != 0:
        body = (f"SEO AGENT JOB FAILED\nJob: {job}\nTime: {ts}\nExit: {proc.returncode}\n\n"
                f"Log tail:\n{out[-4000:]}"
                if out.strip()
                else f"SEO AGENT JOB FAILED\nJob: {job}\nTime: {ts}\nExit: {proc.returncode}\n(no output)")
        try:
            emailer.send(cfg, f"[SEO] FAIL: {job}", body)
        except Exception as e:  # noqa: BLE001
            print("  -> failure email could not be sent:", type(e).__name__, e)

    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
