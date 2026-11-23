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
