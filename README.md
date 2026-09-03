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
      your email. Add scope: `.../auth/webmasters.readonly`.
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

What it writes to `drafts/` (nothing touches the repo until you paste):

| Draft | Trigger | Content |
|---|---|---|
| `werkgevers.json` | core keywords with volume, you rank >10 | rewrite of `EmployersPage.title/subtitle` + `Metadata` |
| `Trades__<trade>.json` | `trade:<trade>` cluster has volume, trade missing from `nl.json` **and** route exists in `flagshipTrades` | complete `Trades.<trade>` block |
| `city__<city>.json` | `city:<city>` cluster has volume, `CitiesSeo.<city>` missing | `CitiesSeo` + `CityRegio` entries |
| `report.md` | always | per draft: the keywords, volumes, your rank, why the draft exists |

Guards (verified in tests):
- Trade drafts skipped if the route is not in `flagshipTrades` — no orphan copy.
- FR page (`/fr/sous-traitance-batiment`) is never touched — it exists, is
  complete, FR-only by design (nl 404s intentionally).
- Schema copied exactly from your repo's `Trades.gevel` template.

Workflow: **check `drafts/report.md` first** — it shows why each draft exists
(keyword, volume, your rank). Then review/edit the JSON, merge into
`src/messages/nl.json`, commit.

Cost: ≈ €0.10/run with `gpt-4o-mini`.

## The loop

```
fetch_market (Agent 2)  ->  analyze_market (Agent 2)
    -> generate_content (Agent 3)  ->  you review drafts/report.md
    -> paste into repo, commit, deploy
    -> collect_gsc (Agent 1) measures the effect next week
    -> next market-analysis cycle
```

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
