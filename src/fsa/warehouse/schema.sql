-- FSA Agent warehouse. One database for every sector: sector/industry are labels, not separate databases.
-- Rule 7: a missing value is stored as NULL with quality_flag = 'missing'. It is never stored as zero.

CREATE TABLE IF NOT EXISTS companies (
    ticker            VARCHAR PRIMARY KEY,
    name              VARCHAR,
    role              VARCHAR,          -- core | peer
    gics_sector       VARCHAR,          -- filled in at the GICS approval step (config/gics_map.yaml)
    gics_industry_group VARCHAR,
    gics_industry     VARCHAR,
    gics_sub_industry VARCHAR,
    conflict_check    VARCHAR,          -- pass | fail | pending (nothing else, rule 3)
    fiscal_year_end   VARCHAR
);

-- Every file we load, with where it came from. Nothing enters the database without a row here.
CREATE TABLE IF NOT EXISTS source_files (
    source_id     VARCHAR PRIMARY KEY,   -- first 16 chars of sha256
    sha256        VARCHAR NOT NULL,
    file_name     VARCHAR NOT NULL,
    provider      VARCHAR NOT NULL,      -- e.g. ngnmarket
    ticker        VARCHAR,
    view          VARCHAR,               -- annual | quarterly
    downloaded    DATE,
    loaded_at     TIMESTAMP DEFAULT current_timestamp
);

-- Statement lines as exported (Income Statement, Balance Sheet, Cash Flow). One row per value.
CREATE TABLE IF NOT EXISTS statement_lines (
    ticker        VARCHAR NOT NULL,
    period_type   VARCHAR NOT NULL,      -- FY | Q
    fiscal_year   INTEGER NOT NULL,
    fiscal_quarter INTEGER,              -- 1-4 for Q, NULL for FY
    period_end    DATE,
    statement     VARCHAR NOT NULL,
    metric        VARCHAR NOT NULL,
    value         DOUBLE,                -- NULL when missing
    unit          VARCHAR,               -- NGN | NGN_per_share
    quality_flag  VARCHAR NOT NULL,      -- ok | missing
    source_id     VARCHAR NOT NULL
);

