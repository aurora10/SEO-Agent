"""Self-scheduling daemon (replaces cron in the container; runs as PID 1).

Fires the pipeline jobs on a cron-like schedule, logs every run to data/jobs.log,
and sends you actionable emails:
  - on FAILURE: the log tail so you know what went wrong,
  - on SUCCESS (for reporting jobs): what changed + exactly what to do next.

Schedule semantics use cron values:  minute hour dow dom
  - dow: "*" = any weekday, "1" = Monday (cron 0=Sun..6=Sat; 1=Mon),
  - dom: "*" = any day,    "1" = the 1st of the month.
Times are container-local (the container runs on a UTC+2 clock).

Cadence — cheap by design, so the paid jobs moved off the weekly path:
  collect_gsc      daily 06:05              (free GSC API)
  gsc_monitor      weekly, Monday 06:30     (free GSC API)
  fetch_market     monthly, 1st of month 06:45   (spends DataForSEO money)
  analyze_market   monthly, 1st of month 07:00
  submit_sitemap   monthly, 1st of month 07:30
  generate_content ON DEMAND only (spends OpenAI money) — see ON_DEMAND below.
Net effect: the old weekly batch of five jobs collapses to roughly one automated
run per week (collect_gsc daily + gsc_monitor weekly); everything paid is monthly
or explicitly on demand.

Print the schedule without starting the daemon:  python src/scheduler.py --list
"""
import datetime as dt
import os
import subprocess
import sys
import time

import yaml

import emailer

# App directory: /app inside the container (unchanged), overridable for dev machines
# so the helpers below can be exercised without the container filesystem.
APP = os.environ.get("APP_DIR", "/app")

# Each job: minute, hour, dow, dom, name, argv, timeout, report(True=email a summary on success), action(what you should do)
SCHEDULE = [
    ("5",  "6", "*", "*", "collect_gsc",     ["python", "src/collect_gsc.py",     "--config", "/app/config.yaml"], 3600, False,
     "Nothing to do — GSC data was collected automatically."),
    ("30", "6", "1", "*", "gsc_monitor",     ["python", "src/gsc_monitor.py",     "--config", "/app/config.yaml", "--repo", "/app/repo"], 1800, True,
     "If a page is NOT indexed after 2-3 weeks, request indexing for just that URL "
     "in GSC. Everything else needs no action — the sitemap is resubmitted for you."),
    ("45", "6", "*", "1", "fetch_market",    ["python", "src/fetch_market.py",    "--config", "/app/config.yaml"], 1800, False,
     "Nothing to do — keyword volumes/SERPs cached for analysis."),
    ("0",  "7", "*", "1", "analyze_market",  ["python", "src/analyze_market.py",  "--config", "/app/config.yaml", "--repo", "/app/repo"], 900, True,
     "Open reports/market-analysis.md (top clusters/opportunities). To turn them into "
     "content now: python src/generate_content.py --config config.yaml --repo /app/repo  "
     "(generate_content is no longer scheduled — it spends OpenAI money, so it runs on "
     "demand only). The content drafts arrive in a separate email with the "
     "exact merge steps (manual commit/push, or publish_drafts -> GitHub PR)."),
    ("30", "7", "*", "1", "submit_sitemap",  ["python", "src/submit_sitemap.py",  "--config", "/app/config.yaml"], 300, True,
     "Nothing to do: this is the automated replacement for clicking 'Resubmit "
     "sitemap' in GSC (Request Indexing has no API). Read the email only if it "
     "reports an error or Google has not downloaded the sitemap recently."),
]

# Jobs that are NEVER fired by the daemon loop — only run explicitly through
# run_job.py (they cost money or need a human decision). Same metadata as a
# SCHEDULE entry, minus the time fields:  name -> (argv, timeout, report, action)
ON_DEMAND = {
    "generate_content": (["python", "src/generate_content.py", "--config", "/app/config.yaml", "--repo", "/app/repo"], 1800, False,
     "It emails the drafts + next steps (review the JSON, merge into src/messages/nl.json "
     "and fr.json, then git add/commit/push — or run publish_drafts.py to open a PR)."),
    "publish_drafts": (["python", "src/publish_drafts.py", "--config", "/app/config.yaml", "--repo", "/app/repo"], 900, True,
     "The only write action in the system: it merged the approved drafts into a branch and "
     "opened a GitHub PR. Review the PR diff and merge it (or close it). Failure emails "
     "carry the log tail."),
}

# Fallback for a job name that is in neither table (manual ad-hoc runs).
DEFAULT_TIMEOUT, DEFAULT_REPORT, DEFAULT_ACTION = 3600, False, ""

DOW_NAMES = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}


