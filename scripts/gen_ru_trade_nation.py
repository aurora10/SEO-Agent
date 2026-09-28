"""Generate the RU TradeNation block (base trade pages) for constructief-bouw.be.

Why: /ru/diensten/onderaannemer-{trade} (6 base trade pages) have no
TradeNation copy. `src/app/[locale]/diensten/[slug]/page.tsx` then falls back to
TradeCityLanding with a SYNTHETIC city slug "belgie", whose CitiesSeo lookup
throws MISSING_MESSAGE for ru. Generating RU TradeNation copy both fixes that
page render and gives the Russian base-trade pages their own country-level text
(like nl/fr already have).

The NL TradeNation block is the factual source (same claims, no new statistics),
the RU Trades label/intro keeps terminology consistent with the rest of /ru.

Output: drafts/TradeNation.ru.json -> {"<trade>": {title, meta_description,
intro, section_title, features[3], cta_title, cta_desc, cta_button}}

Usage:
  python scripts/gen_ru_trade_nation.py --repo "/path/to/constructief"
"""
import argparse
import json
import os
import re
import sys

import yaml
from openai import OpenAI

from gen_ru_trade_city import RU_STYLE, chat

TRADES = ["gevel", "renovatie", "beton", "dak", "ruwbouw", "interieur"]
FIELDS = ["title", "meta_description", "intro", "section_title",
          "features", "cta_title", "cta_desc", "cta_button"]

LATIN_OK = {"constructief", "a1", "limosa", "checkinatwork", "b2b", "seo"}


def validate(trade, block):
    problems = []
    if set(block.keys()) != set(FIELDS):
        problems.append(f"{trade}: fields {sorted(block.keys())} != {sorted(FIELDS)}")
        return problems
    feats = block.get("features")
    if not isinstance(feats, list) or len(feats) != 3:
        problems.append(f"{trade}: features must be a list of 3")
    strings = {k: v for k, v in block.items() if k != "features"}
    strings.update({f"features[{i}]": f for i, f in enumerate(feats or [])})
    for key, text in strings.items():
        text = (text or "").strip()
        if not text:
            problems.append(f"{trade}.{key}: empty")
            continue
        if not re.search(r"[А-Яа-яЁё]", text):
            problems.append(f"{trade}.{key}: no cyrillic")
        words = set(re.findall(r"[A-Za-z][A-Za-z\-]+", text.lower()))
        leaked = sorted(words - LATIN_OK)
        if leaked:
            problems.append(f"{trade}.{key}: latin leak {leaked}")
        if len(text) > 700:
            problems.append(f"{trade}.{key}: too long ({len(text)})")
    if len((block.get("meta_description") or "")) > 165:
        problems.append(f"{trade}.meta_description: >165 chars")
    if len((block.get("title") or "")) > 120:
        problems.append(f"{trade}.title: >120 chars")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", default="drafts/TradeNation.ru.json")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    client = OpenAI(api_key=cfg["llm"]["api_key"])
    model = cfg["llm"].get("model", "gpt-4o-mini")

    msgs = os.path.join(args.repo, "src", "messages")
    nl = json.load(open(os.path.join(msgs, "nl.json")))
    ru = json.load(open(os.path.join(msgs, "ru.json")))
    nl_nation = nl.get("TradeNation") or {}
    ru_trades = ru.get("Trades") or {}

    result, all_problems = {}, []
    for trade in TRADES:
        label = (ru_trades.get(trade) or {}).get("label") or trade
        intro = (ru_trades.get(trade) or {}).get("intro") or ""
        src = nl_nation.get(trade)
        if not src:
            print(f"  skip {trade}: no NL TradeNation source", file=sys.stderr)
            continue

        user = f"""Напишите блок TradeNation для направления «{label}» компании
Constructief — это страновая (без конкретного города) посадочная страница
/ru/diensten/onderaannemer-{trade}.

Тема направления по-русски: {intro}

Нидерландский источник (для смысла, НЕ для копирования — сохраните ту же
фактическую мысль, но напишите естественным русским языком):
{json.dumps(src, ensure_ascii=False, indent=1)}

Верните JSON ровно с такими полями (и только с ними):
{{
  "title": "заголовок H1, 50-90 знаков, без названия компании",
  "meta_description": "мета-описание, 120-155 знаков, с выгодой и призывом",
  "intro": "2-4 предложения (300-600 знаков): что делает бригада, для кого, какие документы",
  "section_title": "заголовок раздела, 30-60 знаков",
  "features": ["три пункта, каждый 90-200 знаков: конкретные работы бригады"],
  "cta_title": "короткий призыв, до 40 знаков",
  "cta_desc": "1-2 предложения: что прислать (объём, сроки, дата старта) и что получите",
  "cta_button": "текст кнопки, 2-4 слова"
}}

Требования:
- полностью по-русски; латиницей только Constructief, A1, Limosa, Checkinatwork;
- без нидерландских и французских слов и без слагов;
- обращение «вы», без восклицательных знаков и рекламных штампов;
- уместно упомяните 14-дневную пробную неделю, личную проверку специалистов и
  оплату за результат;
- не выдумывайте статистику, названия компаний и проекты."""
        print(f"  {trade} ...", flush=True)
        block = json.loads(chat(client, model, RU_STYLE, user))
        problems = validate(trade, block)
        if problems:
            all_problems += problems
            print("    !! " + "; ".join(problems), file=sys.stderr)
        result[trade] = block

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(result, open(args.out, "w"), ensure_ascii=False, indent=2)
    print(f"\nwrote {args.out}: {len(result)} trade blocks")
    if all_problems:
        print("PROBLEMS:")
        for p in all_problems:
            print("  -", p)
        sys.exit(1)
    print("all blocks: RU-only, correct fields, 3 features, lengths in range")


if __name__ == "__main__":
    main()
