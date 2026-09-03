"""Build config.yaml from environment variables (.env via docker-compose).

The repo never stores secrets; the container reconstructs config.yaml at start
from the env vars you put in the VPS .env file. This mirrors config.example.yaml.
"""
import os

import yaml


def _s(*names, default=None):
    for n in names:
        v = os.environ.get(n)
        if v not in (None, ""):
            return v
    return default


def main() -> None:
    cfg = {
        "gsc_property": _s("GSC_PROPERTY", default="https://constructief-bouw.be/"),
        "oauth_client_secret": "credentials/client_secret.json",
        "token_file": "credentials/token.json",
        "db_path": _s("DB_PATH", default="data/seo.db"),
        # Markets drive which (country, language) DataForSEO queries run.
        "markets": [
            {"language": "nl", "country": "BE"},
            {"language": "nl", "country": "NL"},
            {"language": "fr", "country": "BE"},
        ],
        "backfill_days": int(_s("BACKFILL_DAYS", default="90")),
        "your_domain": _s("YOUR_DOMAIN", default="constructief-bouw.be"),
        "market_db": _s("MARKET_DB", default="data/market.db"),
        "row_limit": int(_s("ROW_LIMIT", default="25000")),
    }

    if _s("DFS_LOGIN") and _s("DFS_PASSWORD"):
        cfg["dataforseo"] = {
            "login": _s("DFS_LOGIN"),
            "password": _s("DFS_PASSWORD"),
        }

    if _s("OPENAI_API_KEY"):
        cfg["llm"] = {
            "api_key": _s("OPENAI_API_KEY"),
            "model": _s("LLM_MODEL", default="gpt-4o-mini"),
        }
        cfg["market_analysis"] = _s("MARKET_ANALYSIS", default="data/market-analysis.json")

    if _s("GMAIL_USER") and _s("GMAIL_APP_PASSWORD"):
        cfg["notify"] = {
            "to": _s("NOTIFY_TO", default=_s("GMAIL_USER")),
            "from": _s("NOTIFY_FROM", default=_s("GMAIL_USER")),
            "gmail_user": _s("GMAIL_USER"),
            "gmail_app_password": _s("GMAIL_APP_PASSWORD"),
            "subject_prefix": _s("SUBJECT_PREFIX", default="[SEO drafts]"),
        }

    if _s("GITHUB_TOKEN"):
        cfg["github"] = {
            "token": _s("GITHUB_TOKEN"),
            "repo": _s("GITHUB_REPO", default="aurora10/constructief"),
            "base_branch": _s("GITHUB_BASE_BRANCH", default="google-sheets"),
        }

    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    print("config.yaml written")


if __name__ == "__main__":
    main()
