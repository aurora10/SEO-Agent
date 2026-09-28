"""Agent 3 v2: data-driven LLM content writer.

Every draft is traceable to market-analysis.json:
  keyword(s) -> volume, your rank, top competitors -> page action.

Draft types (only these; nothing else):
  1. REWRITE   werkgevers hero/meta            <- core keywords, not ranking
  2. TRADE     Trades.<trade> block            <- trade keywords, page missing
  3. CITY      CitiesSeo/CityRegio.<city>      <- city keyword has demand,
                                                   description missing
  4. RU        worker-facing RU copy           <- (later; not in this run)

Every draft is language-pure:
  - keyword evidence is filtered to the draft's own language
    (k["lang"] == lang) in every section, so FR keywords can never be woven
    into NL copy again;
  - the finished draft must pass assert_language() before it is written: a
    draft containing another language's words is EXCLUDED and the run exits
    non-zero (silence was the failure mode).

A small ledger (data/agent3_applied.json) stops the same slot from being
re-drafted every week while its draft is still waiting for a human merge.

Output: drafts/*.json + drafts/report.md (every draft lists its evidence).
"""
import argparse
import datetime as dt
import hashlib
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


RU_STYLE = """Вы пишете для Constructief (constructief-bouw.be) — бельгийского
B2B-поставщика ПРОВЕРЕННЫХ ЛЕГАЛЬНЫХ строительных бригад (в основном специалисты
из Восточной Европы) для генеральных подрядчиков. Модель дохода: finders fee.
Только B2B, не трудоустройство частных лиц.

Стиль: русский язык, профессионально, по делу. Форма «вы». Без восклицательных
знаков, без рекламных штампов. Факты важнее прилагательных. Короткие предложения.
USP, которые нужно уместно упоминать: личная проверка специалистов, 14-дневная
пробная неделя, документы A1/Limosa/Checkinatwork полностью оформлены, оплата за
результат, «никаких стопок резюме».

Никогда не выдумывайте статистику, названия компаний, имена и проекты.
Латиницей пишите только: Constructief, A1, Limosa, Checkinatwork, B2B.
Верните ТОЛЬКО валидный JSON."""


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


# ------------------------------------------------------- language guard
#
# Drafts ship straight into src/messages/{nl,fr,ru}.json, so one stray foreign
# word is shipped-content bug (it happened: FR core keywords ended up in the
# Dutch werkgevers hero). Every draft is validated here BEFORE it is written.

# Matched case-insensitively on word boundaries. "sous-trait" is a stem on
# purpose: it must also catch "sous-traitance" and "sous-traitant".
FOREIGN_MARKERS = {
    "nl": ["sous-trait", "bâtiment", "batiment", "équipe", "equipe", "avec",
           "pour", "dans le", "rénovation", "renovation", "façade", "facade",
           "toiture", "gros œuvre", "gros oeuvre", "chantier", "travaux",
           "entrepreneur"],
    "fr": ["wij", "uw", "onze", "ploeg", "vakman", "vakmannen", "geen",
           "stapels", "bouwpersoneel", "detachering", "onderaannemer",
           "werkgevers", "gevel", "ruwbouw", "interieur", "dakwerken"],
}

_PREFIX_MARKERS = {"sous-trait"}

# What matters for RU copy is Dutch/French leakage, not Latin script per se:
# proper nouns, acronyms and technical terms are perfectly legitimate inside
# Russian copy (EPDM, Zuidas, Brainport, "Leidsche Rijn", URL, Email, brand
# names), so flagging every non-allowlisted Latin word rejected correct content.
FOREIGN_MARKERS["ru"] = FOREIGN_MARKERS["nl"] + FOREIGN_MARKERS["fr"]

# Below this length a string may legitimately be a label, a placeholder or an
# email address ("Email", "info@company.ru"), so the Cyrillic requirement only
# applies to actual prose.
MIN_CYRILLIC_LEN = 25

