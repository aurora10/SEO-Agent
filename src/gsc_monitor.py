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
import os
import re
import smtplib
import sys
import time
from email.message import EmailMessage

import yaml

import gsc_client

BASE = "https://constructief-bouw.be"
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
    - base trade pages: /diensten/onderaannemer-{trade} (nl + fr)
    - city trade+city pages: /diensten/onderaannemer-{trade}-{city} (nl + fr) — the
      pages that actually rank for city+trade queries.
    """
    trades, cities = parse_flagships(repo)
    urls = [f"{BASE}/nl/werkgevers"]
    for lang in ("nl", "fr"):
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
    cov = (row.get("coverage") or "").lower()
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

    print(f"\nDone. indexed={len(indexed)}  not-indexed={len(not_idx)}  errors={len(err)}")
    for r in not_idx:
        print(f"  NOT INDEXED: {r['url']} -> {r.get('coverage')} (verdict={r.get('verdict')})")
    for r in err:
        print(f"  ERROR: {r['url']} -> {r.get('error')}")

    if (not_idx or err) and cfg.get("notify"):
        lines = ["# GSC index monitor",
                 f"Site: {site}", f"Checked: {len(rows)} URLs",
                 f"indexed: {len(indexed)} | not-indexed: {len(not_idx)} | errors: {len(err)}", ""]
        lines += [f"- {r['url']} → {r.get('coverage') or r.get('error')}" for r in (not_idx + err)]
        try:
            send_email(cfg, "[SEO] GSC index monitor: pages not indexed", "\n".join(lines))
            print("\nEmail sent (not-indexed pages).")
        except Exception as e:  # noqa: BLE001
            print("email failed:", type(e).__name__, e)
    else:
        print("\nNo email sent (all priority pages indexed, or notify disabled).")


if __name__ == "__main__":
    main()
