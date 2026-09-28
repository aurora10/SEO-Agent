"""Generate the RU TradeCity block (trade x city) for constructief-bouw.be.

Why: /ru/diensten/onderaannemer-{trade}-{city} pages carry only per-CITY copy,
so the 6 trades in the same city render the same two paragraphs -> Google files
them as near-duplicates ("Discovered - currently not indexed"). RU is the
priority locale, so it needs the same per-trade-per-city uniqueness nl/fr
already got.

Source of truth for each pair's angle = the shipped NL TradeCity paragraph, so
the Russian text makes the same factual claims (no new statistics, no invented
project names). The RU city description + the RU Trades labels are passed in so
terminology matches the rest of the Russian site.

Output: drafts/TradeCity.ru.json  ->  {"<trade>": {"<city>": "<paragraph>"}}

Usage:
  python scripts/gen_ru_trade_city.py --repo "/path/to/constructief" [--out drafts/TradeCity.ru.json]
"""
import argparse
import json
import os
import re
import sys

import yaml
from openai import OpenAI

TRADES = ["gevel", "renovatie", "beton", "dak", "ruwbouw", "interieur"]
CITIES = ["antwerpen", "gent", "leuven", "brussel"]
CITY_RU = {
    "antwerpen": "Антверпен",
    "gent": "Гент",
    "leuven": "Лёвен",
    "brussel": "Брюссель",
}

RU_STYLE = """Вы пишете для Constructief (constructief-bouw.be) — бельгийского
B2B-поставщика ПРОВЕРЕННЫХ ЛЕГАЛЬНЫХ строительных бригад (в основном
специалисты из Восточной Европы) для генеральных подрядчиков. Модель дохода:
finders fee. Только B2B, не трудоустройство частных лиц.

Стиль: русский язык, профессионально, по делу. Форма «вы». Без восклицательных
знаков, без рекламных штампов. Факты важнее прилагательных. Короткие предложения.
USP, которые нужно уместно упоминать: личная проверка каждого специалиста,
14-дневная пробная неделя, документы A1/Limosa/Checkinatwork полностью
оформлены, оплата за результат, «никаких стопок резюме».

Никогда не придумывайте статистику, названия компаний, имена и проекты.
Верните ТОЛЬКО валидный JSON."""


def chat(client, model, system, user, max_completion_tokens=2500):
    for attempt in range(3):
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_completion_tokens=max_completion_tokens)
        content = (r.choices[0].message.content or "").strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.S).strip()
        if content:
            return content
        print(f"    empty content, retry {attempt + 1}/3", file=sys.stderr)
    raise RuntimeError("LLM returned empty content 3x")


def norm(s):
    return re.sub(r"[^а-яёa-z0-9 ]", "", s.lower())


def trigrams(s):
    s = norm(s)
    return {s[i:i + 3] for i in range(len(s) - 2)}


def jaccard(a, b):
    ta, tb = trigrams(a), trigrams(b)
    return len(ta & tb) / max(1, len(ta | tb))


def validate(trade, data):
    """Reject anything that would ship as broken or duplicated RU copy."""
    problems = []
    for city in CITIES:
        text = (data.get(city) or "").strip()
        if not text:
            problems.append(f"{trade}.{city}: empty")
            continue
        if not (180 <= len(text) <= 900):
            problems.append(f"{trade}.{city}: length {len(text)}")
        if any(ch in text for ch in "[]{}"):
            problems.append(f"{trade}.{city}: stray markup")
        if re.search(r"\b(renovatie|gevel|ruwbouw|interieur|beton|dak)\b", text, re.I):
            problems.append(f"{trade}.{city}: dutch slug leaked")
        if not re.search(r"[А-Яа-яЁё]", text):
            problems.append(f"{trade}.{city}: no cyrillic")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--repo", required=True, help="path to the constructief checkout")
    ap.add_argument("--out", default="drafts/TradeCity.ru.json")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    client = OpenAI(api_key=cfg["llm"]["api_key"])
    model = cfg["llm"].get("model", "gpt-4o-mini")

    msgs = os.path.join(args.repo, "src", "messages")
    nl = json.load(open(os.path.join(msgs, "nl.json")))
    ru = json.load(open(os.path.join(msgs, "ru.json")))
    nl_tc = nl.get("TradeCity") or {}
    ru_cities = ru.get("CitiesSeo") or {}
    ru_trades = ru.get("Trades") or {}

    result, all_problems = {}, []
    for trade in TRADES:
        ref = nl_tc.get(trade) or {}
        label = (ru_trades.get(trade) or {}).get("label") or trade
        intro = (ru_trades.get(trade) or {}).get("intro") or ""
        city_lines = "\n".join(
            f'- {CITY_RU[c]} ({c}): {(ru_cities.get(c) or "").strip()}'
            for c in CITIES)
        ref_lines = "\n".join(
            f'- {CITY_RU[c]}: {ref.get(c, "(geen)")}' for c in CITIES)

        user = f"""Напишите ОДИН уникальный абзац (2-4 предложения, 250-600 знаков) для
каждого города — для направления «{label}» компании Constructief.

Тема направления по-русски: {intro}

Города:
{city_lines}

Это перевод-адаптация уже опубликованного нидерландского текста для этой пары
«направление x город». Сохраните ТУ ЖЕ фактическую мысль (что именно строится и
ремонтируется в этом городе, какие специалисты нужны, чем занята бригада), но
напишите естественным русским языком — не копируйте структуру нидерландских фраз
и не переводите дословно.

Нидерландский источник (для смысла, НЕ для копирования):
{ref_lines}

Требования:
- для каждого города — своя специфика рынка именно этого города; абзацы не должны
  быть взаимозаменяемыми;
- где уместно, упомяните A1, Limosa, Checkinatwork, 14-дневную пробную неделю,
  личную проверку специалистов;
- не выдумывайте статистику, названия компаний и проектов;
- полностью по-русски, без нидерландских слов и без слагов;
- обращение «вы».

Верните JSON ровно такого вида:
{{"{CITIES[0]}": "<абзац>", "{CITIES[1]}": "<абзац>", "{CITIES[2]}": "<абзац>", "{CITIES[3]}": "<абзац>"}}"""

        print(f"  {trade} ...", flush=True)
        data = json.loads(chat(client, model, RU_STYLE, user))
        data = {c: (data.get(c) or "").strip() for c in CITIES}
        probs = validate(trade, data)
        if probs:
            all_problems += [f"{p}" for p in probs]
            print("    !! " + "; ".join(probs), file=sys.stderr)
        result[trade] = data

    # Same city across trades must not read the same either.
    for city in CITIES:
        texts = [(t, result[t][city]) for t in TRADES if result.get(t, {}).get(city)]
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                sim = jaccard(texts[i][1], texts[j][1])
                if sim > 0.55:
                    all_problems.append(
                        f"similar {texts[i][0]}/{texts[j][0]} @{city}: {sim:.2f}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(result, open(args.out, "w"), ensure_ascii=False, indent=2)
    pairs = sum(len(v) for v in result.values())
    print(f"\nwrote {args.out}: {len(result)} trades x {len(CITIES)} cities = {pairs} paragraphs")
    if all_problems:
        print("PROBLEMS:")
        for p in all_problems:
            print("  -", p)
        sys.exit(1)
    print("all paragraphs unique per city and per trade, RU-only, lengths in range")


if __name__ == "__main__":
    main()
