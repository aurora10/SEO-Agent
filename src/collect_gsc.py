"""Agent 1: GSC data collector.

Backfills history on first run, then incrementally syncs new dates.
Safe to re-run any time — INSERT OR REPLACE dedupes.

Usage:
    python src/collect_gsc.py --config config.yaml
"""
import argparse
from datetime import date, timedelta

import yaml

import db
import gsc_client


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    conn = db.connect(cfg["db_path"])

    service = gsc_client.get_service(cfg["oauth_client_secret"], cfg["token_file"])

    _, default_end = gsc_client.default_window(cfg["backfill_days"])

    last = db.last_synced_date(conn, gsc_client.DIMENSION_SET)
    if last:
        start = date.fromisoformat(last) + timedelta(days=1)
        print(f"Resuming after last synced date: {last}")
    else:
        start, _ = gsc_client.default_window(cfg["backfill_days"])
        print(f"First run: backfilling from {start}")

    if start > default_end:
        print("Already up to date (GSC data lags ~3 days). Nothing to do.")
        return

    day = start
    total = 0
    while day <= default_end:
        rows = gsc_client.fetch_day(
            service, cfg["gsc_property"], day, cfg["row_limit"]
        )
        n = db.upsert_rows(conn, rows)
        db.set_synced_date(conn, gsc_client.DIMENSION_SET, day.isoformat())
        total += n
        print(f"{day}: {n} rows")
        day += timedelta(days=1)

    print(f"Done. {total} rows upserted into {cfg['db_path']}")


if __name__ == "__main__":
    main()
