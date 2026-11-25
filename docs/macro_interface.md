# Macro data interface: contract between a future Macro Agent and the FSA Agent

Status: **v0.1: all five decisions agreed 24 November 2026. Tables created and the two FX series loaded by hand on 25 November 2026 (IMF IFS official rates, vintage 2026-11-25-001); the CPI series is not loaded yet** · Written 24 November 2026 · The Macro Agent does not exist yet. This page fixes the contract so it can be built later without changing the FSA Agent.

## 1. Purpose and boundary

| | Macro Agent (future) | FSA Agent (this repo) |
|---|---|---|
| Owns | Economy-level research: inflation, naira rates, policy rate, fuel and gas prices, commodity prices, policy and tax changes, outlook | Company financial statements, ratios, findings, reports |
| Writes | Dated, sourced macro series and periodic briefings | Company reports and a versioned JSON |
| Reads | Public macro sources | Macro series (read-only) |
| Never does | Read company statements or draw company conclusions | Research the economy, or forecast macro variables |

Data flows one way in v1: **Macro Agent → warehouse → FSA Agent.** The FSA Agent measures company-level macro effects from the statements themselves (FX gains inside operating profit, translation reserve, monetary-position results). It does not guess the macro cause.

## 2. Rules both agents follow (same as `CLAUDE.md`)

1. Public data only. Nothing from Bello's employer, no material non-public information, no secrets in the repo.
2. Every value carries its source (name, URL), retrieval date and file fingerprint (SHA-256). Raw files live in `data/raw/macro/`, which is git-ignored; only code and definitions are public.
3. Respect each source's terms of use. No scraping where it is banned.
4. Facts, calculations and interpretation are stored and written separately. Code does the arithmetic.
5. Never overwrite history. A correction is a **new vintage**; the old one stays readable.
6. Say when data is missing. A missing value is never zero and never guessed.
7. Every discrepancy or break in a series becomes a disclosure.

## 3. The data contract: tables in the shared DuckDB warehouse

**`macro_series_catalog`**: one row per series, written once.

| Column | Meaning |
|---|---|
| `series_id` | Stable name, e.g. `FX_USDNGN_OFFICIAL_AVG_ANNUAL`. The name states the **rate type or basis** so two rates can never be mixed silently |
| `name`, `definition` | Plain-English description, including what the series is NOT |
| `unit`, `frequency` | e.g. `NGN per USD`, `index`, `percent`; `annual`, `monthly`, `daily` |
| `basis_note` | Base year, rate type, seasonal adjustment, or other basis. A change of basis is a **series break** (below) |

**`macro_series`**: one row per observation per vintage.

| Column | Meaning |
|---|---|
| `series_id`, `period_start`, `period_end` | What and when. Annual series use the calendar year |
| `value`, `unit` | The number as published |
| `vintage_id` | Which publication of the series this belongs to |
| `source_name`, `source_url`, `retrieved_at`, `source_sha256` | Where it came from |
| `quality_flag` | `ok` · `provisional` (the source may revise it) · `estimated` · `break_after` (a basis change follows) |
| `note` | Free text, neutral wording |

**`macro_vintages`**: `vintage_id`, `created_at`, `created_by` (agent and version), `note`. A vintage is a dated snapshot. The FSA Agent records the vintage id it used in every report.

Series breaks (e.g. a re-based price index) are stored as **separate series or flagged `break_after`**, never spliced into one smooth line.

## 4. Series the FSA Agent needs in v1

| `series_id` | Needed for |
|---|---|
| `FX_USDNGN_OFFICIAL_AVG_ANNUAL` | USD-translated income statement and cash flow |
| `FX_USDNGN_OFFICIAL_CLOSE_YEAREND` | USD-translated balance sheet |
| `CPI_HEADLINE_INDEX_ANNUAL_AVG` (with its `basis_note`) | Real-versus-nominal growth, shown as information only |

**Decision 1 (AGREED, Bello, 24 Nov 2026):** the default is the official rate. Nigeria has had more than one rate, so the rate used is stated in every report, and any alternative is shown only as a disclosed sensitivity.

Later (not v1): policy rate, treasury-bill and bond yields, diesel and petrol prices, gas and electricity tariffs, palm-oil and other commodity prices, GDP and fiscal data.

## 5. How the FSA Agent uses them

- **USD view:** income and cash-flow items at the annual-average rate, balance-sheet items at the year-end rate; the rates, source and vintage are named in the report's data notes.
- **Real growth:** nominal growth deflated by CPI, labelled as an informational measure. Full inflation restatement of statements stays out of scope for v1.
- **Missing or stale series:** the report says "not available" and shows nominal figures only. A vintage older than the agreed age is flagged, never silently used.
- The FSA Agent **never writes** to the macro tables.

## 6. Briefings (`macro_briefing.v1.json`)

Written by the Macro Agent for people and for other agents. Each claim states its kind, so facts are never mixed with interpretation:

```json
{
  "schema": "macro_briefing.v1",
  "vintage_id": "2026-11-30-001",
  "as_of": "2026-11-30",
  "claims": [
    {"id": "c1", "kind": "fact", "text": "…", "numbers": [{"series_id": "…", "period": "2025", "value": 0, "source": "…"}]},
    {"id": "c2", "kind": "calculation", "text": "…", "inputs": ["c1"]},
    {"id": "c3", "kind": "interpretation", "text": "…", "confidence": "low|medium|high", "inputs": ["c1", "c2"]}
  ],
  "scenarios": [],
  "sector_implications": [],
  "changes_since_last_vintage": []
}
```

## 7. Versioning and compatibility

- Additive changes (new series, new fields) are allowed within `0.x`. A change that could break the FSA Agent gets a new major version and a period where both versions are written.
- The FSA Agent pins the contract version it reads; a mismatch stops the run with a clear message.

## 8. Done when (v1 of this interface)

1. The three tables exist in the warehouse schema, with tests.
2. The three v1 series are loaded with sources and fingerprints, by hand if needed.
3. The FSA Agent can build the USD view from them, and its disclosures name the series and vintage.
4. A missing series produces "not available", not an error or a zero.

## 9. Decisions

| # | Decision | Status |
|---|---|---|
| 1 | Default FX rate | **AGREED**: official rate, alternatives only as a disclosed sensitivity |
| 2 | CPI choice and how a base-year change is handled | **AGREED**: headline inflation; deflate growth with the published year-on-year rate, which survives a re-base; keep index levels too and flag a re-base as a series break, never spliced. The Macro Agent verifies any re-basing date from the source |
| 3 | Where the Macro Agent lives | **AGREED**: its own repository, so each agent can be published and versioned separately |
| 4 | Maximum age of a vintage before a report flags it | **AGREED**: the vintage must be dated after the period it describes ends, and a report flags (never blocks on) a vintage older than 90 days |
| 5 | Whether the FSA Agent later sends company exposure facts back to the Macro Agent | **AGREED**: no for v1. Revisit once both agents exist; if ever added, a one-way, read-only table of exposure facts under the same sourcing rules |
