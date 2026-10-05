"""Agent 2 step 2: market analysis report.

Reads market.db + GSC seo.db, produces:
  - reports/market-analysis.md     (human readable)
  - data/market-analysis.json      (for Agent 3, the content writer)

Run:
    python src/analyze_market.py --config config.yaml
    python src/analyze_market.py --config config.yaml --llm   # add LLM gap analysis
"""
import argparse
import json
import os
import sqlite3
from collections import defaultdict
from urllib.parse import urlparse

import yaml

# Map keyword -> page the site HAS or SHOULD HAVE.
# Paths carry NO locale; the locale is chosen per keyword's language, so French
# keywords target /fr/ pages and Dutch target /nl/ (fixes French->/nl/ mixing).
BASE = "https://constructief-bouw.be"
PAGE_MAP = {
    "core": "werkgevers",
    # Must match the real route slugs (flagshipTrades in the site's
    # src/data/cityContent.ts). Four entries used to point at slugs that do not
    # exist — gevelwerk, betonwerken, dakwerken, afwerking — so the analysis was
    # handing Agent 3 target pages that 404.
    "trade:gevel": "diensten/onderaannemer-gevel",
    "trade:renovatie": "diensten/onderaannemer-renovatie",
    "trade:beton": "diensten/onderaannemer-beton",
    "trade:dak": "diensten/onderaannemer-dak",
    "trade:ruwbouw": "diensten/onderaannemer-ruwbouw",
    "trade:interieur": "diensten/onderaannemer-interieur",
    # Job-seeker cluster (nl + ru): the vacancy pages, never the B2B service
    # pages — "vacature metselaar" is somebody looking for work, not a buyer.
    "jobs": "vacatures",
}
CITY_PAGE = "diensten/onderaannemer-{city}"
# jobs:<slug> -> job-seeker landing per trade (slug matches the site's
# src/data/vacancyTrades.ts, e.g. metselaar, bekister, kraanmachinist).
JOB_TRADE_PAGE = "vacatures/{slug}"

# Fallbacks mirroring the site. --repo overrides both from the site checkout so
# a target page is only recommended when the route really exists.
FLAGSHIP_TRADES = {"gevel", "renovatie", "beton", "dak", "ruwbouw", "interieur"}
FLAGSHIP_CITIES = {"antwerpen", "gent", "leuven", "brussel"}

MISSING = "MISSING (create page)"


def parse_flagships(repo):
    """(trades, cities) from the site, falling back to the lists above."""
    import re

    trades, cities = set(FLAGSHIP_TRADES), set(FLAGSHIP_CITIES)
    if not repo:
        return trades, cities
    for rel, pattern, which in (
        ("src/data/cityContent.ts", r"flagshipTrades\s*=\s*\[([^\]]+)\]", "trades"),
        ("src/data/cities.ts", r"flagshipCitySlugs\s*=\s*\[([^\]]+)\]", "cities"),
    ):
        try:
            found = re.search(pattern, open(os.path.join(repo, rel)).read())
        except OSError:
            continue
        if found:
            values = set(re.findall(r"'([^']+)'", found.group(1)))
            if which == "trades":
                trades = values
            else:
                cities = values
    return trades, cities


def _locale_for(lang: str) -> str:
    # Each language targets its OWN locale: fr -> /fr, ru -> /ru, everything else
    # /nl. (ru fell through to /nl before, so the Russian job keywords would have
    # been pointed at Dutch pages.)
    return {"fr": "fr", "ru": "ru", "en": "en"}.get(lang, "nl")


def best_url_for(kw, flagships=None) -> str:
    trades, cities = flagships or (FLAGSHIP_TRADES, FLAGSHIP_CITIES)
    cluster = kw.cluster
    locale = _locale_for(kw.lang)
    if cluster.startswith("jobs:"):
        # job-seeker landing page per trade, in the keyword's own language
        return f"{BASE}/{locale}/{JOB_TRADE_PAGE.format(slug=cluster.split(':', 1)[1])}"
    if cluster.startswith("city:"):
        parts = cluster.split(":")
        city = parts[1]
        if len(parts) == 3:
            # city+trade: point at the most specific page that exists. The
            # trade+city landing is the best target for a city+trade query, but
            # only for flagship trades/cities — other combinations render a thin
            # page (or none), so those keep the country-level trade page.
            trade = parts[2]
            if trade in trades and city in cities:
                return f"{BASE}/{locale}/diensten/onderaannemer-{trade}-{city}"
            path = PAGE_MAP.get(f"trade:{trade}")
            return f"{BASE}/{locale}/{path}" if path else MISSING
        return f"{BASE}/{locale}/{CITY_PAGE.format(city=city)}"
    path = PAGE_MAP.get(cluster)
    return f"{BASE}/{locale}/{path}" if path else MISSING


def load_gsc_positions(gsc_db: str) -> dict:
    """Latest 28d avg position per query from your existing GSC data.
    Returns {} if the GSC DB doesn't exist yet."""
    if not __import__("os").path.exists(gsc_db):
        return {}
    conn = sqlite3.connect(gsc_db)
    try:
        rows = conn.execute("""
        SELECT query, ROUND(AVG(position),1), SUM(impressions)
        FROM performance
        WHERE date >= date('now','-28 days')
        GROUP BY query
    """).fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    return {q: {"gsc_pos": p, "gsc_imp": i} for q, p, i in rows}


