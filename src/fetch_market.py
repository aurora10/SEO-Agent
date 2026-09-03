"""Agent 2 step 1: fetch market data (volumes + SERPs) into market.db.

Usage:
    python src/fetch_market.py --config config.yaml
"""
import argparse
import sqlite3
import time

import yaml

import dfs_client
from keywords import build


def split_target_locations(cfg):
    """Returns list of (location, lang) pairs to query."""
    locs = set()
    for m in cfg["markets"]:
        locs.add((m["country"], m["language"]))
    return sorted(locs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config))

    dfs_cfg = cfg["dataforseo"]
    dfs = dfs_client.DFS(dfs_cfg["login"], dfs_cfg["password"])

    your_domain = cfg.get("your_domain", "constructief-bouw.be")
    conn = sqlite3.connect(cfg.get("market_db", "data/market.db"))
    dfs_client.ensure_tables(conn)

    kws = build()
    print(f"{len(kws)} keywords in universe")

    # --- 1. Volumes: one task per (location, lang), all keywords batched ---
    for (loc, lang) in split_target_locations(cfg):
        cached = dfs_client.volume_cached(conn, loc)
        pending = [k.keyword for k in kws if k.lang == lang
                   and k.keyword not in cached]
        if not pending:
            print(f"Volumes {loc}/{lang}: all cached")
            continue
        # one task handles up to 1000 keywords — plenty
        data = dfs.search_volume(pending, loc, lang)
        dfs_client.save_volume(conn, loc, data)
        got = sum(1 for k, v in data.items() if v["volume"] > 0)
        print(f"Volumes {loc}/{lang}: {len(data)} fetched, {got} with volume > 0")
        time.sleep(1)

    # --- 2. SERPs: per keyword per relevant location ---
    fetched = 0
    for kw in kws:
        for (loc, lang) in split_target_locations(cfg):
            if lang != kw.lang:
                continue
            # BE keywords also checked in NL location and vice versa only for
            # city keywords of that country; keep it simple: kw's own market.
            if kw.city and kw.city in ("amsterdam", "rotterdam") and loc != "NL":
                continue
            if kw.city and kw.city not in ("amsterdam", "rotterdam") and loc != "BE":
                continue
            if not kw.city and loc not in ("BE",):
                continue  # national keywords: check BE only (main market)
            cache = dfs_client.serp_cached(conn, kw.keyword, loc)
            if cache:
                continue
            result = dfs.serp(kw.keyword, loc, lang, your_domain)
            dfs_client.save_serp(conn, kw.keyword, loc, result)
            fetched += 1
            status = f"rank {result['your_rank']}" if result["your_rank"] else "not ranked"
            print(f"SERP {loc} [{kw.keyword}]: {status} "
                  f"(top: {result['organic'][0]['domain'] if result['organic'] else '-'})")
            if fetched % 20 == 0:
                time.sleep(2)

    print(f"\nDone. {fetched} SERPs fetched this run. "
          f"Next: python src/analyze_market.py --config config.yaml")


if __name__ == "__main__":
    main()
