"""Agent 3 v2: data-driven LLM content writer.

Every draft is traceable to market-analysis.json:
  keyword(s) -> volume, your rank, top competitors -> page action.

Draft types (only these; nothing else):
  1. REWRITE   werkgevers hero/meta            <- core keywords, not ranking
  2. TRADE     Trades.<trade> block            <- trade keywords, page missing
  3. CITY      CitiesSeo/CityRegio.<city>      <- city keyword has demand,
                                                   description missing
  4. RU        worker-facing RU copy           <- (later; not in this run)

Output: drafts/*.json + drafts/report.md (every draft lists its evidence).
"""
import argparse
import json
import os
import re
import sys

import yaml
from openai import OpenAI

STYLE = """You write for Constructief (constructief-bouw.be): B2B supplier of
PRE-VETTED LEGAL construction teams (ploegen), mainly Eastern European
tradespeople, for general contractors (hoofdaannemers). Revenue: finders fee.

Voice: Dutch, professional, direct. "u"-form. No exclamation marks.
Facts over adjectives. Short sentences.
USPs to weave in where relevant: persoonlijke screening (wij spreken iedere
vakman), 14-daagse testweek, A1/Limosa/Checkinatwork volledig geregeld,
vergoeding gekoppeld aan resultaat, "geen stapels CV's".
Never invent statistics, names, or project references.
Return ONLY valid JSON."""


FR_STYLE = """You write for Constructief (constructief-bouw.be): a Belgian B2B
provider of PRE-VETTED, LEGAL construction teams (équipes), mostly Eastern
European tradespeople, supplied to general contractors (maîtres d'œuvre).
Revenue model: finders fee. B2B only, not recruitment for individuals.

Voice: French (fr-BE), professional, direct. "vous"-form. No exclamation marks.
Facts over adjectives. Short sentences.
USPs to weave in: screening personnel, période d'essai de 14 jours,
A1/Limosa/Checkinatwork gérés, rémunération au résultat, "pas de piles de CV".
Never invent statistics, names or project references.
Return ONLY valid JSON."""


def chat(client, model, system, user, max_completion_tokens=2500):
    for attempt in range(3):
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_completion_tokens=max_completion_tokens)
        # Reasoning models occasionally return empty content or fence the JSON.
        content = (r.choices[0].message.content or "").strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.S).strip()
        if content:
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                if attempt == 2:
                    raise
                continue  # unparsable; retry (model wrapped it in prose)
        if attempt == 2:
            raise RuntimeError("LLM returned empty content after retries")
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------- drafts

def draft_werkgevers_rewrite(client, model, evidence):
    """Rewrite EmployersPage.title/subtitle + Metadata for core keywords."""
    kws = ", ".join(f'"{k["keyword"]}" (vol {k["volume"]})' for k in evidence)
    user = f"""Rewrite the /nl/werkgevers page targeting these real keywords:
{kws}

Current values (improve, keep JSON structure identical):
{{
  "EmployersPage.title": "... (H1; include the highest-volume keyword) ...",
  "EmployersPage.subtitle": "... (1-2 sentences: ploegen + legal USPs) ...",
  "Metadata.title": "... (<=60 chars; highest-volume keyword up front) ...",
  "Metadata.description": "... (<=155 chars; keyword + USP + CTA) ..."
}}
Return the JSON object only."""
    return chat(client, model, STYLE, user)


def draft_trade(client, model, trade, evidence):
    kws = ", ".join(f'"{k["keyword"]}" (vol {k["volume"]})' for k in evidence)
    schema = json.dumps({
        "label": "...", "title": "... in {city}", "intro": "...2 zinnen, {city}...",
        "features": ["...", "...", "..."],
        "cta_title": "... {city} ...", "cta_desc": "...",
        "cta_button": "...", "section_title": "Wat uw {label.lower()}ploeg doet in {city}",
    }, ensure_ascii=False)
    user = f"""Write a Trades.{trade} block for the trade "{trade}".
Target keywords (from live market data): {kws}
Schema (match exactly, keep {{city}} placeholders):
{schema}
features = 3 specific services this trade's teams deliver.
Return the JSON object only."""
    return chat(client, model, STYLE, user)


def draft_city(client, model, city, province, evidence):
    kws = ", ".join(f'"{k["keyword"]}" (vol {k["volume"]})' for k in evidence)
    user = f"""City "{city}" (province {province}) has search demand: {kws}

Write two JSON fields like the existing CitiesSeo / CityRegio entries:
1. "description": 2-3 sentences on the local construction market and which
   trades are in demand there. Specific, plausible, no invented project names.
2. "context": one sentence summarising market + sought profiles.
Return {{"description": "...", "context": "..."}} only."""
    return chat(client, model, STYLE, user)


