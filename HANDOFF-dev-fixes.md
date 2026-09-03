# Handoff: Constructief SEO fixes & RU worker pages

**Branch:** `google-sheets` (the live preview branch; `main` is stale — merge or promote before production deploy).
**Priority:** P1 = ranking-blocking, P2 = quality/SEO impact, P3 = new capability.

---

## P1-1. Fix `{province}` placeholder in CitySEO_var2 / CitySEO_var3

**Problem:** `src/messages/nl.json` → `CitySEO_var2.regional_text` and `CitySEO_var3.regional_text` contain literal `{province}`. The city landing component fills `{city}` but **province is never passed**, so pages may render the raw placeholder (duplicate content + broken text on every city page using variants 2/3 — Google-visible).

**Fix — in the city landing component (`CityLanding` / wherever `CitySEO_var{1,2,3}` is consumed):**

Pass both params when translating:

```ts
const cityData = citiesData.find(c => c.slug === city);
const t = await getTranslations({ locale, namespace: `CitySEO_var${cityData.variation}` });

// every t(...) call that interpolates must receive BOTH values:
t('regional_text', { city: cityData.name, province: cityData.province })
```

Safest: build a `params = { city: cityData.name, province: cityData.province }` once and pass it to every `t()` call for the variant.

**Verify:** hit `/nl/diensten/onderaannemer-gent` (variation 2) and `/nl/diensten/onderaannemer-amsterdam` (variation 3) and grep the rendered HTML for `{province}` and `{city}`. Zero occurrences = fixed.

---

## P1-2. Internal linking: city/trade pages must point at `/werkgevers`

`/nl/werkgevers` is the commercial flagship (targets "onderaannemer bouw", "detachering bouwpersoneel"). Programmatic pages currently interlink with *each other* (nearby cities) but should also push equity to the money page.

**Fix — in `CityLanding` + `TradeCityLanding`:** add a contextual link block near the final CTA, e.g.:

> *Werkt u landelijk of zoekt u een specifieke ploeg? Bekijk hoe wij [gescreende bouwploegen voor aannemers](/nl/werkgevers) selecteren en leveren.*

Rules:
- Descriptive anchor text (contains "bouwp(ossible)oegen" / "onderaannemer bouw" semantics), not "klik hier".
- One link per page, above the fold-adjacent CTA is fine.
- Use the `Link` from `@/i18n/routing` so locale handling stays correct.

---

## P2-1. Extend `flagshipTrades` with `beton` and `dak`

Currently `src/data/cityContent.ts`:

```ts
export const flagshipTrades = ['gevel', 'renovatie'];
```

**Steps:**

1. `src/data/cityContent.ts`:
   ```ts
   export const flagshipTrades = ['gevel', 'renovatie', 'beton', 'dak'];
   ```
   (No changes needed to `parseDienstenSlug` or `generateStaticParams` — both derive from `flagshipTrades` automatically. Verify this is still true after the edit.)

2. `src/messages/nl.json` — add `Trades.beton` and `Trades.dak` following the exact `Trades.gevel` schema (`label, title, intro, features[], cta_title, cta_desc, cta_button, section_title`), with `{city}` placeholders. Draft content (review/edit before shipping):

   ```json
   "beton": {
     "label": "Beton",
     "title": "Betonploegen & betonarbeiders in {city}",
     "intro": "Ervaren betonploegen voor funderingen, vloeren en constructiewerk in {city}. Betonarbeiders, bekisters en ijzervlechters — legaal, gescreend en direct inzetbaar.",
     "features": [
       "Funderingen, vloeren en betonconstructies",
       "Bekisting en ijzervlechtwerk",
       "Complete ploeg inclusief meewerkend voorman"
     ],
     "cta_title": "Betonploeg in {city} aanvragen",
     "cta_desc": "Vraag direct een geteste betonploeg aan voor uw project in {city}.",
     "cta_button": "Betonploeg aanvragen",
     "section_title": "Wat uw betonploeg doet in {city}"
   },
   "dak": {
     "label": "Dak",
     "title": "Dakwerkers & dakploegen in {city}",
     "intro": "Dakwerkers voor platte en hellende daken in {city} — EPDM, bitumen, pannen. Gescreende ploegen, volledig legaal met A1 en Limosa geregeld.",
     "features": [
       "Platte daken (EPDM, bitumen)",
       "Hellende daken (pannen, leien)",
       "Dakrenovatie en herstelwerken"
     ],
     "cta_title": "Dakploeg in {city} aanvragen",
     "cta_desc": "Vraag direct een gescreende dakploeg aan voor uw project in {city}.",
     "cta_button": "Dakploeg aanvragen",
     "section_title": "Wat uw dakploeg doet in {city}"
   }
   ```