def classify(v: dict) -> str:
    if v["your_rank"] is None:
        return "not_ranking"
    if v["your_rank"] <= 3:
        return "top3"
    if v["your_rank"] <= 10:
        return "page1"
    if v["your_rank"] <= 20:
        return "striking_distance"
    return "deep"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--llm", action="store_true", help="LLM gap analysis (needs llm section in config)")
    ap.add_argument("--repo", default=None,
                    help="site checkout; used to check which trade/city pages really exist")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config))
    flagships = parse_flagships(args.repo)

    from keywords import build
    universe = {k.keyword: k for k in build()}

    market = sqlite3.connect(cfg.get("market_db", "data/market.db"))
    market.row_factory = sqlite3.Row

    gsc = load_gsc_positions(cfg["db_path"])

    rows = []
    for serp in market.execute(
            "SELECT keyword, location, your_rank, organic_json FROM serp_cache"):
        kw = serp["keyword"]
        k = universe.get(kw)
        if not k:
            continue
        vol = market.execute(
            "SELECT volume, competition FROM keyword_volume WHERE keyword=? AND location=?",
            (kw, serp["location"])).fetchone()
        organic = json.loads(serp["organic_json"])
        item = {
            "keyword": kw,
            "cluster": k.cluster,
            "lang": k.lang,
            "city": k.city,
            "location": serp["location"],
            "volume": vol[0] if vol else 0,
            "competition": vol[1] if vol else None,
            "your_rank": serp["your_rank"],
            "gsc": gsc.get(kw, {}),
            "target_page": best_url_for(k, flagships),
            "top_competitors": [
                {"domain": o["domain"], "title": o["title"], "rank": o["rank"]}
                for o in organic[:5]
                if o["domain"] and cfg.get("your_domain", "constructief-bouw.be")
                not in o["domain"]
            ][:3],
        }
        item["status"] = classify({"your_rank": serp["your_rank"]})
        rows.append(item)

    # Aggregate by cluster
    clusters = defaultdict(lambda: {"volume": 0, "keywords": 0,
                                    "not_ranking": 0, "striking": 0})
    for r in rows:
        c = clusters[r["cluster"]]
        c["volume"] += r["volume"]
        c["keywords"] += 1
        if r["status"] == "not_ranking":
            c["not_ranking"] += 1
        if r["status"] == "striking_distance":
            c["striking"] += 1

    os.makedirs("data", exist_ok=True)
    os.makedirs("reports", exist_ok=True)

    # ---- write JSON for agent 3 ----
    out_json = {
        "generated": __import__("datetime").date.today().isoformat(),
        "clusters": dict(clusters),
        "keywords": rows,
    }
    with open("data/market-analysis.json", "w") as f:
        json.dump(out_json, f, ensure_ascii=False, indent=2)

    # ---- markdown report ----
    L = []
    L.append("# Market analysis — constructief-bouw.be\n")
    L.append(f"Keywords checked: {len(rows)} | "
             f"Total monthly volume: {sum(r['volume'] for r in rows)}\n")

    L.append("## Clusters by opportunity (volume where you don't rank)\n")
    L.append("| cluster | kw | volume | not ranking | striking (4-20) |")
    L.append("|---|---|---|---|---|")
    for name, c in sorted(clusters.items(), key=lambda x: -x[1]["volume"]):
        L.append(f"| {name} | {c['keywords']} | {c['volume']} | "
                 f"{c['not_ranking']} | {c['striking']} |")

    L.append("\n## Priority keywords (volume > 0, best first)\n")
    L.append("| keyword | vol | your rank | GSC 28d | target page | top competitor |")
    L.append("|---|---|---|---|---|---|")
    prio = sorted([r for r in rows if r["volume"] > 0],
                  key=lambda r: (-r["volume"], r["your_rank"] or 99))
    for r in prio:
        top = r["top_competitors"][0]["domain"] if r["top_competitors"] else "-"
        gsc_s = (f"pos {r['gsc']['gsc_pos']}, {r['gsc']['gsc_imp']} imp"
                 if r["gsc"] else "-")
        L.append(f"| {r['keyword']} | {r['volume']} | {r['your_rank'] or '-'} | "
                 f"{gsc_s} | {r['target_page']} | {top} |")

    L.append("\n## Competitor frequency (who dominates these SERPs)\n")
    comp = defaultdict(int)
    for r in rows:
        for c in r["top_competitors"]:
            comp[c["domain"]] += 1
    for d, n in sorted(comp.items(), key=lambda x: -x[1])[:15]:
        L.append(f"- {d}: in top-3 of {n} keywords")

    if args.llm:
        L.append("\n## LLM gap analysis\n")
        L.append("(see llm_gaps section in JSON / or run again after configuring llm)")
        # LLM call kept separate: fetch competitor pages, diff vs yours.
        # Deliberately NOT inline here — see analyze_gaps_llm.py when ready.

    with open("reports/market-analysis.md", "w") as f:
        f.write("\n".join(L))

    print(f"Wrote reports/market-analysis.md "
          f"({len(prio)} keywords with volume)")
    print("Top 5 clusters:")
    for name, c in sorted(clusters.items(), key=lambda x: -x[1]["volume"])[:5]:
        print(f"  {name}: vol {c['volume']}, {c['not_ranking']} not ranking")


if __name__ == "__main__":
    main()