# ---------------------------------------------------------- orchestrator

def gen_trade_nation(client, model, trade, keywords, lang):
    """Nation-wide (city-agnostic) landing copy: TradeNation.<trade> for one lang."""
    # French trade labels so the FR page never uses the Dutch slug in copy.
    FR_LABEL = {
        "beton": "béton", "dak": "toiture", "gevel": "façade",
        "interieur": "plâtrerie & finition", "renovatie": "rénovation",
        "ruwbouw": "gros œuvre",
    }
    if lang == "nl":
        style = STYLE
        lang_hint = "Dutch"
        trade_term = trade
        keyword_hint = f'onderaannemer {trade}'
    else:
        style = FR_STYLE
        lang_hint = "French (fr-BE)"
        trade_term = FR_LABEL.get(trade, trade)
        keyword_hint = f'sous-traitance {trade_term}'
    kws = ", ".join(keywords[:6]) or "(no volume keywords — use your judgement)"
    schema = json.dumps({
        "title": "...H1 <=60 chars, includes the trade + action word...",
        "meta_description": "...<=155 chars, keyword + USP...",
        "intro": "...2-3 sentences; NATION-WIDE value proposition; no city...",
        "section_title": "...(e.g. 'Wat onze ploegen leveren')...",
        "features": ["...", "...", "..."],
        "cta_title": "...", "cta_desc": "...", "cta_button": "...",
    }, ensure_ascii=False)
    user = f"""Write the NATION-WIDE landing page (no city) for the trade "{trade}"
of Constructief, in {lang_hint}. This is the country-level hub page for the trade.

Target keywords (from market data): {kws}

Language rules (MUST follow):
- Write entirely in {lang_hint}. Never use a Dutch or non-{lang_hint} word.
- Refer to the trade as "{trade_term}" (in {lang_hint}) — never the slug "{trade}".
- Target the keyword '{keyword_hint}'.

Schema (fill every field; NEVER use a city placeholder):
{schema}
- title = H1 <=60 chars, includes "{trade_term}" in {lang_hint}
- meta_description <=155 chars
- intro = 2-3 sentences, national value proposition
- features = 3 specific services this trade's teams deliver nation-wide
Return the JSON object only."""
    r = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": style},
                  {"role": "user", "content": user}],
        max_completion_tokens=2500)
    return json.loads(r.choices[0].message.content)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="list would-be drafts without calling the LLM or writing files")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    dry_run = args.dry_run
    if not dry_run:
        client = OpenAI(api_key=cfg["llm"]["api_key"])
    model = cfg["llm"].get("model", "gpt-4o-mini")

    repo = args.repo
    nl = json.load(open(os.path.join(repo, "src/messages", "nl.json")))

    # ground truth: which trades have routes
    cc_path = os.path.join(repo, "src/data/cityContent.ts")
    live_trades = set()
    if os.path.exists(cc_path):
        m = re.search(r"flagshipTrades\s*=\s*\[([^\]]+)\]", open(cc_path).read())
        if m:
            live_trades = set(re.findall(r"'([^']+)'", m.group(1)))

    analysis = json.load(open(cfg.get("market_analysis", "data/market-analysis.json")))
    kws = analysis["keywords"]
    commercial_kw = [k["keyword"] for k in kws if k["volume"] > 0][:8]
    by_cluster = {}
    for k in kws:
        if k["volume"] > 0:
            by_cluster.setdefault(k["cluster"], []).append(k)

    drafts = {}
    targets = []
    rationale = ["# Agent 3 — data-driven drafts\n",
                 f"Model: {model} | source: {cfg.get('market_analysis')}\n"]

    # 1) werkgevers rewrite — evidence: core keywords with volume, not ranking
    core = by_cluster.get("core", [])
    if core:
        unr = [k for k in core if (k.get("your_rank") or 99) > 10]
        if unr:
            if dry_run:
                targets.append(f"werkgevers.json ({len(unr)} unranked core kws)")
                print(f"[dry-run] would generate: werkgevers.json ({len(unr)} unranked core kws)")
            else:
                print(f"LLM: werkgevers rewrite ({len(unr)} unraked core kws)")
                drafts["werkgevers.json"] = draft_werkgevers_rewrite(client, model, unr)
                rationale += [
                    "## werkgevers.json — rewrite EmployersPage + Metadata",
                    "Evidence (you rank >10 or not at all):",
                    *[f"- `{k['keyword']}` vol {k['volume']}, rank {k.get('your_rank') or '-'}"
                      for k in unr], ""]

    # 2) trade pages — evidence: trade cluster volume > 0 and route exists
    for cluster, items in by_cluster.items():
        if not cluster.startswith("trade:"):
            continue
        trade = cluster.split(":", 1)[1]
        if trade in nl.get("Trades", {}):
            continue  # already has copy
        if live_trades and trade not in live_trades:
            print(f"  skip Trades.{trade}: no route in flagshipTrades")
            continue
        if dry_run:
            targets.append(f"Trades__{trade}.json")
            print(f"[dry-run] would generate: Trades__{trade}.json")
            continue
        print(f"LLM: Trades.{trade} (vol {sum(i['volume'] for i in items)})")
        drafts[f"Trades__{trade}.json"] = {"Trades": {trade: draft_trade(
            client, model, trade, items)}}
        rationale += [f"## Trades__{trade}.json",
                      *[f"- `{i['keyword']}` vol {i['volume']}, rank {i.get('your_rank') or '-'}"
                        for i in items], ""]

    # 3) city content — evidence: city:<c> cluster with volume, copy missing
    for cluster, items in by_cluster.items():
        if not cluster.startswith("city:"):
            continue
        parts = cluster.split(":")
        if len(parts) != 2:
            continue  # skip city:trade combos here
        city = parts[1]
        if city in nl.get("CitiesSeo", {}):
            continue
        prov = "België/Nederland"  # cities.ts holds it; agent can look up
        if dry_run:
            targets.append(f"city__{city}.json")
            print(f"[dry-run] would generate: city__{city}.json")
            continue
        print(f"LLM: CitiesSeo.{city} (vol {sum(i['volume'] for i in items)})")
        out = draft_city(client, model, city, prov, items)
        drafts[f"city__{city}.json"] = {
            "CitiesSeo": {city: out["description"]},
            "CityRegio": {city: {"context": out["context"]}},
        }
        rationale += [f"## city__{city}.json",
                      *[f"- `{i['keyword']}` vol {i['volume']}, rank {i.get('your_rank') or '-'}"
                        for i in items], ""]

    # 4) Nation-wide trade landing copy (TradeNation.<trade>) for nl + fr.
    # These populate the base trade pages /diensten/onderaannemer-{trade} with
    # UNIQUE country-level copy (not a reworded city page) + city links.
    want_nation = set(live_trades) if live_trades else {
        "gevel", "renovatie", "beton", "dak", "ruwbouw", "interieur"}
    for lang in ("nl", "fr"):
        msg_path = os.path.join(repo, "src/messages", f"{lang}.json")
        msg = json.load(open(msg_path)) if os.path.exists(msg_path) else {}
        have_nation = set((msg.get("TradeNation") or {}).keys())
        for trade in sorted(want_nation - have_nation):
            trade_kws = [k["keyword"] for k in by_cluster.get(f"trade:{trade}", [])][:6] \
                or commercial_kw[:6]
            if dry_run:
                targets.append(f"TradeNation/{trade}.json ({lang})")
                print(f"[dry-run] would generate: TradeNation/{trade}.json ({lang})")
                continue
            print(f"LLM: TradeNation.{trade} ({lang})")
            block = gen_trade_nation(client, model, trade, trade_kws, lang)
            drafts[f"TradeNation_{trade}__{lang}.json"] = {"TradeNation": {trade: block}}

    if dry_run:
        print(f"\nDry run: {len(targets)} draft(s) would be generated "
              f"(no LLM calls, no files written).")
        print("Run without --dry-run to generate them.")
        return

    os.makedirs("drafts", exist_ok=True)
    for name, content in drafts.items():
        with open(os.path.join("drafts", name), "w") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
        print(f"  wrote drafts/{name}")
    with open("drafts/report.md", "w") as f:
        f.write("\n".join(rationale))
    print(f"\n{len(drafts)} drafts, evidence in drafts/report.md")

    # notify: email you the drafts for review (nothing goes live automatically)
    if drafts and cfg.get("notify") and not args.dry_run:
        import notify
        notify.send_review_email(cfg, "drafts", "\n".join(rationale))
    elif args.dry_run:
        print("(dry-run: email suppressed)")


if __name__ == "__main__":
    main()
