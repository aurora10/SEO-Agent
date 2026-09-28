# SEO Agent — Agents 1 + 2 + 3

A data-driven SEO loop for **constructief-bouw.be**:

**Agent 1** — GSC collector: Search Console performance → SQLite (`data/seo.db`)
**Agent 2** — Market analyzer: keyword universe → DataForSEO volumes + live SERPs
→ gap report (`reports/market-analysis.md` + machine-readable `data/market-analysis.json`)
**Agent 3** — LLM content writer (OpenAI): turns Agent 2's findings into
repo-ready JSON drafts — every draft is traceable to keywords with real
volume where you rank poorly.

## Setup (one time, ~10 min)

1. **Python env**
   ```bash
   cd seo-agent
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Google Cloud: enable the API + create OAuth credentials** (Agent 1)
   1. Go to https://console.cloud.google.com — create a project (e.g. "seo-agent").
   2. APIs & Services → Library → enable **Google Search Console API**.
   3. APIs & Services → OAuth consent screen → External → fill in app name +
      your email. Add scope: `.../auth/webmasters` (read + write: needed to
      resubmit the sitemap; `webmasters.readonly` also works but then sitemap
      submission is unavailable).
      Add yourself as a **test user** (important, or consent will fail).
   4. APIs & Services → Credentials → Create Credentials → **OAuth client ID**
      → Application type: **Desktop app** → Download JSON.
   5. Save the file as `credentials/client_secret.json` in this folder.

3. **DataForSEO** (Agent 2): sign up at https://app.dataforseo.com/signup,
   top up $5, copy **API login + password** (API access page) into config.yaml.

4. **OpenAI** (Agent 3): create a key at https://platform.openai.com/api-keys,
   paste into config.yaml (`llm.api_key`).

5. **Configure**
   ```bash
   cp config.example.yaml config.yaml
   # fill every placeholder; see comments in the file
   ```

## Agent 1: GSC collector

```bash
source .venv/bin/activate
python src/collect_gsc.py --config config.yaml
```

First run opens a browser — log in with the Google account that has access to
your Search Console property. Token is cached in `credentials/token.json`;
later runs are headless. The script backfills ~90 days, then prints rows/day.

Re-running is safe (dedupes via primary key) and incremental (syncs only new
dates, skipping the GSC 3-day lag window).

**Verify:**
```bash
python src/inspect_data.py --config config.yaml
```
Shows date range, row counts, top queries/pages by impressions, and
"striking distance" keywords (positions 4–20).

**Schedule (optional):**
```
30 6 * * *  cd /path/to/seo-agent && .venv/bin/python src/collect_gsc.py --config config.yaml >> data/collector.log 2>&1
```

## Agent 2: market analysis

```bash
python src/fetch_market.py --config config.yaml     # ~$0.25–0.50, cached
python src/analyze_market.py --config config.yaml   # writes reports/market-analysis.md
```

The report shows keyword volumes, which clusters you rank for vs miss, the target page
(existing or "MISSING — create"), and the top-3 competitors per keyword.
It also writes `data/market-analysis.json` — the machine input for Agent 3.

## Agent 3: data-driven content writer

Not random content: **each draft exists because a keyword in your market
analysis has volume and you rank poorly**. Run:

```bash
python src/generate_content.py --config config.yaml --repo /path/to/constructief
```

What it writes to `drafts/` (nothing touches the repo until you paste). A `__<lang>`
suffix marks the target locale (`nl`/`fr`/`ru`); no suffix means `nl`:

| Draft | Trigger | Content |
|---|---|---|
| `werkgevers.json` / `werkgevers__fr.json` | core keywords in that language with volume, you rank >10 | rewrite of `EmployersPage.title/subtitle` + `Metadata` in `nl.json` / `fr.json` |
| `Trades__<trade>.json` | `trade:<trade>` cluster has volume, trade missing from `nl.json` **and** route exists in `flagshipTrades` | complete `Trades.<trade>` block |
| `city__<city>.json` | `city:<city>` cluster has volume, `CitiesSeo.<city>` missing | `CitiesSeo` + `CityRegio` entries |
| `TradeNation_<trade>__<lang>.json` | `TradeNation.<trade>` missing for that locale | country-level copy for `/diensten/onderaannemer-<trade>` |
| `TradeCity_<trade>__<lang>.json` | `TradeCity.<trade>` missing for that locale | unique paragraph per trade x city for `/diensten/onderaannemer-<trade>-<city>` |
| `report.md` | always | per draft: the keywords, volumes, your rank, why the draft exists |

Generated for **nl, fr and ru**, so all three locales stay at parity.

Guards (verified in tests):
- **Evidence is language-scoped.** Keywords carry `lang`. A Dutch draft only ever
  sees Dutch keywords and vice versa — the French core keywords
  (`sous-traitant construction`) used to be handed to the Dutch rewrite, which is
  how French text kept leaking into the Dutch `werkgevers` subtitle.
- **Language guard.** Every draft is scanned before it is written; a draft that
  contains foreign-language markers (or non-allowlisted Latin script in a Russian
  draft) is *excluded*, reported in `drafts/report.md`, and the run exits non-zero.
- **Each slot is drafted once** (`data/agent3_applied.json`). Re-runs report
  "draft still pending merge" or "already applied" instead of regenerating the
  same werkgevers rewrite every week. Force one with `--redraft <slot>`.
- Trade drafts skipped if the route is not in `flagshipTrades` — no orphan copy.
- FR page (`/fr/sous-traitance-batiment`) is never touched — it exists, is
  complete, FR-only by design (nl 404s intentionally).
- Schema copied exactly from your repo's `Trades.gevel` template.

Workflow: **check `drafts/report.md` first** — it shows why each draft exists
(keyword, volume, your rank) and what was skipped. Then review/edit the JSON,
merge into `src/messages/<lang>.json`, commit.

Cost: ≈ €0.10/run with `gpt-4o-mini`.

### Bulk RU content (one-off / refresher)

`generate_content.py` covers RU as part of the weekly cycle. For a bulk pass over
every trade x city pair there are two standalone helpers, useful when you add
trades/cities and need the whole matrix filled at once:

```bash
python scripts/gen_ru_trade_city.py   --repo /path/to/constructief   # 6 trades x 4 cities
python scripts/gen_ru_trade_nation.py --repo /path/to/constructief   # 6 base trade pages
```

Both validate their own output (Cyrillic only, no Dutch/French leakage, no
near-duplicate paragraphs between cities or trades) and write to `drafts/`.

## The loop

```
fetch_market (Agent 2)  ->  analyze_market (Agent 2)
    -> generate_content (Agent 3)  ->  you review drafts/report.md
    -> paste into repo, commit, deploy
    -> collect_gsc (Agent 1) measures the effect next week
    -> next market-analysis cycle