3. Also add translations to `fr.json` (and `ru.json` only if those trade pages should exist in RU — see P3).

**Sitemap check:** after adding, confirm `src/app/sitemap.ts` picks up the new `trade × flagshipCity` combinations automatically (it should, if it iterates `flagshipTrades × flagshipCitySlugs`). Result: 4 trades × (4 BE + optional NL cities) new indexed pages.

**Verify:** `npx next build` → check `/nl/diensten/onderaannemer-beton-antwerpen` and `/nl/diensten/onderaannemer-dak-gent` render and appear in `/sitemap.xml`.

---

## P2-2. Copy inconsistency: homepage says "recruiter", werkgevers says "ploegen"

`Hero.subtitle`, `ValueProps` and `AboutPage.story_*` speak of individual *vakmannen*; `EmployersPage`/`EmployersUsp` sell *gescreende ploegen for GCs*. Both audiences are real, but make the split explicit so Google (and visitors) don't get mixed signals:

- **Homepage / kandidaten-facing copy** → worker audience (recruitment, testweek, anonimity).
- **`/werkgevers` + all `/diensten/*` pages** → contractor audience (ploegen, A1/Limosa, finders fee).

Concrete minimal edit: in `Hero.subtitle`, change "direct inzetbare vakmannen" → "direct inzetbare vakmannen en bouwploegen" (or equivalent). Everything else can stay; the important part is that the contractor CTA (`cta_employers`) is semantically distinct and links to `/werkgevers`.

---

## P3-1. RU worker-facing pages (new capability)

Goal: capture Russian-speaking workers searching for jobs (e.g. "работа в бельгии строительство", "вакансии строительство нидерланды"). **Separate intent from the B2B pages — never mix on one URL.**

1. **Remove the blanket `noindex`** from `/ru/` **worker-facing pages only** (`/ru`, `/ru/vacatures` (or the RU equivalent), `/ru/kandidaten`, `/ru/vacatures/[id]`). Keep `/ru/diensten/*` noindexed forever — those are B2B duplicates of the NL pages.
   - This means the current logic in `[locale]/diensten/[slug]/page.tsx` (`isLocaleIndexed = locale !== 'ru'`) **stays as-is**; only page-level metadata on the worker routes changes.
2. **Hreflang:** for the *worker* cluster, `ru` pages are NOT alternates of the `nl` pages (different intent/audience). Give them `x-default` → themselves or omit alternates; do NOT point `x-default` at `/nl/` for these routes, otherwise Google collapses them.
3. **Content:** write real RU copy in `ru.json` for `HomePage`, `CandidatesPage`, `VacanciesPage`, `CandidateForm`, plus a dedicated RU FAQ block ("Документы A1/Limosa", "Жильё", "Оплата"). No machine-only translation of the NL marketing copy — it must answer worker questions. (Agent 3 will draft these later with OpenAI; schema must exist first.)
4. **Metadata:** `Metadata.title/description` in `ru.json` targeting RU job-search keywords.

---

## Definition of done

- [ ] No `{city}`/`{province}` literals in rendered HTML of any `/diensten/` page (test variations 1–3)
- [ ] `flagshipTrades = ['gevel','renovatie','beton','dak']`; new trade pages indexed in sitemap
- [ ] Every city/trade page has exactly one internal link to `/nl/werkgevers` with descriptive anchor
- [ ] `/ru` worker pages indexable, `/ru/diensten/*` still noindexed
- [ ] Deploy preview → fetch-as-Google (URL Inspection in GSC) on 2–3 sample pages

Questions → Albert (or the SEO agent repo `seo-agent/`, Agent 3 consumes `flagshipTrades` + `Trades.*` schema directly).
