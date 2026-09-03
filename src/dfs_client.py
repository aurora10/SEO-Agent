"""DataForSEO SERP + search-volume fetcher.

Costs (as of 2025, verify on their pricing page):
  - SERP organic (live):      ~$0.0020 per keyword per location
  - Search volume (Keywords Data, live): ~$0.075 per 1000 keywords... but min $0.05/task.
Strategy: volume for all keywords in ONE task (cheap), SERP live per keyword
in 2 locations (BE, NL). ~59 keywords x 2 locations x $0.002 = ~$0.24/run.

Results are cached in SQLite — re-runs cost nothing unless cache is expired.
"""
import json
import sqlite3
import time
from datetime import date, datetime

import requests

API = "https://api.dataforseo.com/v3"

# Location codes: https://docs.dataforseo.com/v3/keywords_data/google/locations/
LOCATIONS = {"BE": 2056, "NL": 2528}
LANG = {"nl": "nl", "fr": "fr"}


class DFS:
    def __init__(self, login: str, password: str):
        self.auth = (login, password)

    def _post(self, path: str, payload: list[dict]) -> dict:
        r = requests.post(f"{API}{path}", json=payload, auth=self.auth, timeout=120)
        r.raise_for_status()
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