def cron_match(now: dt.datetime, minute: str, hour: str, dow: str, dom: str = "*") -> bool:
    """True when `now` matches the cron fields. "*" always matches; an exact value must be equal."""
    if minute != "*" and now.minute != int(minute):
        return False
    if hour != "*" and now.hour != int(hour):
        return False
    if dow != "*":
        target = 7 if dow == "0" else int(dow)  # cron 0=Sun..6=Sat, isoweekday Mon=1..Sun=7
        if now.isoweekday() != target:
            return False
    if dom != "*" and now.day != int(dom):  # day of month, e.g. "1" = 1st only
        return False
    return True


def entry_matches(now: dt.datetime, entry: tuple) -> bool:
    """cron_match() for a full 9-field SCHEDULE entry."""
    minute, hour, dow, dom = entry[0], entry[1], entry[2], entry[3]
    return cron_match(now, minute, hour, dow, dom)


def next_run(entry: tuple, now: dt.datetime) -> dt.datetime | None:
    """First datetime strictly after `now` that fires this entry (scans up to ~13 months).

    Entries in SCHEDULE use explicit numeric minute/hour; a "*" minute/hour is
    treated as 0 so the helper stays total.
    """
    minute = 0 if entry[0] == "*" else int(entry[0])
    hour = 0 if entry[1] == "*" else int(entry[1])
    day = now.date()
    for _ in range(400):
        candidate = dt.datetime.combine(day, dt.time(hour, minute))
        if candidate > now and entry_matches(candidate, entry):
            return candidate
        day += dt.timedelta(days=1)
    return None


def describe_entry(entry: tuple) -> str:
    """Human-readable cadence of a SCHEDULE entry: daily / weekly Mon / monthly day 1."""
    minute, hour, dow, dom = entry[0], entry[1], entry[2], entry[3]
    when = f"{int(hour):02d}:{int(minute):02d}"
    if dom != "*":
        return f"monthly, day {int(dom)} of month at {when}"
    if dow != "*":
        target = 0 if dow == "0" else int(dow)
        return f"weekly, {DOW_NAMES[target]} at {when}"
    return f"daily at {when}"


def lookup_job(name: str) -> tuple:
    """(timeout, report, action) for a job name: SCHEDULE first, then ON_DEMAND.

    Unknown names keep the historical defaults (3600, False, "") — an ad-hoc
    `run_job.py` call for a name that is in neither table still logs the run and
    still emails on failure, it just has no canned action text.
    """
    for _m, _h, _d, _dom, sname, _cmd, timeout, report, action in SCHEDULE:
        if sname == name:
            return timeout, report, action
    if name in ON_DEMAND:
        _cmd, timeout, report, action = ON_DEMAND[name]
        return timeout, report, action
    return DEFAULT_TIMEOUT, DEFAULT_REPORT, DEFAULT_ACTION


def schedule_lines() -> list:
    """Startup listing: scheduled rows with cadence + next run, then the on-demand rows."""
    now = dt.datetime.now()
    lines = ["[seo-agent] schedule (container-local time):"]
    for entry in SCHEDULE:
        name, timeout, report = entry[4], entry[6], entry[7]
        nxt = next_run(entry, now)
        lines.append(f"  {name:<16} {describe_entry(entry):<32} "
                     f"next {nxt.strftime('%Y-%m-%d %H:%M') if nxt else 'n/a':<17} "
                     f"timeout {timeout}s  report={'yes' if report else 'no'}")
    lines.append("[seo-agent] on demand (never fired by the daemon; run via run_job.py):")
    for name, (_cmd, timeout, report, _action) in ON_DEMAND.items():
        lines.append(f"  {name:<16} {'on demand':<32} {'':<17} "
                     f"timeout {timeout}s  report={'yes' if report else 'no'}")
    return lines


def print_schedule() -> list:
    lines = schedule_lines()
    for line in lines:
        print(line)
    return lines


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


def run_one(cfg: dict, name: str, cmd: list, timeout: int, report: bool, action: str) -> int:
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

    return 0 if ok else 1


def main() -> None:
    if "--list" in sys.argv:  # dry path: print the schedule and exit (works off-container)
        print_schedule()
        return
    if os.path.isdir(APP):  # container: /app always exists, so this stays a chdir
        os.chdir(APP)
    cfg = yaml.safe_load(open(os.path.join(APP, "config.yaml")))
    print("[seo-agent] scheduler started (self-scheduling daemon, PID 1)")
    print_schedule()
    while True:
        now = dt.datetime.now()
        stamp = (now.year, now.month, now.day, now.hour, now.minute)
        # Scheduled jobs only: ON_DEMAND is never fired from here (run_job.py only).
        for entry in SCHEDULE:
            minute, hour, dow, dom, name, cmd, timeout, report, action = entry
            if cron_match(now, minute, hour, dow, dom) and not already_ran(name, stamp):
                print(f"[scheduler] firing {name}")
                mark_ran(name, stamp)
                run_one(cfg, name, cmd, timeout, report, action)
        time.sleep(20)


if __name__ == "__main__":
    main()