```

## Run on a VPS (Docker)

The agents run in a self-scheduling Docker container (a Python scheduler daemon), deployed via
GitHub Actions. Secrets live in a `.env` on the VPS — none are in the repo/image.

- `Dockerfile`, `entrypoint.sh`, `src/scheduler.py`, `docker-compose.yml`, `deploy/setup_vps.sh`
- `.github/workflows/deploy.yml` — builds + pushes `DockerHubUser/seo-agent` on push
  to `main`, and optionally redeploys the VPS over SSH.

**One-time VPS setup** (Debian/Ubuntu, as root):
```bash
git clone https://github.com/aurora10/SEO-Agent.git /srv/seo-agent
cd /srv/seo-agent && sudo bash deploy/setup_vps.sh
# then edit .env with real secrets (it was created from .env.example):
#   - copy values from your local config.yaml
#   - base64 -i credentials/client_secret.json | tr -d '\n'   -> CLIENT_SECRET_JSON
#   - base64 -i credentials/token.json      | tr -d '\n'      -> TOKEN_JSON
docker compose up -d          # (or re-run setup_vps.sh which starts it)
```

**GitHub Secrets** (repo → Settings → Secrets and variables → Actions):
`DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, and optionally `VPS_HOST`,
`VPS_USER`, `VPS_PORT`, `VPS_SSH_KEY`, `VPS_DIR` (for auto-redeploy).

The container's scheduler: `collect_gsc` daily, `fetch_market`/`analyze_market`/
`generate_content`/`gsc_monitor` weekly. Every run is logged to `data/jobs.log`, and a
failure emails you. Logs: `docker compose logs -f`.

## Indexing automation — what runs for you, and what cannot be automated

Google has **no API for "Request Indexing"**: the Search Console API can only
*inspect* whether a URL is indexed, and the button in the UI is limited to about
10 URLs/day. So 91 URLs cannot be requested programmatically — but requesting
them individually is also the least valuable lever. What actually gets pages
indexed is a fresh sitemap plus internal links; both are handled here.

