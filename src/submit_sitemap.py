"""Resubmit the site's sitemap to Google Search Console — automatically.

Why this exists: "Request Indexing" in the GSC UI has NO public API and a
~10 URL/day quota, so it cannot be automated. What CAN be automated is the
signal that actually gets Google to recrawl: submitting the sitemap. This does
that, and reports what Google last downloaded so a stale sitemap is visible
instead of silent.

Run weekly (the scheduler does) or by hand:
    python src/submit_sitemap.py --config config.yaml
    python src/submit_sitemap.py --config config.yaml --list      # status only
    python src/submit_sitemap.py --config config.yaml --auth      # re-consent (needs a browser)

Exit code is 0 on success and non-zero on failure, so run_job.py emails it.
"""
import argparse
import sys

import yaml
from googleapiclient.errors import HttpError

from gsc_client import (RECONSENT_HELP, can_write, get_service, granted_scopes,
                        load_credentials)

DEFAULT_SITEMAP = "https://constructief-bouw.be/sitemap.xml"


def _count(value) -> int:
    """GSC returns these as strings ("0"/"1") and sometimes as lists."""
    if value is None:
        return 0
    if isinstance(value, (list, tuple)):
        return len(value)
    try:
        return int(str(value).strip() or 0)
    except ValueError:
        return 0


def _fmt(value) -> str:
    return value or "-"


def sitemap_status(service, site: str) -> list[dict]:
    resp = service.sitemaps().list(siteUrl=site).execute()
    return resp.get("sitemap", [])


def prune(service, site: str, entries: list[dict]) -> int:
    """Delete entries registered as sitemaps that cannot be sitemaps.

    A page URL pasted into the sitemap field stays in the property forever,
    reporting an error on every crawl and teaching Google nothing. Only entries
    that are clearly not XML sitemaps AND have no contents are removed.
    """
    junk = [s for s in entries
            if not str(s.get("path", "")).lower().split("?")[0].endswith((".xml", ".xml.gz"))
            or not (s.get("contents") or [])]
    if not junk:
        print("\nNothing to prune: every registered sitemap looks like a sitemap.")
        return 0
    print("\nNot actually sitemaps (page URLs registered in the sitemap field):")
    for s in junk:
        print(f"  - {s.get('path')}  (type={s.get('type')}, "
              f"errors={_count(s.get('errors'))}, contents={len(s.get('contents') or [])})")
    rc = 0
    for s in junk:
        path = s.get("path")
        try:
            service.sitemaps().delete(siteUrl=site, feedpath=path).execute()
            print(f"  deleted {path}")
        except HttpError as e:
            print(f"  could not delete {path}: {e}")
            rc = 1
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--sitemap", default=None,
                    help=f"sitemap URL to submit (default {DEFAULT_SITEMAP})")
    ap.add_argument("--list", action="store_true", help="only report current status")
    ap.add_argument("--prune", action="store_true",
                    help="delete entries registered as sitemaps that are not sitemaps")
    ap.add_argument("--auth", action="store_true",
                    help="re-consent now (opens a browser) and exit — use after "
                         "publishing the OAuth app to get a long-lived token")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    site = cfg["gsc_property"]
    creds = load_credentials(cfg["oauth_client_secret"], cfg["token_file"],
                             force=args.auth)
    scopes = granted_scopes(creds)
    print(f"Property: {site}")
    print(f"Token scopes: {', '.join(scopes) or '(none reported)'}")
    can_write_now = can_write(creds)
    if not can_write_now:
        print("  -> read-only token: sitemap status can be read, submit cannot.")

    if args.auth:
        if can_write_now:
            print("\nFresh token written with write scope. Copy it to the VPS:\n"
                  "  base64 -i credentials/token.json | tr -d '\\n'   # -> TOKEN_JSON in .env")
            return 0
        print("\nToken still lacks the write scope — check the consent screen's "
              "configured scopes.")
        return 1

    sitemap = args.sitemap or DEFAULT_SITEMAP
    service = get_service(cfg["oauth_client_secret"], cfg["token_file"])

    # ---- current status (read scope is enough) -----------------------------
    try:
        entries = sitemap_status(service, site)
    except HttpError as e:
        print(f"Could not list sitemaps: {e}")
        entries = []
    if entries:
        print("\nSitemaps known to Google:")
        for s in entries:
            errs = _count(s.get("errors"))
            warns = _count(s.get("warnings"))
            contents = s.get("contents") or []
            print(f"  {s.get('path')}")
            print(f"    last submitted : {_fmt(s.get('lastSubmitted'))}")
            print(f"    last downloaded: {_fmt(s.get('lastDownloaded'))}"
                  + ("  (NEVER — Google has not fetched it)" if not s.get("lastDownloaded") else ""))
            print(f"    pending        : {s.get('isPending', False)}"
                  f" | errors: {errs} | warnings: {warns}")
            for c in contents:
                print(f"    contents       : {c.get('type')} — {c.get('submitted')} submitted, "
                      f"{c.get('indexed')} indexed by Google")
            if not contents:
                print("    ! Not a readable sitemap (no contents). A page URL added in "
                      "the sitemap field always fails — remove it with "
                      "`sitemaps().delete` (this script does that with --prune).")
            if not s.get("isPending") and not s.get("lastDownloaded"):
                print("    ! Google has never downloaded this sitemap — check it "
                      "returns 200 XML and is allowed by robots.txt.")
    else:
        print("\nNo sitemaps registered for this property yet.")

    if args.prune:
        return prune(service, site, entries)

    if args.list:
        return 0

    # ---- submit ------------------------------------------------------------
    print(f"\nSubmitting {sitemap} ...")
    try:
        service.sitemaps().submit(siteUrl=site, feedpath=sitemap).execute()
    except HttpError as e:
        status = getattr(e.resp, "status", None)
        body = str(e)
        if status == 403 and "scope" in body.lower():
            print("\nFAILED: token lacks the write scope.\n" + RECONSENT_HELP)
            return 1
        print(f"\nFAILED: {e}")
        if status == 404:
            print("  The sitemap URL must be under the property and return 200. "
                  "Check it in a browser first.")
        return 1

    print("Submitted. Google will re-fetch the sitemap shortly.")
    try:
        for s in sitemap_status(service, site):
            if s.get("path") == sitemap:
                print(f"  lastSubmitted now: {_fmt(s.get('lastSubmitted'))} "
                      f"| pending: {s.get('isPending', False)}")
    except HttpError:
        pass
    print("\nNote: submitting a sitemap is a recrawl signal, not a guarantee. "
          "Per-URL indexing still happens on Google's schedule; gsc_monitor.py "
          "tracks which URLs are actually indexed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
