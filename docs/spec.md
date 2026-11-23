# FSA Agent: Spec (v1)

## 1. Purpose

An agent that analyses a company's financial statements the way a strong CFA-trained credit and equity analyst would. It classifies the business, checks the data, analyses every statement and section, benchmarks against the right peers, and produces observations, questions and hypotheses for a human (or the agent) to follow up. It is a portfolio and CV project, and the first "analyst employee" of Bello's future Sharia-compliant, AI-agent-run investment firm. It is built and tested on public data only.

Two modes share one engine:
- **Mode A (Analyst, document-driven):** v1.
- **Mode B (Personal research agent, autonomous):** after v1, using the fixed source list in section 4.

---

## 2. Decisions log

| # | Topic | Decision |
| --- | --- | --- |
| 1 | Mode first | Mode A. A small helper fetches annual reports via connected tools and logs each source; the engine takes a folder of documents |
| 2 | Sectors | Non-financial corporates: **Cement** and **Palm oil / agricultural products** in v1. Banks and insurers are marked "not yet supported" |
| 2 | Core companies | **Dangote Cement, Presco** (changed 23 Nov 2026; Nestlé Nigeria and Nigerian Breweries deferred) (subject to the conflict check) |
| 3 | Work split | Claude builds the plumbing; Bello owns `rules/` |
| 4 | v1 done | About 12 weeks: 3 full reports, 1 back-test case study, public repo, Streamlit demo |
| 5 | Regularise | Two separate outputs: **standardised** statements, then a separate **analyst-adjusted** view (one-offs out, FX below operating profit, EBITDA pre/post IFRS 16, capitalised interest added back; each adjustment logged and switchable). Plus a **USD-translated** view |
| 6 | Extraction | Automated (Docling or pdfplumber, OCR when needed) → Haiku mapping → integrity checks → Bello signs a one-page review sheet per company-year |
| 7 | Years | Core companies FY2023–FY2025 audited; the rule is "up to 5 years where available, minimum 3". A separate general industry section carries the longer context, including pre-June 2023 float |
| 8 | Benchmarks | **NGN Market Premium** (bought) for all NGX companies, plus our own extraction for core companies. Kobo terminal pending (link and terms not yet seen) |
| 10 | Report format | Markdown report + JSON with the same content; Streamlit reads the JSON |
| 11 | Ratings | Keep Financial Health, Earnings Quality and Forensic Risk (Green to Red); each lists the findings that drive it |
| 12 | JSON | One JSON per report, one record per finding; ratios and statements in DuckDB |
| 13 | Feedback loop | Streamlit form to answer open questions; affected findings re-run; each run is a version with a change log |
| 14 | Claude access | Agent SDK on the Pro plan |
| 15 | Models | Haiku (map), Sonnet (analyse), Opus (review), Fable off |
| 16 | Local model | None in v1 |
| 17 | Where it runs | Bello's Mac; code on public GitHub; demo on Streamlit's free hosting with sample data |
| 18 | Add-on budget | Zero in v1 (beyond NGN Market Premium) |
| 19 | Mode B sources | Fixed list (section 4), every document logged |
| 20 | Publishing | **Public:** code, method and case studies. Changed from private for CV value |
| 21 | Employer separation | Conflict check before any company |
| 22 | Underwriting hookup | Stable versioned JSON + DuckDB + one entry point (company + documents → report) |
| 23 | Sharia | v1 records screening inputs as tagged fields; the screen itself comes later |

### How the agent instructions are reconciled with the brief

| Topic | Agent instructions say | v1 rule |
| --- | --- | --- |
| Years | At least 5 | Up to 5 where available, minimum 3 |
| Ratings | Three ratings incl. Green to Red | Keep; each lists its driving findings; never implies fraud |
| Section L (questions) | Short list | Replaced by the brief's format: observation, question, why I'm asking, hypotheses (with confirming or ruling-out evidence), materiality and type, who answers |
| Quarterly, interim, TTM | Required where available | Rules kept, switched off in v1 (NGN Market quarterly data may switch them on later) |
| ROIC vs cost of capital | Where an estimate exists | v1 shows ROIC and its trend only |
| Sector modules | Seven sectors | v1 ships consumer goods and cement; the rest return "not yet supported" |
| Forensic scores | Not named | Beneish M-score, Altman Z'' (emerging markets), Sloan accruals, read against peers and never alone (devaluation years distort Beneish) |
| Citations | Primary sources for material conclusions | Every number and finding: document, page, note; quotes code-verified |
| Standardised vs adjusted | Separate reported from calculated | Scorecards B–D show standardised and adjusted side by side |
| Structured providers | Source rank 5 | NGN Market (and Kobo, if adopted) at rank 5; always reconciled to annual reports for core companies |

---

## 3. Spec (v1)

### In scope