Automated on the weekly schedule:

| Job | What it does | Your action |
|---|---|---|
| `submit_sitemap` (Mon 07:30) | Resubmits `sitemap.xml` and reports what Google last downloaded, plus any sitemap errors, and prunes page URLs wrongly registered as sitemaps | none |
| `gsc_monitor` (Mon 07:15) | Inspects all 91 priority URLs, tracks **how long** each has been unindexed, and writes `reports/gsc-request-indexing.txt` | none |
| `collect_gsc` (daily 06:05) | Search-analytics history, so ranking effects are measurable | none |

The only manual step left is nudging URLs that stay unindexed for weeks. The
monitor does that triage for you: anything unindexed **≥ 14 days** is listed
first (oldest first, capped at the ~10/day quota) and it emails "No action
needed" while everything is still fresh, so you are never asked to click for
pages Google simply has not visited yet.

```bash
python src/submit_sitemap.py --config config.yaml          # submit + status (the weekly job)
python src/submit_sitemap.py --config config.yaml --list    # status only
python src/submit_sitemap.py --config config.yaml --prune   # drop non-sitemap entries
python src/gsc_monitor.py --config config.yaml --repo /path/to/site --no-email   # baseline, no email
```

`submit_sitemap.py` exits non-zero (and so emails you) if the token lacks the
write scope, printing the exact one-time fix: re-consent on a machine with a
browser, then update `TOKEN_JSON` in the VPS `.env`.

## Merge new content (runbook)

After `generate_content` emails drafts, get them live one of two ways:

**Option A — manual commit & push (most control)**
```bash
cd /path/to/constructief
# merge each draft fragment into src/messages/<lang>.json under its top-level
# key (e.g. "EmployersPage", "TradeNation", "TradeCity", "Trades").
# scripts/merge_messages.py does this surgically — it replaces only the namespace
# that changed, keeps existing values unless --force, backs the file up first, and
# refuses to write anything that is not valid JSON:
python /path/to/seo-agent/scripts/merge_messages.py \
    --file src/messages/ru.json --payload /path/to/seo-agent/drafts/TradeCity.ru.json --dry-run
git add src/messages/nl.json src/messages/fr.json src/messages/ru.json
git commit -m "SEO: apply Agent 3 content drafts"
git push origin google-sheets        # Vercel deploys
```

**Option B — open a PR (via publish_drafts, safer)**
```bash
cd /path/to/seo-agent
python src/publish_drafts.py --config config.yaml --repo /path/to/constructief --dry-run   # preview
python src/publish_drafts.py --config config.yaml --repo /path/to/constructief             # opens a PR
# then on GitHub: review the PR -> "Merge pull request" -> Vercel deploys
```

`generate_content` never touches the repo directly — it only produces drafts for you to review.

## Indexation policy (site-side, worth not regressing)

- **Trade pages** (`/diensten/onderaannemer-{trade}` and
  `onderaannemer-{trade}-{city}`) are indexable and sitemapped in **all three
  locales, ru included**. That is only safe because each page carries unique
  copy per trade and per city; if trade x city pages are ever mass-generated
  again without unique copy, Google files them as near-duplicates
  ("Discovered - currently not indexed").
- **City-only pages** (`onderaannemer-{city}`) are indexable only for the
  flagship cities, and never for ru.
- hreflang alternates are reciprocal on trade pages: `nl`, `fr`, `ru`,
  `x-default`. Non-indexed copies (thin city pages, ru city pages) declare only
  `x-default` -> the nl version.
- The weekly `gsc_monitor` inspects **91 URLs** (nl/fr/ru x trade + trade x city
  + `/nl/werkgevers`).

## Troubleshooting

- **"Site not found / permission denied"** → `gsc_property` string is wrong.
  Copy it exactly from the GSC property selector (`sc-domain:...` vs `https://...`).
- **Consent screen error "access blocked"** → you forgot to add yourself as a
  test user on the OAuth consent screen.
- **"API has not been used / disabled"** → enable Search Console API in step 2.2.
- **`Skipping Trades.<x>: not in flagshipTrades`** → developer hasn't added the
  route yet; apply `HANDOFF-dev-fixes.md` first.
- **Zero rows but no errors** → site is very new or has no impressions yet;
  the DB and pipeline still work, data will appear as traffic grows.