# Legitimately foreign-looking text: {city}-style placeholders, URLs, the site
# domain, file names and slug/path tokens (e.g. /nl/werkgevers, sous-traitance-…
# appears in URLs and slugs, never in copy we want flagged by it).
EXEMPT_PATTERNS = [
    re.compile(r"\{[^{}]*\}"),
    re.compile(r"https?://\S+|www\.\S+", re.I),
    re.compile(r"\S*constructief-bouw\.be\S*", re.I),
    re.compile(r"\S+\.(?:json|tsx?|jsx?|mjs|cjs|md|markdown|html?|ya?ml|py|txt"
               r"|svg|png|jpe?g|webp|css)\b", re.I),
    re.compile(r"(?:\b[\w.-]+/)+[\w.-]+"),
]


class LanguageGuardError(AssertionError):
    """Raised when a draft contains copy in the wrong language."""

    def __init__(self, lang, problems):
        self.lang = lang
        self.problems = list(problems)
        super().__init__(
            f"{lang} draft has {len(self.problems)} foreign-language issue(s): "
            + " | ".join(self.problems[:5]))


def _strip_exempt(text):
    """Remove text that may legitimately contain foreign words."""
    for pat in EXEMPT_PATTERNS:
        text = pat.sub(" ", text)
    return text


def _snippet(text, match, width=45):
    """Short quoted window around a match, for the warning message."""
    a = max(0, match.start() - width)
    b = min(len(text), match.end() + width)
    body = " ".join(text[a:b].split())
    return f"{'…' if a > 0 else ''}{body}{'…' if b < len(text) else ''}"


def _iter_strings(obj, path=""):
    """Walk every string VALUE in a draft (keys are structural, never copy)."""
    if isinstance(obj, str):
        yield path or "<value>", obj
    elif isinstance(obj, dict):
        for key, val in obj.items():
            yield from _iter_strings(val, f"{path}.{key}" if path else str(key))
    elif isinstance(obj, (list, tuple)):
        for i, val in enumerate(obj):
            yield from _iter_strings(val, f"{path}[{i}]")


def _marker_pattern(marker):
    escaped = re.escape(marker)
    if marker in _PREFIX_MARKERS:
        return re.compile(r"\b" + escaped, re.I)
    return re.compile(r"\b" + escaped + r"\b", re.I)


def _marker_hits(raw, clean, lang):
    """[(marker, snippet)] for foreign markers, matched on the cleaned text."""
    hits = []
    for marker in FOREIGN_MARKERS.get(lang, []):
        pat = _marker_pattern(marker)
        if not pat.search(clean):
            continue
        m = pat.search(raw)
        hits.append((marker, _snippet(raw, m) if m else f"…{marker}…"))
    return hits


def _has_cyrillic(text):
    return bool(re.search(r"[\u0400-\u04FF]", text))


def language_violations(payload, lang):
    """Human-readable language-guard violations; [] means the draft is clean."""
    if lang not in FOREIGN_MARKERS and lang != "ru":
        return []
    problems = []
    for path, raw in _iter_strings(payload):
        text = (raw or "").strip()
        if not text:
            continue
        clean = _strip_exempt(text)
        if lang == "ru":
            # Russian prose must actually be Russian...
            if len(text) >= MIN_CYRILLIC_LEN and not _has_cyrillic(text):
                problems.append(
                    f"{path}: no Cyrillic script in RU copy (\"{_snippet(text)}\")")
            # ...and must not have Dutch/French keywords woven into it.
            for marker, snippet in _marker_hits(text, clean, lang):
                problems.append(
                    f"{path}: Dutch/French marker '{marker}' in RU copy "
                    f"(\"{snippet}\")")
            continue
        for marker, snippet in _marker_hits(text, clean, lang):
            problems.append(f"{path}: foreign marker '{marker}' in \"{snippet}\"")
    return problems


def assert_language(payload, lang):
    """Validate a draft's language; raise LanguageGuardError when it is mixed."""
    problems = language_violations(payload, lang)
    if problems:
        raise LanguageGuardError(lang, problems)
    return True


# ---------------------------------------------------------------- ledger
#
# One entry per draft slot so a slot is drafted ONCE. site_hash is the hash of
# the exact message slice the draft would replace, taken BEFORE drafting:
#   same hash  -> draft is still waiting for a human merge: don't re-draft
#   other hash -> the site moved on (draft was merged): mark applied + skip

LEDGER_PATH = os.path.join("data", "agent3_applied.json")

