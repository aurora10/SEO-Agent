"""Keyword universe for constructief-bouw.be.

Two audiences, deliberately kept apart:

1. **nl-B2B** — Dutch-speaking general contractors who buy subcontractor crews.
   Buyer intent. Targets /werkgevers and /diensten/onderaannemer-*.
2. **nl/ru-jobs** — job seekers: Dutch-speaking tradespeople in BE/NL and the
   Russian-speaking crews recruited in Eastern Europe (the /ru vacancy pages are
   indexed recruiting pages, not translations). Targets /vacatures and
   /vacatures/{trade}.

French was dropped on purpose. The FR SERPs were dominated by job boards
(emplois.be.indeed.com, constructiv.be), i.e. job-seeker intent rather than the
B2B buyer intent this site sells, so those rows measured noise and cost money.

Cluster naming:
    core                 nl-B2B, trade-agnostic          -> /werkgevers
    trade:<trade>        nl-B2B, one trade              -> /diensten/onderaannemer-<trade>
    city:<city>          nl-B2B, city                   -> /diensten/onderaannemer-<city>
    city:<city>:<trade>  nl-B2B, city x trade           -> /diensten/onderaannemer-<trade>-<city>
    jobs                 nl/ru job seekers              -> /vacatures
    jobs:<slug>          nl/ru job seekers, one trade   -> /vacatures/<slug>
                         (slug = the site's jobTradePages slug, e.g. "metselaar")
"""
from dataclasses import dataclass


@dataclass
class Keyword:
    keyword: str
    cluster: str        # e.g. "trade:beton", "jobs:metselaar"
    lang: str           # nl | ru
    city: str | None    # None = national
    target_url: str | None = None  # suggested page on the site


# --- 1. nl-B2B: core buyer intent (trade-agnostic) ---
CORE_NL = [
    "onderaannemer bouw",
    "onderaannemer bouw beschikbaar",
    "bouwploeg huren",
    "bouwploeg inhuren",
    "buitenlandse bouwvakkers inhuren",
    "poolse arbeiders bouw",
    "oost-europese vakmensen bouw",
    "detachering bouwpersoneel",
    "vakmensen bouw inhuren",
    "betrouwbare onderaannemer bouw",
]

# --- 1. nl-B2B: trades the broker actually supplies ---
# Trade keys match flagshipTrades in the site's src/data/cityContent.ts, which is
# what Vercel deploys.
TRADES_NL = {
    "gevel": ["onderaannemer gevelwerk", "gevelrenovatie onderaannemer", "gevelisolatie ploeg"],
    "renovatie": ["onderaannemer renovatie", "renovatiebouwer", "renovatieploeg"],
    "beton": ["onderaannemer betonwerken", "betonarbeiders ploeg", "betonploeg"],
    "dak": ["onderaannemer dakwerken", "dakwerkers ploeg", "dakdekker onderaannemer"],
    "ruwbouw": ["onderaannemer ruwbouw", "metselaars ploeg", "metselaars inhuren",
                "bekistingploeg", "betonvlechters"],
    "interieur": ["stukadoor ploeg", "stukadoors inhuren", "tegelzetter ploeg",
                  "onderaannemer binnenafwerking", "afwerkingsploeg bouw"],
}

CITIES_BE = ["antwerpen", "brussel", "gent", "leuven", "luik"]
CITIES_NL = ["amsterdam", "rotterdam"]

# --- 2. Job seekers, Dutch ---
# The job side has its own trade taxonomy (src/data/vacancyTrades.ts on the site:
# metselaar, bekister, kraanmachinist, werfleider, industrieel-elektricien), which
# is NOT the same as the B2B service trades above.
JOBS_NL_CORE = [
    "vacatures bouw",
    "bouw vacatures",
    "vacature bouw belgië",
    "werken in de bouw",
    "bouwjobs",
    "vacatures bouw belgië",
]