-- The provider's own ratios. Kept apart from ours: we compute every ratio in code from rules/ratios.yaml
-- and use these only to cross-check.
CREATE TABLE IF NOT EXISTS provider_ratios (
    ticker        VARCHAR NOT NULL,
    period_type   VARCHAR NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    fiscal_quarter INTEGER,
    period_end    DATE,
    metric        VARCHAR NOT NULL,
    value         DOUBLE,
    unit          VARCHAR,               -- pct | x | days | NGN_per_share
    quality_flag  VARCHAR NOT NULL,
    source_id     VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS dividends (
    ticker        VARCHAR NOT NULL,
    payment_date  DATE,
    ex_dividend_date DATE,
    amount_ngn    DOUBLE,
    dividend_type VARCHAR,
    yield_pct     DOUBLE,
    source_id     VARCHAR NOT NULL
);

-- A newer download of the same company and view supersedes the older one. Older rows are kept
-- (a changed past value can reveal a restatement) but queries should use the *_current views.
ALTER TABLE source_files ADD COLUMN IF NOT EXISTS is_current BOOLEAN DEFAULT true;

CREATE OR REPLACE VIEW statement_lines_current AS
    SELECT s.* FROM statement_lines s JOIN source_files f USING (source_id) WHERE f.is_current;
CREATE OR REPLACE VIEW provider_ratios_current AS
    SELECT r.* FROM provider_ratios r JOIN source_files f USING (source_id) WHERE f.is_current;

-- Statement lines extracted from an audited annual-report PDF (no model involved: code only).
CREATE TABLE IF NOT EXISTS pdf_statement_lines (
    ticker        VARCHAR NOT NULL,
    fiscal_year   INTEGER NOT NULL,      -- the report's year
    statement     VARCHAR NOT NULL,      -- profit_or_loss | comprehensive_income | financial_position | cash_flows
    seq           INTEGER NOT NULL,      -- order of the line on the page
    section       VARCHAR,               -- last heading above the line, e.g. "Current liabilities"
    label         VARCHAR NOT NULL,      -- as printed ('' for an unlabelled subtotal)
    note_ref      VARCHAR,
    entity        VARCHAR NOT NULL,      -- group | company
    period_end    DATE NOT NULL,
    value         DOUBLE,                -- in the statement's unit; NULL if not readable
    unit          VARCHAR,               -- NGN_million | NGN_per_share
    value_flag    VARCHAR NOT NULL,      -- ok | reported_nil (printed as a dash) | unreadable
    page          INTEGER NOT NULL,
    source_id     VARCHAR NOT NULL
);

-- NGN Market vs the audited PDF. Differences are recorded, never silently resolved.
CREATE TABLE IF NOT EXISTS reconciliation (
    ticker        VARCHAR NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    provider_metric VARCHAR NOT NULL,
    pdf_label     VARCHAR NOT NULL,
    pdf_value     DOUBLE,                -- NGN
    provider_value DOUBLE,               -- NGN
    difference    DOUBLE,
    status        VARCHAR NOT NULL,      -- match | differs | not_comparable
    checked_at    TIMESTAMP DEFAULT current_timestamp
);

-- Results of arithmetic checks on an extracted statement.
CREATE TABLE IF NOT EXISTS integrity_checks (
    ticker        VARCHAR NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    statement     VARCHAR NOT NULL,
    entity        VARCHAR NOT NULL,
    period_end    DATE NOT NULL,
    check_name    VARCHAR NOT NULL,
    expected      DOUBLE,
    actual        DOUBLE,
    status        VARCHAR NOT NULL,      -- pass | fail
    checked_at    TIMESTAMP DEFAULT current_timestamp
);

-- Which chart-of-accounts code each printed line maps to. Two independent proposers (a rule baseline and a model)
-- are compared; disagreements and unmapped lines go to the analyst. Nothing is 'approved' without the analyst.
CREATE TABLE IF NOT EXISTS line_mappings (
    ticker        VARCHAR NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    statement     VARCHAR NOT NULL,
    seq           INTEGER NOT NULL,
    section       VARCHAR,
    label         VARCHAR,
    proposer      VARCHAR NOT NULL,      -- rule | haiku | analyst
    code          VARCHAR,               -- chart account code; NULL = no suggestion
    confidence    VARCHAR,               -- high | medium | low (model) / exact | section (rule)
    reason        VARCHAR,
    model_used    VARCHAR,               -- the model that actually answered (from the usage report), never the one requested
    proposed_at   TIMESTAMP DEFAULT current_timestamp
);

-- The line mapping we actually use, and how each line got its code.
CREATE TABLE IF NOT EXISTS mapping_final (
    ticker        VARCHAR NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    statement     VARCHAR NOT NULL,
    seq           INTEGER NOT NULL,
    label         VARCHAR,
    code          VARCHAR,
    basis         VARCHAR NOT NULL,      -- analyst_decision | agreed | rule_only | model_accepted | rule_disputed | open
    status        VARCHAR NOT NULL,      -- settled | open
    rule_code     VARCHAR,
    model_code    VARCHAR,
    note          VARCHAR
);

-- Everything a reader of the report should be told about the data: restatements, differences, gaps, basis of preparation.
CREATE TABLE IF NOT EXISTS report_disclosures (
    ticker        VARCHAR NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    seq           INTEGER NOT NULL,
    category      VARCHAR NOT NULL,
    severity      VARCHAR NOT NULL,      -- standard | note | important
    text          VARCHAR NOT NULL,
    source_ref    VARCHAR,
    generated_at  TIMESTAMP DEFAULT current_timestamp
);

-- Ratios computed by code from the settled mapping, with the inputs behind every number (rule 4: every number cites its source).
CREATE TABLE IF NOT EXISTS ratios (
    ticker        VARCHAR NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    ratio_id      VARCHAR NOT NULL,
    category      VARCHAR,
    unit          VARCHAR,
    value         DOUBLE,                -- NULL when not meaningful
    status        VARCHAR NOT NULL,      -- ok | N/M
    reason        VARCHAR,               -- why N/M
    inputs        VARCHAR,               -- JSON: the account values used (N millions), current and opening
    rules_version VARCHAR,
    computed_at   TIMESTAMP DEFAULT current_timestamp
);

-- Industry comparison. Every company in a group is computed from the SAME provider data, so definitions and rounding match.
CREATE TABLE IF NOT EXISTS comparison_ratios (
    ticker        VARCHAR NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    ratio_id      VARCHAR NOT NULL,
    unit          VARCHAR,
    value         DOUBLE,
    status        VARCHAR NOT NULL,      -- ok | N/M
    reason        VARCHAR
);
CREATE TABLE IF NOT EXISTS industry_stats (
    industry      VARCHAR NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    ratio_id      VARCHAR NOT NULL,
    n             INTEGER NOT NULL,      -- companies with a meaningful value
    n_members     INTEGER NOT NULL,      -- companies in the industry
    median        DOUBLE, q1 DOUBLE, q3 DOUBLE, minimum DOUBLE, maximum DOUBLE
);
CREATE TABLE IF NOT EXISTS peer_position (
    ticker        VARCHAR NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    ratio_id      VARCHAR NOT NULL,
    value         DOUBLE,
    peer_n        INTEGER,
    peer_median   DOUBLE,
    peer_min      DOUBLE,
    peer_max      DOUBLE,
    position      VARCHAR NOT NULL,      -- above | near | below | no peers | not meaningful
    method        VARCHAR,
    small_set     BOOLEAN
);

-- Forensic indices, scores and red-flag tests. Indicators to examine, never verdicts, never read alone.
CREATE TABLE IF NOT EXISTS forensic_scores (
    ticker        VARCHAR NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    item_id       VARCHAR NOT NULL,
    unit          VARCHAR,
    value         DOUBLE,
    status        VARCHAR NOT NULL,      -- ok | N/M
    reason        VARCHAR,
    band          VARCHAR,               -- the published zone for a score
    flagged       BOOLEAN,               -- for a red-flag test: did it trip the threshold
    inputs        VARCHAR,
    rules_version VARCHAR
);

-- Dry-run output of the findings trigger rules (rules/findings/). A candidate is not a finding until written, quote-checked and reviewed.
CREATE TABLE IF NOT EXISTS finding_candidates (
    ticker        VARCHAR NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    rule_id       VARCHAR NOT NULL,
    title         VARCHAR,
    section       VARCHAR,
    state         VARCHAR NOT NULL,      -- fired | not fired | not tested
    materiality   VARCHAR,               -- high | medium | low | unrated
    measure       DOUBLE,
    reason        VARCHAR,               -- why not tested, or why unrated
    facts         VARCHAR,               -- JSON: every input the trigger used
    rules_version VARCHAR,
    evaluated_at  TIMESTAMP DEFAULT current_timestamp
);

-- Macro data (contract: docs/macro_interface.md). Written by the loader today and by the future Macro Agent; the FSA Agent only reads.
CREATE TABLE IF NOT EXISTS macro_series_catalog (
    series_id     VARCHAR PRIMARY KEY,
    name          VARCHAR NOT NULL,
    definition    VARCHAR NOT NULL,      -- includes what the series is NOT
    unit          VARCHAR NOT NULL,
    frequency     VARCHAR NOT NULL,
    basis_note    VARCHAR
);
CREATE TABLE IF NOT EXISTS macro_vintages (
    vintage_id    VARCHAR PRIMARY KEY,
    created_at    TIMESTAMP NOT NULL,
    created_by    VARCHAR NOT NULL,
    note          VARCHAR
);
CREATE TABLE IF NOT EXISTS macro_series (
    series_id     VARCHAR NOT NULL,
    period_start  DATE NOT NULL,
    period_end    DATE NOT NULL,
    value         DOUBLE,
    unit          VARCHAR,
    vintage_id    VARCHAR NOT NULL,
    source_name   VARCHAR NOT NULL,
    source_url    VARCHAR,
    retrieved_at  TIMESTAMP,
    source_sha256 VARCHAR,
    quality_flag  VARCHAR NOT NULL,      -- ok | provisional | estimated | break_after
    note          VARCHAR
);