# slot -> draft file name (for the report)
SLOT_FILES = {
    "werkgevers_nl": "werkgevers.json",
    "werkgevers_fr": "werkgevers__fr.json",
}


def canonical_hash(obj):
    """sha256 of the canonical JSON of a site slice or object."""
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_ledger(path=LEDGER_PATH):
    """The slot ledger; a missing or corrupt file is treated as empty."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        print(f"  warn: ignoring unreadable ledger {path}: {e}")
        return {}
    if not isinstance(data, dict):
        print(f"  warn: ledger {path} is not an object; starting empty")
        return {}
    return data


def save_ledger(ledger, path=LEDGER_PATH):
    """Write the ledger (temp file + replace) so a crash cannot corrupt it."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def write_text(path, text):
    """Same atomic-ish write for the human-readable report."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def werkgevers_slice(msg):
    """The exact message slice a werkgevers draft replaces (nl or fr file)."""
    return {k: msg[k] for k in ("EmployersPage", "Metadata") if k in msg} or ""


def subtree_slice(msg, top, key):
    """{'<top>': {'<key>': value}} when the subtree exists, else '' (spec)."""
    sub = msg.get(top)
    if isinstance(sub, dict) and key in sub:
        return {top: {key: sub[key]}}
    return ""


def city_slice(msg, city):
    """CitiesSeo + CityRegio entry for one city (what a city draft replaces)."""
    out = {}
    for top in ("CitiesSeo", "CityRegio"):
        sub = msg.get(top)
        if isinstance(sub, dict) and city in sub:
            out[top] = {city: sub[city]}
    return out or ""


def slot_decision(ledger, slot, site_hash, redraft=(), seen=None):
    """('draft' | 'skip', reason) for one slot, honouring the ledger."""
    if seen is not None:
        seen.add(slot)
    if slot in redraft:
        return "draft", "forced by --redraft"
    entry = ledger.get(slot)
    if not isinstance(entry, dict) or not entry:
        return "draft", ""
    if entry.get("site_hash") == site_hash:
        return "skip", "draft still pending merge, not re-drafting"
    # The target slice changed since we drafted it -> that draft was merged.
    entry["status"] = "applied"
    return "skip", "already applied"


# ------------------------------------------------- labelled trade/city names

TRADE_LABEL_FR = {
    "beton": "béton", "dak": "toiture", "gevel": "façade",
    "interieur": "plâtrerie & finition", "renovatie": "rénovation",
    "ruwbouw": "gros œuvre",
}

TRADE_LABEL_RU = {
    "beton": "Бетонные работы", "dak": "Кровля", "gevel": "Фасад",
    "interieur": "Внутренняя отделка", "renovatie": "Реновация",
    "ruwbouw": "Черновые/каркасные работы",
}

CITY_RU = {
    "antwerpen": "Антверпен", "gent": "Гент",
    "leuven": "Лёвен", "brussel": "Брюссель",
}


def lang_profile(lang, trade):
    """(system prompt, language hint, trade term) for one draft language."""
    if lang == "fr":
        return FR_STYLE, "French (fr-BE)", TRADE_LABEL_FR.get(trade, trade)
    if lang == "ru":
        return RU_STYLE, "Russian", TRADE_LABEL_RU.get(trade, trade)
    return STYLE, "Dutch", trade


def evidence_for(items, lang, fallback):
    """Keyword evidence for a draft written in `lang`.

    Never mixes languages: a cluster with no keywords in the draft's language
    falls back to that language's OWN commercial keywords only.
    """
    own = [k for k in items if k.get("lang") == lang]
    if own:
        return own
    return [k for k in fallback if k.get("lang") == lang]


def keyword_names(evidence):
    return [k["keyword"] for k in evidence]


# ---------------------------------------------------------------- drafts

def draft_werkgevers_rewrite(client, model, evidence, lang):
    """Rewrite EmployersPage.title/subtitle + Metadata for core keywords.

    `lang` picks the system prompt (STYLE for nl, FR_STYLE for fr) and the page
    being targeted. `evidence` must already be filtered to that same language:
    `core` holds both Dutch and French keywords.
    """
    style = FR_STYLE if lang == "fr" else STYLE
    teams = "équipes" if lang == "fr" else "ploegen"
    page = "/fr/werkgevers (fr.json)" if lang == "fr" else "/nl/werkgevers (nl.json)"
    language = "French (fr-BE)" if lang == "fr" else "Dutch"
    kws = ", ".join(f'"{k["keyword"]}" (vol {k["volume"]})' for k in evidence)
    user = f"""Rewrite the {page} page targeting these real keywords:
{kws}

