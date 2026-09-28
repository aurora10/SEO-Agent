"""Google Search Console API access (OAuth installed-app flow).

First run opens a browser for consent and caches token.json;
later runs refresh silently. Uses searchanalytics.query + urlInspection.index.

Note on scopes: `webmasters` is the read-WRITE scope and a superset of
`webmasters.readonly`. Read-only is enough for collecting data and inspecting
index status, but `sitemaps.submit` needs the write scope — a token consented
before that scope was added keeps working for reads and reports a clear
"re-consent once" error on submit.
"""
from datetime import date, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
WRITE_SCOPE = "https://www.googleapis.com/auth/webmasters"  # superset: read + write
SCOPES = [WRITE_SCOPE]
RECONSENT_HELP = (
    "This token was consented with read-only access, so it cannot submit a "
    "sitemap. Fix once: on a machine WITH a browser run "
    "`python src/submit_sitemap.py --config config.yaml --auth` (or any script "
    "here) to re-consent with write access, then update TOKEN_JSON in the VPS "
    ".env with `base64 -i credentials/token.json | tr -d '\\n'` and restart the "
    "container. Reads (collect_gsc, gsc_monitor) keep working meanwhile."
)

# GSC data typically lags ~2-3 days; never request later than this.
DATA_LAG_DAYS = 3

DIMENSIONS = ["query", "page", "country", "device"]
DIMENSION_SET = ",".join(DIMENSIONS)


def load_credentials(client_secret_path: str, token_path: str, force: bool = False):
    """Valid Credentials, re-consenting via the browser flow when needed.

    `force=True` ignores the cached token and runs the consent flow again — use it
    after publishing the OAuth app, because tokens issued while the app is in
    'Testing' expire after 7 days and only a fresh consent gets a long-lived one.
    """
    creds = None
    if not force:
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception:
            creds = None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            # Token revoked/expired (invalid_grant): fall through and re-consent
            # via the browser flow below rather than crashing.
            creds = None
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
        try:
            creds = flow.run_local_server(port=0)
        except Exception as e:
            # Headless container: there is no browser to run the consent flow.
            raise RuntimeError(
                "GSC token invalid and no browser available to re-consent "
                "(headless container). Fix: re-authenticate on a machine WITH a "
                "browser (run `python src/collect_gsc.py --config config.yaml` "
                "locally), then update TOKEN_JSON in .env (base64 of the fresh "
                "credentials/token.json) and restart the container. "
                "IMPORTANT: if your OAuth consent screen is in 'Testing' status, "
                "Google expires refresh tokens every 7 days — click 'Publish app' "
                "on the consent screen to stop this recurring."
            ) from e
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return creds


def granted_scopes(creds) -> list[str]:
    return list(getattr(creds, "scopes", None) or [])


def can_write(creds) -> bool:
    """True when the token may call write endpoints (sitemaps.submit)."""
    return WRITE_SCOPE in granted_scopes(creds)


def get_service(client_secret_path: str, token_path: str):
    return build("searchconsole", "v1",
                 credentials=load_credentials(client_secret_path, token_path))


def query_range(service, site_url: str, start: str, end: str,
                row_limit: int = 25000) -> list[dict]:
    """Fetch all rows for [start, end] with full pagination."""
    body = {
        "startDate": start,
        "endDate": end,
        "dimensions": DIMENSIONS,
        "rowLimit": row_limit,
        "startRow": 0,
    }
    out = []
    while True:
        resp = (service.searchanalytics()
                .query(siteUrl=site_url, body=body).execute())
        rows = resp.get("rows", [])
        for r in rows:
            keys = r["keys"]
            out.append({
                "date": None,  # caller sets when querying per-day
                "query": keys[0],
                "page": keys[1],
                "country": keys[2],
                "device": keys[3],
                "clicks": r["clicks"],
                "impressions": r["impressions"],
                "ctr": r["ctr"],
                "position": r["position"],
            })
        if len(rows) < row_limit:
            return out
        body["startRow"] += row_limit


def fetch_day(service, site_url: str, day: date, row_limit: int) -> list[dict]:
    """One query per day keeps rows unsampled and lets PRIMARY KEY dedupe."""
    ds = day.isoformat()
    rows = query_range(service, site_url, ds, ds, row_limit)
    for r in rows:
        r["date"] = ds
    return rows


def default_window(backfill_days: int) -> tuple[date, date]:
    end = date.today() - timedelta(days=DATA_LAG_DAYS)
    start = end - timedelta(days=backfill_days)
    return start, end