JOBS_NL_TRADES = {
    "metselaar": ["vacature metselaar", "vacatures metselaar belgië"],
    "bekister": ["vacature bekister", "bekisting vacature"],
    "kraanmachinist": ["vacature kraanmachinist", "kraanmachinist vacatures"],
    "werfleider": ["vacature werfleider", "werfleider vacature belgië"],
    "industrieel-elektricien": ["vacature industrieel elektricien",
                                "vacature elektricien industrie"],
}

# Trade-worker terms with no dedicated page yet: they target the /vacatures hub
# until a /vacatures/<trade> landing page exists for them.
JOBS_NL_WORKERS = [
    "vacature timmerman",
    "vacature stukadoor",
    "vacature dakdekker",
    "vacature metselaar antwerpen",
]

# --- 2. Job seekers, Russian ---
# The crews are recruited in Eastern Europe and mostly speak Russian, so RU is a
# first-class language here rather than a translation of the Dutch job pages.
JOBS_RU_CORE = [
    "вакансии строительство бельгия",
    "работа строительство бельгия",
    "вакансии стройка бельгия",
    "работа в бельгии строительство",
    "работа в бельгии для строителей",
    "вакансии бельгия для иностранцев строительство",
    "вакансии разнорабочий бельгия",
    "работа строителем в бельгии",
]

JOBS_RU_TRADES = {
    "metselaar": ["вакансии каменщик бельгия", "работа каменщиком в бельгии"],
    "bekister": ["вакансии опалубщик бельгия", "работа опалубщиком бельгия"],
    "kraanmachinist": ["вакансии крановщик бельгия", "работа машинистом крана бельгия"],
    "werfleider": ["вакансии прораб бельгия", "работа прорабом в бельгии"],
    "industrieel-elektricien": ["вакансии электрик бельгия",
                                "работа промышленным электриком бельгия"],
}

# Russian names for the service trades, as job-seeker queries: whoever types these
# is looking for work, so they point at the hub, not at the B2B trade pages.
JOBS_RU_WORKERS = [
    "вакансии кровельщик бельгия",
    "вакансии фасадчик бельгия",
    "вакансии бетонщик бельгия",
    "вакансии арматурщик бельгия",
    "вакансии штукатур бельгия",
    "вакансии плиточник бельгия",
    "вакансии маляр бельгия",
    "вакансии отделочник бельгия",
]


def build() -> list[Keyword]:
    kws: list[Keyword] = []

    def add(list_, cluster, lang, city=None):
        for kw in list_:
            kws.append(Keyword(kw, cluster, lang, city))

    # ---- 1. nl-B2B -------------------------------------------------------
    add(CORE_NL, "core", "nl")
    for trade, trade_kws in TRADES_NL.items():
        add(trade_kws, f"trade:{trade}", "nl")

    # City x core: only the highest-intent combos (every SERP costs money).
    for city in CITIES_BE:
        add([f"onderaannemer bouw {city}"], f"city:{city}", "nl", city)
    for city in CITIES_NL:
        add([f"onderaannemer bouw {city}", f"bouwploeg huren {city}"],
            f"city:{city}", "nl", city)

    # City x top trades (beton + interieur: the biggest demand).
    for city in CITIES_BE:
        add([f"onderaannemer betonwerken {city}"], f"city:{city}:beton", "nl", city)
        add([f"stukadoor ploeg {city}"], f"city:{city}:interieur", "nl", city)

    # ---- 2. Job seekers, nl + ru ----------------------------------------
    add(JOBS_NL_CORE, "jobs", "nl")
    add(JOBS_NL_WORKERS, "jobs", "nl")
    for slug, job_kws in JOBS_NL_TRADES.items():
        add(job_kws, f"jobs:{slug}", "nl")

    add(JOBS_RU_CORE, "jobs", "ru")
    add(JOBS_RU_WORKERS, "jobs", "ru")
    for slug, job_kws in JOBS_RU_TRADES.items():
        add(job_kws, f"jobs:{slug}", "ru")

    return kws


if __name__ == "__main__":
    from collections import Counter
    all_kw = build()
    print(f"{len(all_kw)} keywords")
    print(Counter(k.cluster for k in all_kw))
    print(Counter(k.lang for k in all_kw))