- **Classification:** GICS sector (11), industry group (25) and sub-industry where it changes the analysis, with reason and confidence. Conglomerates show a segment split, and major segments are analysed separately.
- **Market-wide data layer (new, early):** every NGX-listed company from NGN Market (full history) is loaded into DuckDB and mapped to GICS. That gives industry ratio comps (median, quartiles, count) by GICS industry group and sub-industry, by year.
- **Core-company deep analysis:** annual-report PDFs for the 2 core companies are extracted to a corporate chart of accounts, checked, and reconciled to NGN Market. Disagreements are flagged, never silently resolved.
- **Integrity checks:** the balance sheet balances; the cash flow reconciles to balance-sheet movements; notes sum to the face statements; comparatives match the prior-year report (a mismatch may be an undisclosed restatement).
- **Data-quality grade:** High, Moderate or Low, shown at the top of each report.
- **Three outputs per company:** standardised, analyst-adjusted and USD-translated, all traceable to source.
- **Analysis:** agent instructions sections 1–18 (Appendix A), with the reconciliations above, plus the brief's Nigeria-specific checks: naira devaluation and inflation effects, FX gains inside operating profit, import dependence, energy and diesel costs.
- **Benchmarking:** NGX peers first (from the market-wide layer), then emerging-market data where Nigerian peers are too few. The report shows the peer set, source and date, and where the company sits (above, near or below) and why. Outliers are judged against peers and trends, not fixed global cut-offs.
- **Report:** sections A–M as Markdown, plus a JSON with one record per finding. Fields: id, section, observation, numbers (each with source), question, why, hypotheses, materiality (high, medium, low), type (risk, opportunity, trend, market concern), who answers (management, public sources, field check), status (open or answered), version.
- **Feedback loop:** answers or new documents → re-run the affected findings → new version + "what changed and why". Unanswered questions stay open.
- **Sharia inputs:** interest-bearing debt, interest income, cash and interest-bearing deposits, and non-compliant income where disclosed, all as tagged fields.
- **Back-test:** one case study on a company 1–2 years before a known event (restatement, qualified opinion, regulator action, auditor resignation) against matched peers. It is judged on whether the agent raised the right questions, not on "prediction".

### Out of scope for v1

Mode B, banks and insurers, quarterly/interim/TTM analysis, cost-of-capital comparison, full inflation restatement, the Sharia screen itself, local models, Fable review, paid hosting, and any data source beyond NGN Market Premium.

### Pipeline

1. **Collect:** NGN Market exports (all NGX) and annual-report PDFs (core companies), each with a source record: URL or export name, download date, SHA-256 fingerprint.
2. **Load (market layer):** NGN Market data → DuckDB with checks on units, currency, period length, year-end and duplicates.
3. **Classify:** Haiku drafts GICS for every company with a one-line reason; Bello approves in `config/gics_map.yaml`.
4. **Extract (core):** Docling or pdfplumber pull the statement tables. No model.
5. **Map:** Haiku maps lines to `rules/chart_of_accounts/corporate.yaml`.
6. **Validate:** the four integrity checks, reconciliation to NGN Market, review sheet, data-quality grade.
7. **Calculate:** ratios, adjustments, USD view and forensic scores, all from `rules/` and all in code.
8. **Benchmark:** GICS peer comps from the market layer.
9. **Write:** Sonnet turns triggered rules and numbers into the A–M report and findings, with citations. Code checks the quotes.
10. **Review:** Opus reviews the most material findings and questions.
11. **Feedback:** answers re-run the affected findings; the run is versioned.

### v1 is done when

- The market-wide layer covers all NGX companies, with GICS-mapped industry comps.
- 2 core reports with JSON pass Bello's review, with every quote code-verified.
- Integrity checks pass, or failures are explained, for all 6 core company-years.
- One back-test case study is written.
- The Streamlit demo runs on sample data, and the answer-and-re-run loop works.

---

## 4. Data sources

**Source hierarchy (from the agent instructions):**
1. audited annual reports
2. official interims
3. NGX announcements and filings
4. company presentations
5. approved structured providers
6. other credible sources

| Source | Use in v1 | Notes |
| --- | --- | --- |
| **NGN Market (Premium)** | All NGX companies: income statement, balance sheet, cash flow and ratios, quarterly and annual, full history. Basis of GICS industry comps | Premium includes full history and Excel/CSV download. A Developer API exists but may be priced separately; check whether Bello's plan includes it. **Read the Terms of Service before publishing anything derived from it.** Rank 5 source: reconcile to annual reports for the core companies |
| Annual reports (PDF) | Core companies, full notes-level analysis | From NGX disclosures or company IR pages; linked, never copied into the repo |
| Kobo terminal | Pending | Bello to share a link; check export/API and terms first |
| Emerging-market industry data (e.g. Damodaran's free datasets) | Regional layer where NGX peers are too few | Annual updates; cite the version |
| **Mode B list (later)** | NGX disclosures, company IR pages, SEC Nigeria, FRC Nigeria, CBN and NBS for macro, news via web search | Every document logged; site terms respected |

---

## 5. Tools and connectors

**Installed on the Mac in Session 1 (Claude Code guides each command):**
- Homebrew (Mac package manager)
- git and the GitHub CLI (`gh`), logged in to Bello's GitHub
- `uv` and Python 3.12 (Python and package management)
- VS Code (to read and edit files, especially `rules/`)
- Claude Code (already in use), signed in with the Pro plan
- DuckDB command-line tool (to look inside the database)
- gitleaks + pre-commit (block secrets before every commit)

**Python packages (managed by `uv` in `pyproject.toml`):** `duckdb`, `pandas`, `openpyxl`, `pdfplumber`, `docling`, `pydantic`, `pyyaml`, `python-dotenv`, `claude-agent-sdk`, `streamlit`, `pytest`.

**Connectors (MCP servers) for Claude Code:**
- **Firecrawl:** fetch annual reports and NGX disclosure pages; log every URL.
- **GitHub:** through `gh` (repo creation, settings, issues).
- **NGN Market:** CSV/Excel exports that Bello downloads into `data/raw/ngnmarket/`, or the Developer API with the key in `.env`, if his plan includes it.

**GitHub repo settings:** public; secret scanning and push protection on; `main` branch only for now.
