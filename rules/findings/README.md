# Finding trigger rules (draft v0.1 by Claude, for Bello to decide)

A **finding** is an item to examine, never an accusation. These files say WHEN the engine raises one, HOW MATERIAL it is, and what
question it asks. The engine only evaluates them; the writer model (Sonnet) turns the facts into prose; the reviewer (Opus) checks the
most material ones. A trigger can never be argued into or out of existence by a model.

## The record each rule produces (rules/spec: observation, question, why, hypotheses, materiality, type, who answers)
| Field in a rule | Becomes |
|---|---|
| `title`, `trigger` | the observation (numbers pulled by code, each with statement, page and the NGN Market or PDF source) |
| `question`, `why` | question and "why I'm asking" |
| `hypotheses` (each with `confirm` and `rule_out` evidence) | hypotheses with the evidence that would confirm or rule them out |
| `materiality` | high, medium or low (below) |
| `type` | risk · opportunity · trend · market concern |
| `who_answers` | management · public sources · field check |
| `section` | which report section (A to M, agent instructions section 17) it feeds; every finding also feeds section L (questions) |

## What a trigger can use
Names are the same as in rules/ratios.yaml: chart-of-accounts codes (`BS_TRADE_RECEIVABLES`), metrics (`EBITDA`, `FCF`), ratio ids in lower case (`dso`),
forensic ids (`beneish_m`, `receivables_growth_gap`), plus suffixes on any ratio id:
`_prev` and `_prev2` (the same ratio one and two years earlier), `_cmp` (the company's value on the peer-comparison basis),
`_peer_median`, `_peer_min`, `_peer_max`, `_peer_n` (peers only, the company excluded).
Also: `restated_lines` (prior-year lines that differ from last year's own report), `later_restated_lines` (this year's lines that next year's report changed), `flags_tripped` (growth-gap and cash flags), `has_acquisition`.
Functions: `prior(X)`, `avg(X)`, `abs(X)`, `min(a, b)`, `max(a, b)`. `and` / `or` stop at the first deciding part. Operators: + - * /, comparisons, `and`, `or`.
**An input that is missing makes the rule NOT TESTED with the reason. It never counts as "no finding".**

## Materiality (decision 1 below)
`measure` is a number computed by code; `high` and `medium` are thresholds such as `">= 10"`; anything else is low.
A rule may instead say `fixed: low|medium|high`, with `escalate_if` raising it to high when another condition also holds (corroboration).
Default scale used throughout, as a share of the base that fits the item:
**earnings items: share of profit before tax: high >= 10%, medium >= 3%. Balance-sheet items: share of total assets: high >= 2%, medium >= 0.5%.**

## Text rules
Rules with `kind: text` search the auditor's report and notes (qualified opinions, going-concern language, emphasis of matter, key audit matters,
restatement and accounting-policy notes). The engine must quote the passage, and code verifies the quote exists in the PDF before the finding is kept.
Not yet built: they show as "not tested".

## DECISIONS FOR YOU (all five APPROVED as drafted by Bello, 25 Nov 2026)
1. **Materiality scale**: the shares above. Stricter (e.g. high >= 5% of PBT) raises more high findings.
2. **Thresholds** inside each rule (for example "receivables outgrow revenue by 10 points"). Each is a judgement. They are listed once per rule so you can change them.
3. **Peer rules** (section K) use the peer median and need at least 2 peers; with so few peers they are always marked indicative and never rated high.
4. **Corroboration**: a forensic score alone is at most medium; it becomes high only when a growth-gap flag or the cash test agrees.
5. **Which rules to switch off**, or add. Ideas not yet coded because they need notes the engine does not read yet: ECL provision movements, segment margins, contingent liabilities, subsequent events, covenant terms, currency mix of debt.
