"""Google Search Console API access (OAuth installed-app flow).

First run opens a browser for consent and caches token.json;
later runs refresh silently. Uses the searchanalytics.query endpoint.
"""
from datetime import date, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
# GSC data typically lags ~2-3 days; never request later than this.
DATA_LAG_DAYS = 3

DIMENSIONS = ["query", "page", "country", "device"]
DIMENSION_SET = ",".join(DIMENSIONS)


def get_service(client_secret_path: str, token_path: str):
    creds = None
    try:
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    except Exception:
        creds = None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return build("searchconsole", "v1", credentials=creds)


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
