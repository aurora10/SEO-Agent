"""Offline test for Agent 2: mocked DataForSEO responses."""
import json
import sqlite3
import sys

sys.path.insert(0, '.')
import requests

import dfs_client
from keywords import build

FAKE_VOLUME = {
    "status_code": 20000,
    "tasks": [{"result": [
        {"keyword": k, "search_volume": v, "competition": 0.5}
        for k, v in [
            ("onderaannemer bouw", 880), ("bouwploeg huren", 390),
            ("detachering bouwpersoneel", 140), ("stukadoor ploeg", 90),
            ("onderaannemer betonwerken", 170), ("sous-traitant façade", 50),
        ]
    ]}],
}

FAKE_SERP = {
    "status_code": 20000,
    "tasks": [{"result": [{"items": [
        {"type": "organic", "rank_absolute": 1,
         "url": "https://www.tempo-team.be/onderaannemer",
         "domain": "tempo-team.be", "title": "Onderaannemer bouw | Tempo-Team",
         "description": "..."},
        {"type": "organic", "rank_absolute": 2, "url": "https://www.jobfixers.be/bouw",
         "domain": "jobfixers.be", "title": "Bouwpersoneel | Jobfixers",
         "description": "..."},
        {"type": "organic", "rank_absolute": 7,
         "url": "https://constructief-bouw.be/nl/werkgevers",
         "domain": "constructief-bouw.be",
         "title": "Voor werkgevers | Constructief", "description": "..."},
    ]}]}],
}


def fake_post(url, **kwargs):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return FAKE_SERP if "serp" in url else FAKE_VOLUME
    return R()


def main():
    orig = requests.post
    requests.post = fake_post
    try:
        cfg_markets = [{"language": "nl", "country": "BE"},
                       {"language": "fr", "country": "BE"}]
        dfs = dfs_client.DFS("x", "x")
        conn = sqlite3.connect("data/test_market.db")
        dfs_client.ensure_tables(conn)

        kws = build()
        # volumes (mocked)
        for m in cfg_markets:
            pending = [k.keyword for k in kws if k.lang == m["language"]][:6]
            data = dfs.search_volume(pending, m["country"], m["language"])
            dfs_client.save_volume(conn, m["country"], data)
        nvol = conn.execute("SELECT COUNT(*) FROM keyword_volume").fetchone()[0]
        assert nvol >= 6, nvol
        print(f"volume cache OK ({nvol} rows)")

        # serps (mocked): your site ranks 7, competitors above
        for kw in kws[:3]:
            res = dfs.serp(kw.keyword, "BE", kw.lang, "constructief-bouw.be")
            assert res["your_rank"] == 7, res
            assert res["organic"][0]["domain"] == "tempo-team.be"
            dfs_client.save_serp(conn, kw.keyword, "BE", res)
        nserp = conn.execute("SELECT COUNT(*) FROM serp_cache").fetchone()[0]
        assert nserp == 3
        print(f"serp cache OK ({nserp} rows)")

        # cache-hit behavior: second lookup must come from cache even w/o mock
        requests.post = orig  # real requests now — must NOT be called
        cached = dfs_client.serp_cached(conn, kws[0].keyword, "BE")
        assert cached and cached["your_rank"] == 7
        print("cache-hit read OK (no HTTP needed)")
    finally:
        requests.post = orig

    print("ALL OK")


if __name__ == "__main__":
    import os
    if os.path.exists("data/test_market.db"):
        os.remove("data/test_market.db")
    main()
