# FSA Agent: Plan

## 6. Repo structure

```text
fsa-agent/
├── CLAUDE.md                  working agreement + non-negotiable rules
├── README.md                  CV-facing: what it is, how it works, case studies
├── pyproject.toml
├── .env.example               names of secrets, never values
├── .gitignore                 data/, reports/, .env
├── .pre-commit-config.yaml    gitleaks
├── config/
│   ├── companies.yaml         tickers, year-ends, core vs peer, conflict check pass/fail
│   ├── gics_map.yaml          GICS per NGX company, reason, confidence, approved_by
│   ├── models.yaml            model per step
│   └── sources.yaml           allowed sources (Mode B later)
├── rules/                     BELLO'S: the analysis layer
│   ├── chart_of_accounts/corporate.yaml
│   ├── ratios.yaml            formula, definition, N/M conditions
│   ├── adjustments.yaml       analyst adjustments, each switchable
│   ├── findings/              triggers and materiality per section
│   ├── sectors/               cement.yaml, agriculture.yaml
│   └── agent_instructions.md
├── src/fsa/                   CLAUDE'S: the plumbing
│   ├── ingest/                NGN Market loader, PDF fetcher, source log
│   ├── classify/              GICS drafting and approval
│   ├── extract/               PDF statement tables
│   ├── map/                   line items → chart of accounts
│   ├── validate/              integrity checks, reconciliation, review sheet, quality grade
│   ├── warehouse/             DuckDB schema and access
│   ├── analyse/               ratios, adjustments, USD view, forensic scores, comps
│   ├── findings/              rules engine, writer, quote checker
│   ├── review/                Opus review
│   ├── report/                Markdown, JSON, ratings
│   ├── feedback/              answers, re-runs, versions
│   └── cli.py                 one entry point: company + documents → report
├── schemas/report.v1.json     the format the underwriting agent will read
├── app/streamlit_app.py       industry comps, report viewer, answer form
├── tests/                     known-answer tests for ratios and checks
├── docs/                      spec, plan, method, case studies (published)
├── data/                      NOT PUSHED: raw/, interim/, warehouse/fsa.duckdb
└── reports/                   NOT PUSHED: <ticker>/<version>/report.md, report.json, changelog.md
```

---

## 7. Week-by-week plan (12 weeks, about 10 hours a week)

The market-wide NGN Market layer comes first, so there's a visible result (GICS industry comps) by week 3. The riskiest weeks are 4–5 (PDF extraction). If they slip, the back-test moves to week 13; the review steps are never cut.

| Week | Goal | Bello (about 10 h) | Claude | Done when |
| --- | --- | --- | --- | --- |
| 1 | Setup, security, data in | Conflict check (pass/fail); install tools; download NGN Market exports for all NGX companies (full history); read NGN Market's Terms of Service | Repo scaffold, CLAUDE.md, secrets lock-down; source log; fetch core-company annual reports FY2023–FY2025 | Repo live with first commit; NGN Market exports and 9 core PDFs on the Mac, each logged |
| 2 | Market warehouse + GICS | Approve GICS mapping (Claude drafts every company with a reason) | NGN Market → DuckDB; checks on units, periods, year-ends, duplicates; GICS drafting | All NGX companies in DuckDB, each with approved GICS |
| 3 | Industry comps v0 | Decide ratio definitions and N/M rules in `rules/ratios.yaml` | Ratio engine with tests; median, quartiles and count by GICS industry group and sub-industry and year; first Streamlit page | Comps table and page for every GICS industry group with enough companies |
| 4 | Core extraction | Spot-check pulled tables against the PDFs | PDF extraction, starting with Dangote Cement FY2025; draft corporate chart of accounts | All 4 statements pulled for one company-year; chart of accounts signed off |
| 5 | Mapping, checks, reconciliation | Approve review sheets; judge the NGN Market vs PDF differences | Haiku mapping; 4 integrity checks; reconciliation to NGN Market; data-quality grade | 9 core company-years in DuckDB, checks passed or explained |
| 6 | Adjusted and USD views, forensic scores | Decide each analyst adjustment | Adjustments, USD translation, Beneish, Altman Z'', Sloan; Sharia input fields | Three separate views per core company; scores vs GICS peers |
| 7 | Findings engine | Write triggers and materiality rules | Rules engine; Sonnet writer; quote checker | Draft findings in the brief's format for Dangote Cement |
| 8 | Report + JSON + review | Review the first full report hard | A–M report, ratings, `report.v1.json`, Opus review | One complete report Bello would show |
| 9 | Both core companies | Review Presco | Fix what reviews find | 2 reports pass review |
| 10 | Streamlit app | Test answer and re-run | Report viewer, answer form, re-run, version log; sample-data demo | A finding updates after an answer, with a logged version |
| 11 | Back-test | Choose the case (older-year data must exist; NGN Market history helps) | Run the pipeline on the case company and matched peers | Back-test results ready |
| 12 | Publish | Write the case study and method page; final README for the CV | Deploy the demo; tidy the repo | Public repo, live demo, case study and method page |

---

## 8. Session 1: the first task (about 2 hours)

The goal is a safe, working setup with data starting to land. No analysis yet.

**Before the session (Bello, on his own):**
- [ ] Conflict check for Dangote Cement and Presco. Bring only pass or fail.
- [ ] Log in to NGN Market and check two things: (a) how to export all companies' statements to CSV/Excel, and whether the plan includes the Developer API; (b) what the Terms of Service say about redistribution and derived data.

**In the session (Claude Code leads, one step at a time):**
- [ ] Install: Homebrew, git, `gh` (log in), `uv` + Python 3.12, VS Code, DuckDB CLI, gitleaks, pre-commit.
- [ ] Create the **public** GitHub repo `fsa-agent`; turn on secret scanning and push protection.
- [ ] Lock down secrets before the first commit: `.gitignore` (`data/`, `reports/`, `.env`), `.env.example`, gitleaks pre-commit hook. Test it with a fake key to show it gets blocked.
- [ ] Split this file into `CLAUDE.md`, `docs/spec.md`, `docs/plan.md`, `rules/agent_instructions.md`; the original handoff and brief stay in the git-ignored `private/` folder; create the folder structure; first commit and push.
- [ ] Set up Firecrawl as a connector in Claude Code (key in `.env` only, if one is needed).
- [ ] Fetch Dangote Cement FY2023, FY2024 and FY2025 annual reports into `data/raw/DANGCEM/<FY>/`, each with `source.json` (URL, date, SHA-256).
- [ ] Bello checks the 3 PDFs: full annual report, audited, auditor's report included, right year.
- [ ] Put the first NGN Market export into `data/raw/ngnmarket/` and log it. Loading waits for week 2.

**Done when:** the public repo has its first commit with secret protection proven to work, and 3 checked Dangote Cement PDFs plus the first NGN Market export are on the Mac, logged.

---

## 9. Open items

- Kobo terminal: link, export/API, terms.
- NGN Market: API inclusion and redistribution terms (Session 1).
- Back-test case selection (week 11). The brief's example is Oando's restated 2013–2014 accounts; this depends on older documents being obtainable.
- A legal check before publishing findings that name real companies (not legal advice).
- Employer policy on outside work and publications (Bello to check).
