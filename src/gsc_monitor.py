"""GSC index-status monitor.

Checks whether the priority pages are actually indexed (via the Search Console
URL Inspection API) and reports / emails you, so you don't have to check manually.
Reuses Agent 1's existing GSC credentials — no extra setup.

This is *monitoring*, not force-indexing: Google's URL Inspection API only
inspects; the GSC "Request Indexing" button has no programmatic equivalent for
ordinary pages. So the real automation is verify + report (and the fresh sitemap
lets Google discover the pages).

Usage:
    python src/gsc_monitor.py --config config.yaml [--dry-run]
    python src/gsc_monitor.py --config config.yaml --repo /path/to/constructief
"""
import argparse
import datetime as dt
import json
import os
import re
import smtplib
import sys
import time
from email.message import EmailMessage

import yaml

import gsc_client

BASE = "https://constructief-bouw.be"

# A URL unindexed for at least this long is worth a manual nudge; a freshly
# discovered one is not.
STUCK_DAYS = 14
# Practical ceiling for GSC's "Request Indexing" button (no API exists for it).
INDEX_REQUEST_QUOTA = 10


def load_state(path: str) -> dict:
    """Per-URL index history. Missing or corrupt -> empty (never crash)."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: dict) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def update_state(state: dict, rows: list, today, indexed_fn) -> dict:
    """Record status + when each URL was FIRST seen not indexed."""
    for r in rows:
        url = r["url"]
        entry = state.setdefault(url, {})
        entry["last_checked"] = today.isoformat()
        if "error" in r:
            entry["status"] = "error"
            entry["error"] = str(r["error"])[:200]
            continue
        if indexed_fn(r):
            entry["status"] = "indexed"
            entry.setdefault("indexed_since", today.isoformat())
            entry.pop("first_not_indexed", None)
            entry.pop("error", None)
        else:
            entry["status"] = "not_indexed"
            entry["coverage"] = r.get("coverage")
            # setdefault: keep the ORIGINAL first sighting across runs
            entry.setdefault("first_not_indexed", today.isoformat())
            entry.pop("indexed_since", None)
    return state
DEFAULT_REPO = "/Users/albert/Desktop/CodeWork/Constructief/constructief"


def parse_flagships(repo: str):
    trades: set[str] = set()
    cities: set[str] = set()
    cc = os.path.join(repo, "src/data/cityContent.ts")
    if os.path.exists(cc):
        m = re.search(r"flagshipTrades\s*=\s*\[([^\]]+)\]", open(cc).read())
        if m:
            trades = set(re.findall(r"'([^']+)'", m.group(1)))
    ci = os.path.join(repo, "src/data/cities.ts")
    if os.path.exists(ci):
        m = re.search(r"flagshipCitySlugs\s*=\s*\[([^\]]+)\]", open(ci).read())
        if m:
            cities = set(re.findall(r"'([^']+)'", m.group(1)))
    return trades, cities


def priority_urls(repo: str) -> list[str]:
    """The pages we care most about:
    - werkgevers (commercial flagship)
    - base trade pages: /diensten/onderaannemer-{trade}
    - city trade+city pages: /diensten/onderaannemer-{trade}-{city} — the pages
      that actually rank for city+trade queries.
    All three locales are checked. ru used to be noindexed and excluded from the
    sitemap; it is now indexable for these trade pages (they carry unique
    per-trade-per-city copy), so their indexation has to be tracked too.
    """
    trades, cities = parse_flagships(repo)
    urls = [f"{BASE}/nl/werkgevers"]
    for lang in ("nl", "fr", "ru"):
        for t in sorted(trades):
            urls.append(f"{BASE}/{lang}/diensten/onderaannemer-{t}")
        for c in sorted(cities):
            for t in sorted(trades):
                urls.append(f"{BASE}/{lang}/diensten/onderaannemer-{t}-{c}")
    return urls


def inspect(service, site: str, url: str) -> dict:
    try:
        r = service.urlInspection().index().inspect(
            body={"inspectionUrl": url, "siteUrl": site}).execute()
    except Exception as e:  # noqa: BLE001
        return {"url": url, "error": f"{type(e).__name__}: {e}"}
    ir = r.get("inspectionResult", {}) or {}
    isr = ir.get("indexStatusResult", {}) or {}
    return {
        "url": url,
        "verdict": isr.get("verdict"),
        "coverage": isr.get("coverageState"),
        "last_crawl": (isr.get("lastCrawlTime") or "").replace("T", " ").replace("Z", "")[:16],
        "fetch": isr.get("pageFetchState"),
    }


def is_indexed(row: dict) -> bool:
    """True only when Google actually indexed the page.

    Note: 'Discovered - currently not indexed', 'Crawled - currently not indexed',
    'Duplicate...', 'Excluded...', 'URL is unknown to Google' all contain the
    substring 'indexed' but are NOT indexed — so check the negatives first.
    """
    cov = (row.get("coverage") or "").lower()
    if not cov:
        return False
    negatives = ("not indexed", "unknown to google", "excluded", "duplicate",
                 "alternate page", "noindex", "blocked", "error", "soft 404")
    if any(n in cov for n in negatives):
        return False
    return "indexed" in cov


def send_email(cfg: dict, subject: str, body: str) -> None:
    n = cfg["notify"]
    msg = EmailMessage()
    msg["From"] = n["from"]
    msg["To"] = n["to"]
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(n["gmail_user"], n["gmail_app_password"].replace(" ", ""))
        s.send_message(msg)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--dry-run", action="store_true",
                    help="list the URLs only (no API calls)")
    ap.add_argument("--no-email", action="store_true",
                    help="run the check but skip the summary email")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    site = cfg["gsc_property"]

    if args.dry_run:
        urls = priority_urls(args.repo)
        print(f"Dry run — {len(urls)} URLs would be inspected (site={site}):\n")
        for u in urls:
            print("  ", u)
        return

    service = gsc_client.get_service(cfg["oauth_client_secret"], cfg["token_file"])
    urls = priority_urls(args.repo)
    print(f"Checking {len(urls)} URLs via urlInspection.index (site={site}) ...\n")

    rows = []
    for u in urls:
        row = inspect(service, site, u)
        rows.append(row)
        status = row.get("coverage") or row.get("error") or "unknown"
        print(f"  {u}")
        print(f"      -> {status}   (verdict={row.get('verdict')}, last_crawl={row.get('last_crawl') or '-'})")
        time.sleep(1)  # be gentle with the api quota

    indexed = [r for r in rows if is_indexed(r)]
    not_idx = [r for r in rows if not is_indexed(r) and "error" not in r]
    err = [r for r in rows if "error" in r]

    # Age tracking: a URL that has been unindexed for weeks is worth a manual
    # nudge; one discovered yesterday is not. We only know the age by remembering
    # when we first saw it.
    state_path = cfg.get("monitor_state", "data/gsc_monitor_state.json")
    state = load_state(state_path)
    today = dt.date.today()
    update_state(state, rows, today, is_indexed)
    save_state(state_path, state)

    def age_days(r):
        first = (state.get(r["url"]) or {}).get("first_not_indexed")
        try:
            return (today - dt.date.fromisoformat(first)).days
        except (TypeError, ValueError):
            return 0

    stuck = sorted([r for r in not_idx if age_days(r) >= STUCK_DAYS], key=age_days, reverse=True)
    recent = [r for r in not_idx if age_days(r) < STUCK_DAYS]
    requestable = [r["url"] for r in stuck][:INDEX_REQUEST_QUOTA]

    print(f"\nDone. indexed={len(indexed)}  not-indexed={len(not_idx)}  errors={len(err)}")
    print(f"  stuck >= {STUCK_DAYS} days: {len(stuck)} | newly not-indexed: {len(recent)}")
    for r in stuck:
        print(f"  STUCK {age_days(r):3d}d: {r['url']} -> {r.get('coverage')}")
    for r in recent:
        print(f"  new      : {r['url']} -> {r.get('coverage')}")
    for r in err:
        print(f"  ERROR    : {r['url']} -> {r.get('error')}")

    # Copy-paste file for the GSC URL-inspection box.
    os.makedirs("reports", exist_ok=True)
    with open("reports/gsc-request-indexing.txt", "w") as f:
        f.write(f"# URLs still not indexed, oldest first — {today.isoformat()}\n")
        f.write("# Request indexing for at most ~10 per day in GSC (no API exists for this step).\n\n")
        for r in sorted(stuck + recent, key=lambda x: -age_days(x)):
            f.write(f"{r['url']}\t{age_days(r)}d\t{r.get('coverage') or ''}\n")

    if (not_idx or err) and cfg.get("notify") and not args.no_email:
        lines = ["# GSC index monitor",
                 f"Site: {site}",
                 f"Checked: {len(rows)} URLs | indexed: {len(indexed)} | "
                 f"not-indexed: {len(not_idx)} | errors: {len(err)}",
                 f"Stuck >= {STUCK_DAYS} days: {len(stuck)} | newly not-indexed: {len(recent)}",
                 ""]
        if requestable:
            lines += [f"## The only manual step (quota ~{INDEX_REQUEST_QUOTA}/day)",
                      "These have resisted a recrawl for weeks, so request indexing for "
                      "them in GSC — copy-paste from reports/gsc-request-indexing.txt:", ""]
            lines += [f"- {u}" for u in requestable]
            if len(stuck) > len(requestable):
                lines += ["", f"({len(stuck) - len(requestable)} more are stuck; take them "
                              "over the following days.)", ""]
        else:
            lines += ["## No action needed",
                      "Nothing has been unindexed long enough to be worth a manual "
                      "request. The sitemap is resubmitted automatically; age tracking "
                      "starts from the first observation.", ""]
        lines += ["## Newly not-indexed (leave alone — Google needs time)", ""]
        lines += [f"- {r['url']} → {r.get('coverage')}" for r in recent] or ["- (none)"]
        if err:
            lines += ["", "## Inspection errors", ""]
            lines += [f"- {r['url']} → {r.get('error')}" for r in err]
        try:
            send_email(cfg, "[SEO] GSC index monitor: pages not indexed", "\n".join(lines))
            print("\nEmail sent (not-indexed pages).")
        except Exception as e:  # noqa: BLE001
            print("email failed:", type(e).__name__, e)
    else:
        print("\nNo email sent (--no-email, all priority pages indexed, or notify disabled).")


if __name__ == "__main__":
    main()