Current values (improve, keep JSON structure identical):
{{
  "EmployersPage.title": "... (H1; include the highest-volume keyword) ...",
  "EmployersPage.subtitle": "... (1-2 sentences: {teams} + legal USPs) ...",
  "Metadata.title": "... (<=60 chars; highest-volume keyword up front) ...",
  "Metadata.description": "... (<=155 chars; keyword + USP + CTA) ..."
}}
Write entirely in {language}; never use a word from another language.
Return the JSON object only."""
    return chat(client, model, style, user)


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
    # Localised trade labels so the page never uses the Dutch slug in copy.
    style, lang_hint, trade_term = lang_profile(lang, trade)
    if lang == "fr":
        keyword_hint = f"sous-traitance {trade_term}"
    elif lang == "ru":
        keyword_hint = f"бригада {trade_term.lower()}"
    else:
        keyword_hint = f"onderaannemer {trade}"
    extra_rule = ""
    if lang == "ru":
        extra_rule = ("- Russian is written in Cyrillic. Latin script only for: "
                      "Constructief, A1, Limosa, Checkinatwork, B2B.\n")
    kws = ", ".join(keywords[:6]) or "(no volume keywords — use your judgement)"
    schema = json.dumps({
        "title": "...H1 <=60 chars, includes the trade + action word...",
        "meta_description": "...<=155 chars, keyword + USP...",
        "intro": "...2-3 sentences; NATION-WIDE value proposition; no city...",
        "section_title": "...short heading for the services list...",
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
{extra_rule}
Schema (fill every field; NEVER use a city placeholder):
{schema}
- title = H1 <=60 chars, includes "{trade_term}" in {lang_hint}
- meta_description <=155 chars
- intro = 2-3 sentences, national value proposition
- features = 3 specific services this trade's teams deliver nation-wide
Return the JSON object only."""
    return chat(client, model, style, user)


