"""DataForSEO SERP + search-volume fetcher.

Costs (as of 2025, verify on their pricing page):
  - SERP organic (live):      ~$0.0020 per keyword per location
  - Search volume (Keywords Data, live): ~$0.075 per 1000 keywords... but min $0.05/task.
Strategy: volume for all keywords in ONE task per market (cheap), SERP live per
keyword. Markets come from config `markets` (BE/nl, NL/nl, BE/ru): SERPs are only
fetched for keywords that actually report volume, so the spend tracks demand.
Results are cached in SQLite — re-runs cost nothing unless cache is expired.
"""
import json
import sqlite3
import time
from datetime import date, datetime

import requests

API = "https://api.dataforseo.com/v3"

# Location codes: https://docs.dataforseo.com/v3/keywords_data/google/locations/
# (verified against GET /v3/serp/google/locations).
# Every country in config `markets` MUST be here, and every language in
# src/keywords.py MUST be in LANG — a missing entry is a hard KeyError on the
# first keyword of that market, i.e. it breaks the whole monthly run.
# BE/NL = where the work is; UA/PL/RO/LT/LV/EE = where the crews are recruited
# from, searched in Russian (see config `markets`).
LOCATIONS = {
    "BE": 2056, "NL": 2528,                      # destination markets
    "UA": 2804, "PL": 2616, "RO": 2642,          # recruiting markets
    "LT": 2440, "LV": 2428, "EE": 2233,          # recruiting markets (Baltics)
}
LANG = {"nl": "nl", "fr": "fr", "ru": "ru"}


class DFS:
    def __init__(self, login: str, password: str):
        self.auth = (login, password)

    def _post(self, path: str, payload: list[dict]) -> dict:
        r = requests.post(f"{API}{path}", json=payload, auth=self.auth, timeout=120)
        if r.status_code != 200:
            # The JSON body carries the real reason when there is one, but for
            # HTTP 402 the body can be a plain envelope ("status_code 20000: Ok")
            # which reads like success while the request was refused. Name the
            # real cause: an exhausted DataForSEO balance.
            body = {}
            try:
                body = r.json()
            except ValueError:
                pass
            detail = (f"{body.get('status_code')}: {body.get('status_message')}"
                      if body else r.text[:200])
            if r.status_code == 402:
                raise RuntimeError(
                    "DataForSEO HTTP 402 — the account has no funds left, so this "
                    "request was refused (the body may misleadingly say 'Ok'). "
                    f"Top up at https://app.dataforseo.com/ then re-run. Detail: {detail}")
            if r.status_code == 403:
                raise RuntimeError(
                    "DataForSEO HTTP 403 — usually the API IP whitelist or an "
                    f"unverified account (40104). Detail: {detail}")
            raise RuntimeError(f"DataForSEO HTTP {r.status_code} ({detail})")
        data = r.json()
        if data.get("status_code") not in (20000,):
            raise RuntimeError(f"DataForSEO error {data.get('status_code')}: "
                               f"{data.get('status_message')}")
        return data

    def search_volume(self, keywords: list[str], location: str, lang: str) -> dict:
        """Returns {keyword: {volume, competition}} for a batch."""
        payload = [{
            "keywords": keywords,
            "location_code": LOCATIONS[location],
            "language_code": LANG[lang],
        }]
        data = self._post("/keywords_data/google_ads/search_volume/live", payload)
        out = {}
        for task in data.get("tasks", []):
            for item in (task.get("result") or []):
                out[item["keyword"]] = {
                    "volume": item.get("search_volume") or 0,
                    "competition": item.get("competition"),
                }
        return out

    def serp(self, keyword: str, location: str, lang: str,
             your_domain: str) -> dict:
        """Live Google SERP. Returns top-10 organic + your rank if found."""
        payload = [{
            "keyword": keyword,
            "location_code": LOCATIONS[location],
            "language_code": LANG[lang],
            "device": "desktop",
            "depth": 30,
        }]
        data = self._post("/serp/google/organic/live/regular", payload)
        organic, your_rank = [], None
        for task in data.get("tasks", []):
            for item in (task.get("result") or [{}])[0].get("items", []):
                if item.get("type") != "organic":
                    continue
                url = item.get("url", "")
                rank = item.get("rank_absolute")
                organic.append({
                    "rank": rank,
                    "url": url,
                    "domain": item.get("domain"),
                    "title": item.get("title"),
                    "description": item.get("description"),
                })
                if your_domain in url and your_rank is None:
                    your_rank = rank
        return {"organic": organic[:10], "your_rank": your_rank}


# --- caching layer -----------------------------------------------------------

def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS keyword_volume (
        keyword TEXT, location TEXT, volume INT, competition REAL,
        fetched_at TEXT, PRIMARY KEY (keyword, location)
    );
    CREATE TABLE IF NOT EXISTS serp_cache (
        keyword TEXT, location TEXT, your_rank INT,
        organic_json TEXT, fetched_at TEXT,
        PRIMARY KEY (keyword, location)
    );
    """)


def volume_cached(conn, location, max_age_days=30):
    rows = conn.execute(
        "SELECT keyword, volume, competition FROM keyword_volume WHERE location=? "
        "AND fetched_at > date('now', ?)",
        (location, f"-{max_age_days} days")).fetchall()
    return {r[0]: {"volume": r[1], "competition": r[2]} for r in rows}


def save_volume(conn, location, data: dict):
    today = date.today().isoformat()
    conn.executemany(
        "INSERT OR REPLACE INTO keyword_volume VALUES (?,?,?,?,?)",
        [(kw, location, d["volume"], d.get("competition"), today)
         for kw, d in data.items()])
    conn.commit()


def serp_cached(conn, keyword, location, max_age_days=14):
    row = conn.execute(
        "SELECT your_rank, organic_json FROM serp_cache WHERE keyword=? AND location=? "
        "AND fetched_at > date('now', ?)",
        (keyword, location, f"-{max_age_days} days")).fetchone()
    if row:
        return {"your_rank": row[0], "organic": json.loads(row[1])}
    return None


def save_serp(conn, keyword, location, result):
    conn.execute(
        "INSERT OR REPLACE INTO serp_cache VALUES (?,?,?,?,?)",
        (keyword, location, result["your_rank"],
         json.dumps(result["organic"]), date.today().isoformat()))
    conn.commit()
