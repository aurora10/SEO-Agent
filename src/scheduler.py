"""Self-scheduling daemon (replaces cron in the container; runs as PID 1).

Fires the pipeline jobs on a cron-like schedule, logs every run to data/jobs.log,
and sends you actionable emails:
  - on FAILURE: the log tail so you know what went wrong,
  - on SUCCESS (for reporting jobs): what changed + exactly what to do next.

Schedule semantics use cron values:  minute hour dow  (dow: 0=Sun..6=Sat; 1=Mon).
"""
import datetime as dt
import os
import subprocess
import time

import yaml

import emailer

APP = "/app"

# Each job: minute, hour, dow, name, argv, timeout, report(True=email a summary on success), action(what you should do)
SCHEDULE = [
    ("5",  "6", "*", "collect_gsc",      ["python", "src/collect_gsc.py",     "--config", "/app/config.yaml"], 3600, False,
     "Nothing to do — GSC data was collected automatically."),
    ("30", "6", "1", "fetch_market",     ["python", "src/fetch_market.py",    "--config", "/app/config.yaml"], 1800, False,
     "Nothing to do — keyword volumes/SERPs cached for analysis."),
    ("45", "6", "1", "analyze_market",   ["python", "src/analyze_market.py",  "--config", "/app/config.yaml"], 900, True,
     "Open reports/market-analysis.md (top clusters/opportunities). To turn them into "
     "content now: python src/generate_content.py --config config.yaml --repo /app/repo  "
     "(or wait for the Monday cron). The content drafts arrive in a separate email with the "
     "exact merge steps (manual commit/push, or publish_drafts -> GitHub PR)."),
    ("0",  "7", "1", "generate_content", ["python", "src/generate_content.py", "--config", "/app/config.yaml", "--repo", "/app/repo"], 1800, False,
     "It emails the drafts + next steps (review the JSON, merge into src/messages/nl.json "
     "and fr.json, then git add/commit/push — or run publish_drafts.py to open a PR)."),
    ("15", "7", "1", "gsc_monitor",      ["python", "src/gsc_monitor.py",     "--config", "/app/config.yaml", "--repo", "/app/repo"], 1800, True,
     "If any page above is NOT indexed: request indexing in GSC (or fix the crawl issue). "
     "If all indexed, nothing to do."),
]


def cron_match(now: dt.datetime, minute: str, hour: str, dow: str) -> bool:
    if minute != "*" and now.minute != int(minute):
        return False
    if hour != "*" and now.hour != int(hour):
        return False
    if dow != "*":
        target = 7 if dow == "0" else int(dow)  # cron 0=Sun..6=Sat, isoweekday Mon=1..Sun=7
        if now.isoweekday() != target:
            return False
    return True


def marker_path(name: str) -> str:
    return os.path.join(APP, "data", f".last_{name}")


def already_ran(name: str, stamp: tuple) -> bool:
    p = marker_path(name)
    if os.path.exists(p):
        with open(p) as f:
            return f.read().strip() == "_".join(map(str, stamp))
    return False


def mark_ran(name: str, stamp: tuple) -> None:
    os.makedirs(os.path.join(APP, "data"), exist_ok=True)
    with open(marker_path(name), "w") as f:
        f.write("_".join(map(str, stamp)))


def _log(ts: str, name: str, status: str, out: str) -> None:
    os.makedirs(os.path.join(APP, "data"), exist_ok=True)
    with open(os.path.join(APP, "data/jobs.log"), "a") as f:
        f.write(f"[{ts}] {name} -> {status}\n")
        if out.strip():
            f.write(out[-4000:].rstrip() + "\n")
        f.write("-" * 60 + "\n")


def run_one(cfg: dict, name: str, cmd: list, timeout: int, report: bool, action: str) -> None:
    ts = dt.datetime.now().isoformat(timespec="seconds")
    exit_code, out, ok = None, "", True
    try:
        proc = subprocess.run(cmd, cwd=APP, capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "") + (proc.stderr or "")
        exit_code, ok = proc.returncode, proc.returncode == 0
    except subprocess.TimeoutExpired:
        out, exit_code, ok = f"TIMEOUT after {timeout}s", "timeout", False

    status = "OK" if ok else "FAIL"
    _log(ts, name, status, out)
    print(f"[{ts}] {name} -> {status}")

    try:
        if not ok:
            body = (f"SEO AGENT JOB FAILED\nJob: {name}\nTime: {ts}\nExit: {exit_code}\n\n"
                    f"Log tail:\n{out[-4000:]}")
            emailer.send(cfg, f"[SEO] FAIL: {name}", body)
        elif report:
            # Actionable summary: what happened (output tail) + exactly what to do.
            body = (f"SEO AGENT — {name} run summary\nTime: {ts}\nStatus: OK\n\n"
                    f"WHAT HAPPENED (last output):\n{out[-1500:].rstrip()}\n\n"
                    f"WHAT YOU SHOULD DO:\n{action}")
            emailer.send(cfg, f"[SEO] {name}: done", body)
    except Exception as e:  # noqa: BLE001
        print("  -> notification email could not be sent:", type(e).__name__, e)


def main() -> None:
    os.chdir(APP)
    cfg = yaml.safe_load(open(os.path.join(APP, "config.yaml")))
    print("[seo-agent] scheduler started (self-scheduling daemon, PID 1)")
    while True:
        now = dt.datetime.now()
        stamp = (now.year, now.month, now.day, now.hour, now.minute)
        for minute, hour, dow, name, cmd, timeout, report, action in SCHEDULE:
            if cron_match(now, minute, hour, dow) and not already_ran(name, stamp):
                print(f"[scheduler] firing {name}")
                mark_ran(name, stamp)
                run_one(cfg, name, cmd, timeout, report, action)
        time.sleep(20)


if __name__ == "__main__":
    main()
