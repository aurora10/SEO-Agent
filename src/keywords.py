"""Keyword universe for constructief-bouw.be.

Generates buyer-intent keywords for a subcontractor-team broker
(pre-vetted Eastern European construction crews for general contractors).

Structure: cluster -> trade -> city -> keyword
Languages: nl (primary), fr (Brussels/Wallonia).
"""
from dataclasses import dataclass, field


@dataclass
class Keyword:
    keyword: str
    cluster: str        # e.g. "trade:beton"
    lang: str           # nl | fr
    city: str | None    # None = national
    target_url: str | None = None  # suggested page on the site


# --- Core buyer intent (trade-agnostic) ---
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

CORE_FR = [
    "sous-traitant construction",
    "sous-traitance bâtiment",
    "équipe construction",
    "main-d'œuvre construction",
    "ouvriers construction étrangers",
]

# --- Trades mapped to trades the broker actually supplies ---
# Note: trade names match flagshipTrades in src/data/cityContent.ts on the
# google-sheets branch (which is what Vercel deploys).
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

TRADES_FR = {
    "gevel": ["sous-traitant façade", "rénovation façade sous-traitant"],
    "renovatie": ["sous-traitant rénovation", "entrepreneur rénovation"],
    "beton": ["sous-traitant béton", "équipe béton"],
    "dak": ["sous-traitant toiture", "couvreurs équipe"],
    "ruwbouw": ["sous-traitance gros œuvre", "maçons équipe"],
    "interieur": ["sous-traitant plâtrerie", "carreleurs équipe", "finition"],
}

CITIES_BE = ["antwerpen", "brussel", "gent", "leuven", "luik"]
CITIES_NL = ["amsterdam", "rotterdam"]

BE_NL = ("BE", "nl")
NL_NL = ("NL", "nl")


def build() -> list[Keyword]:
    kws: list[Keyword] = []

    def add(list_, cluster, lang, city):
        for kw in list_:
            kws.append(Keyword(kw, cluster, lang, city))

    # Core buyer intent
    add(CORE_NL, "core", "nl", None)
    add(CORE_FR, "core", "fr", None)

    # Trades, national
    for trade, kws_nl in TRADES_NL.items():
        add(kws_nl, f"trade:{trade}", "nl", None)
    for trade, kws_fr in TRADES_FR.items():
        add(kws_fr, f"trade:{trade}", "fr", None)

    # City x core: only the highest-intent combos (control API cost)
    for city in CITIES_BE:
        lang = "nl" if city not in ("brussel", "luik") else "fr"
        add([f"onderaannemer bouw {city}"], f"city:{city}", lang, city)
    for city in CITIES_NL:
        add([f"onderaannemer bouw {city}", f"bouwploeg huren {city}"],
            f"city:{city}", "nl", city)

    # City x top trades (beton + interieur: biggest demand)
    for city in CITIES_BE:
        add([f"onderaannemer betonwerken {city}"], f"city:{city}:beton", "nl", city)
        add([f"stukadoor ploeg {city}"], f"city:{city}:interieur", "nl", city)

    return kws


if __name__ == "__main__":
    from collections import Counter
    all_kw = build()
    print(f"{len(all_kw)} keywords")
    print(Counter(k.cluster for k in all_kw))
