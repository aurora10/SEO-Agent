"""Sanity check: show what's in the DB.

This output is the contract for Agent 2 (analyzer). Run after collecting:

    python src/inspect_data.py --config config.yaml
"""
import argparse
import sqlite3

import yaml


def q(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config))
    conn = sqlite3.connect(cfg["db_path"])

    r = q(conn, """
        SELECT MIN(date), MAX(date), COUNT(*),
               COUNT(DISTINCT query), COUNT(DISTINCT page), SUM(clicks)
        FROM performance
    """)[0]
    print(f"Date range:      {r[0]} .. {r[1]}")
    print(f"Rows:            {r[2]}")
    print(f"Unique queries:  {r[3]}")
    print(f"Unique pages:    {r[4]}")
    print(f"Total clicks:    {int(r[5] or 0)}")

    print("\n-- Top 15 queries by impressions (all time) --")
    for row in q(conn, """
        SELECT query, SUM(impressions) imp, SUM(clicks) clk,
               ROUND(AVG(position), 1) pos
        FROM performance GROUP BY query ORDER BY imp DESC LIMIT 15
    """):
        print(f"  {row[1]:>7} imp  {row[2]:>5} clk  pos {row[3]:>5}  {row[0]}")

    print("\n-- Pages by impressions (all time) --")
    for row in q(conn, """
        SELECT page, SUM(impressions) imp, SUM(clicks) clk,
               ROUND(AVG(position), 1) pos
        FROM performance GROUP BY page ORDER BY imp DESC LIMIT 20
    """):
        print(f"  {row[1]:>7} imp  {row[2]:>5} clk  pos {row[3]:>5}  {row[0]}")

    print("\n-- Striking distance (pos 4-20, sorted by impressions) --")
    rows = q(conn, """
        SELECT query, page, SUM(impressions) imp, ROUND(AVG(position), 1) pos
        FROM performance
        WHERE date >= date('now', '-28 days')
        GROUP BY query, page
        HAVING imp >= 5 AND pos BETWEEN 4 AND 20
        ORDER BY imp DESC LIMIT 25
    """)
    if not rows:
        print("  (none yet — normal for a new/low-traffic site)")
    for row in rows:
        print(f"  {row[2]:>6} imp  pos {row[3]:>5}  {row[0]}  ->  {row[1]}")


if __name__ == "__main__":
    main()