def gen_trade_city(client, model, trade, city_names, keywords, lang):
    """Unique paragraph per city for one trade (the trade x city intersection).

    These make each /diensten/onderaannemer-{trade}-{city} page genuinely
    distinct instead of the same trade copy with only {city} swapped — which is
    why Google reported those pages as near-duplicate ("Discovered - currently
    not indexed").
    """
    style, lang_hint, trade_term = lang_profile(lang, trade)

    kws = ", ".join(keywords[:6]) or "(none)"
    if lang == "ru":
        # Slugs stay the JSON keys; the paragraph itself names the city in Russian.
        cities = ", ".join(f"{c} ({CITY_RU.get(c, c)})" for c in city_names)
        extra = (f"\nThe paragraph text must be Russian (Cyrillic); the JSON keys "
                 f"stay exactly the slugs listed above. Latin script only for: "
                 f"Constructief, A1, Limosa, Checkinatwork, B2B.\n")
    else:
        cities = ", ".join(city_names)
        extra = ""
    user = f"""Write ONE short unique paragraph (2-3 sentences) per city for the trade
"{trade_term}" of Constructief, in {lang_hint}.

Cities: {cities}

For EACH city, mention something specific and plausible about that city's market
for this trade (what is built/renovated there, which kinds of projects drive
demand, which profiles are needed). Never invent company names, project names or
statistics.

Return JSON exactly like:
{{"{city_names[0]}": "<paragraph>", ...}}   (one key per city, names as given)
{extra}
Use the target keywords naturally where relevant: {kws}
Write entirely in {lang_hint}; never use the slug "{trade}"."""
    return chat(client, model, style, user)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="list would-be drafts without calling the LLM or writing files")
    ap.add_argument("--redraft", action="append", default=[], metavar="SLOT",
                    help="draft SLOT again regardless of the ledger (repeatable), "
                         "e.g. --redraft werkgevers_nl")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(args.config))
    dry_run = args.dry_run
    redraft = set(args.redraft)
    if not dry_run:
        client = OpenAI(api_key=cfg["llm"]["api_key"])
    else:
        client = None
    model = cfg["llm"].get("model", "gpt-4o-mini")

    repo = args.repo
    messages_dir = os.path.join(repo, "src", "messages")
    msg_cache = {}

    def load_msg(lang):
        """{lang}.json; a missing or unreadable file is treated as empty."""
        if lang not in msg_cache:
            path = os.path.join(messages_dir, f"{lang}.json")
            try:
                with open(path, encoding="utf-8") as f:
                    msg_cache[lang] = json.load(f)
            except (OSError, ValueError) as e:
                print(f"  warn: cannot read {path}: {e}")
                msg_cache[lang] = {}
        return msg_cache[lang]

    nl = load_msg("nl")
    ledger = load_ledger()
    seen_slots = set()

    # ground truth: which trades have routes
    cc_path = os.path.join(repo, "src/data/cityContent.ts")
    live_trades = set()
    if os.path.exists(cc_path):
        m = re.search(r"flagshipTrades\s*=\s*\[([^\]]+)\]", open(cc_path).read())
        if m:
            live_trades = set(re.findall(r"'([^']+)'", m.group(1)))

    # flagship cities (the cities that get trade x city pages)
    cities_path = os.path.join(repo, "src/data/cities.ts")
    flagship_cities = []
    if os.path.exists(cities_path):
        m = re.search(r"flagshipCitySlugs\s*=\s*\[([^\]]+)\]", open(cities_path).read())
        if m:
            flagship_cities = re.findall(r"'([^']+)'", m.group(1))

    analysis = json.load(open(cfg.get("market_analysis", "data/market-analysis.json"),
                              encoding="utf-8"))
    kws = analysis["keywords"]
    # commercial keywords per language: the ONLY fallback evidence a draft of
    # that language may use (never the other language's keywords).
    all_commercial = [k for k in kws if k["volume"] > 0]
    by_cluster = {}
    for k in kws:
        if k["volume"] > 0:
            by_cluster.setdefault(k["cluster"], []).append(k)

    drafts = {}
    targets = []
    skipped = []          # (slot, draft file, why) for the report
    guard_failures = []   # (draft file, [problems])
    rationale = ["# Agent 3 — data-driven drafts\n",
                 f"Model: {model} | source: {cfg.get('market_analysis')}\n"]

    def note_skip(slot, name, why):
        skipped.append((slot, name, why))
        print(f"  skip {slot}: {why}")

    def keep(name, lang, payload, slot, site_hash):
        """Language-guard, register and ledger a draft. False = excluded."""
        try:
            assert_language(payload, lang)
        except LanguageGuardError as e:
            guard_failures.append((name, e.problems))
            print(f"  !! LANGUAGE GUARD — drafts/{name} ({lang}) NOT written:")
            for problem in e.problems[:5]:
                print(f"       {problem}")
            rationale.extend([f"## !! {name} — EXCLUDED by the language guard ({lang})",
                              *[f"- {p}" for p in e.problems], ""])
            return False
        drafts[name] = payload
        ledger[slot] = {"site_hash": site_hash,
                        "drafted_on": dt.date.today().isoformat(),
                        "status": "pending"}
        return True

    # 1) werkgevers rewrite — evidence: unranked core keywords, PER LANGUAGE.
    # `core` holds Dutch AND French keywords; drafting one Dutch file from both
    # is what shipped FR words in the NL hero, so each language gets its own
    # draft, its own keywords and its own message file.
    core = by_cluster.get("core", [])
    for lang in ("nl", "fr"):
        slot = f"werkgevers_{lang}"
        name = SLOT_FILES[slot]
        unr = [k for k in evidence_for(core, lang, all_commercial)
               if (k.get("your_rank") or 99) > 10]
        if not unr:
            print(f"  skip {slot}: no unranked core keywords in {lang}")
            continue
        site_hash = canonical_hash(werkgevers_slice(load_msg(lang)))
        action, why = slot_decision(ledger, slot, site_hash, redraft, seen_slots)
        if action == "skip":
            note_skip(slot, name, why)
            continue
        if dry_run:
            targets.append(f"{name} ({len(unr)} unranked {lang} core kws)")
            print(f"[dry-run] would generate: {name} "
                  f"({len(unr)} unranked {lang} core kws)")
            continue
        print(f"LLM: werkgevers rewrite {lang} ({len(unr)} unranked {lang} core kws)")
        payload = draft_werkgevers_rewrite(client, model, unr, lang)
        if keep(name, lang, payload, slot, site_hash):
            rationale += [
                f"## {name} — rewrite EmployersPage + Metadata ({lang})",
                f"Evidence (unranked {lang} core keywords, language-filtered):",
                *[f"- `{k['keyword']}` vol {k['volume']}, rank {k.get('your_rank') or '-'}"
                  for k in unr], ""]

    # 2) trade pages — evidence: trade cluster volume > 0 and route exists.
    # Trades.<trade> lives in nl.json, so this draft is Dutch.
    for cluster, items in by_cluster.items():
        if not cluster.startswith("trade:"):
            continue
        trade = cluster.split(":", 1)[1]
        if trade in nl.get("Trades", {}):
            continue  # already has copy
        if live_trades and trade not in live_trades:
            print(f"  skip Trades.{trade}: no route in flagshipTrades")
            continue
        slot = f"Trades__{trade}"
        name = f"Trades__{trade}.json"
        site_hash = canonical_hash(subtree_slice(nl, "Trades", trade))
        action, why = slot_decision(ledger, slot, site_hash, redraft, seen_slots)
        if action == "skip":
            note_skip(slot, name, why)
            continue
        ev = evidence_for(items, "nl", all_commercial)
        if dry_run:
            targets.append(name)
            print(f"[dry-run] would generate: {name}")
            continue
        print(f"LLM: Trades.{trade} (vol {sum(i['volume'] for i in ev)})")
        payload = {"Trades": {trade: draft_trade(client, model, trade, ev)}}
        if keep(name, "nl", payload, slot, site_hash):
            rationale += [f"## {name} — Trades.{trade} (nl)",
                          *[f"- `{i['keyword']}` vol {i['volume']}, rank {i.get('your_rank') or '-'}"
                            for i in ev], ""]

    # 3) city content — evidence: city:<c> cluster with volume, copy missing.
    # CitiesSeo/CityRegio entries are Dutch, so filter evidence to nl too.
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
        slot = f"city__{city}"
        name = f"city__{city}.json"
        site_hash = canonical_hash(city_slice(nl, city))
        action, why = slot_decision(ledger, slot, site_hash, redraft, seen_slots)
        if action == "skip":
            note_skip(slot, name, why)
            continue
        ev = evidence_for(items, "nl", all_commercial)
        if dry_run:
            targets.append(name)
            print(f"[dry-run] would generate: {name}")
            continue
        print(f"LLM: CitiesSeo.{city} (vol {sum(i['volume'] for i in ev)})")
        out = draft_city(client, model, city, prov, ev)
        payload = {
            "CitiesSeo": {city: out["description"]},
            "CityRegio": {city: {"context": out["context"]}},
        }
        if keep(name, "nl", payload, slot, site_hash):
            rationale += [f"## {name} — CitiesSeo.{city} + CityRegio.{city} (nl)",
                          *[f"- `{i['keyword']}` vol {i['volume']}, rank {i.get('your_rank') or '-'}"
                            for i in ev], ""]

    # 4) Nation-wide trade landing copy (TradeNation.<trade>) for nl + fr + ru.
    # These populate the base trade pages /diensten/onderaannemer-{trade} with
    # UNIQUE country-level copy (not a reworded city page) + city links.
    want_nation = set(live_trades) if live_trades else {
        "gevel", "renovatie", "beton", "dak", "ruwbouw", "interieur"}
    for lang in ("nl", "fr", "ru"):
        msg = load_msg(lang)
        have_nation = set((msg.get("TradeNation") or {}).keys())
        for trade in sorted(want_nation - have_nation):
            slot = f"TradeNation_{trade}__{lang}"
            name = f"TradeNation_{trade}__{lang}.json"
            site_hash = canonical_hash(subtree_slice(msg, "TradeNation", trade))
            action, why = slot_decision(ledger, slot, site_hash, redraft, seen_slots)
            if action == "skip":
                note_skip(slot, name, why)
                continue
            ev = evidence_for(by_cluster.get(f"trade:{trade}", []), lang,
                              all_commercial)
            if dry_run:
                targets.append(f"TradeNation/{trade}.json ({lang})")
                print(f"[dry-run] would generate: TradeNation/{trade}.json ({lang})")
                continue
            print(f"LLM: TradeNation.{trade} ({lang})")
            block = gen_trade_nation(client, model, trade, keyword_names(ev), lang)
            payload = {"TradeNation": {trade: block}}
            if keep(name, lang, payload, slot, site_hash):
                rationale += [f"## {name} — TradeNation.{trade} ({lang})",
                              *[f"- `{k['keyword']}` vol {k['volume']}"
                                for k in ev], ""]

    # 5) Unique per trade x city copy (TradeCity.<trade>.<city>) for nl + fr + ru.
    # Makes each /diensten/onderaannemer-{trade}-{city} page genuinely distinct
    # (the near-duplicate fix for the "Discovered - currently not indexed" pages).
    if flagship_cities:
        for lang in ("nl", "fr", "ru"):
            msg = load_msg(lang)
            have_tc = set((msg.get("TradeCity") or {}).keys())
            for trade in sorted(want_nation - have_tc):
                slot = f"TradeCity_{trade}__{lang}"
                name = f"TradeCity_{trade}__{lang}.json"
                site_hash = canonical_hash(subtree_slice(msg, "TradeCity", trade))
                action, why = slot_decision(ledger, slot, site_hash, redraft, seen_slots)
                if action == "skip":
                    note_skip(slot, name, why)
                    continue
                ev = evidence_for(by_cluster.get(f"trade:{trade}", []), lang,
                                  all_commercial)
                if dry_run:
                    targets.append(f"TradeCity/{trade}.json ({lang})")
                    print(f"[dry-run] would generate: TradeCity/{trade}.json ({lang})")
                    continue
                print(f"LLM: TradeCity.{trade} ({lang})")
                block = gen_trade_city(client, model, trade, flagship_cities,
                                       keyword_names(ev), lang)
                payload = {"TradeCity": {trade: block}}
                if keep(name, lang, payload, slot, site_hash):
                    rationale += [f"## {name} — TradeCity.{trade} ({lang})",
                                  *[f"- `{k['keyword']}` vol {k['volume']}"
                                    for k in ev], ""]

    unused_redraft = sorted(redraft - seen_slots)

    if dry_run:
        if skipped:
            print("\n[ledger] slot(s) already handled:")
            for slot, name, why in skipped:
                print(f"  - {slot} ({name}): {why}")
        print(f"\nDry run: {len(targets)} draft(s) would be generated "
              f"(no LLM calls, no files written).")
        print("Run without --dry-run to generate them.")
        print("(dry-run: no ledger write, no email)")
        if unused_redraft:
            print(f"note: --redraft matched no slot in this run: {unused_redraft}")
        return

    os.makedirs("drafts", exist_ok=True)
    for name, content in drafts.items():
        with open(os.path.join("drafts", name), "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
        print(f"  wrote drafts/{name}")

    rationale += ["", "## Skipped (already handled)", ""]
    if skipped:
        rationale += [f"- `{slot}` ({name}): {why}" for slot, name, why in skipped]
    else:
        rationale.append("- (nothing skipped this run)")
    rationale.append("")
    write_text(os.path.join("drafts", "report.md"), "\n".join(rationale))
    save_ledger(ledger)

    print(f"\n{len(drafts)} drafts, evidence in drafts/report.md")
    print(f"ledger: {LEDGER_PATH} ({len(ledger)} slot(s) tracked), "
          f"{len(skipped)} slot(s) skipped")
    if unused_redraft:
        print(f"note: --redraft matched no slot in this run: {unused_redraft}")

    # notify: email you the drafts for review (nothing goes live automatically)
    if drafts and cfg.get("notify"):
        import notify
        notify.send_review_email(cfg, "drafts", "\n".join(rationale))

    if guard_failures:
        print(f"\n!! {len(guard_failures)} draft(s) EXCLUDED by the language guard "
              f"(not written, listed in drafts/report.md):")
        for name, problems in guard_failures:
            print(f"  - drafts/{name}: {problems[0]}"
                  + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""))
        sys.exit(1)


if __name__ == "__main__":
    main()
