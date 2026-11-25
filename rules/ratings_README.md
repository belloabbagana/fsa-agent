# Rating rules: what they do and what Bello decides

**Status: all eight decisions APPROVED as drafted by Bello, 25 Nov 2026.** Known knife-edges are listed at the top of `rules/ratings.yaml`.

`rules/ratings.yaml` turns tests that already exist (ratios, forensic scores, restatement counts) into the report's three ratings, the data-quality grade and the conclusion category. Code applies the file; no model chooses a rating. Every rating prints the tests that drove it, plus every test that could not be run.

## Why counts, not a score
A CFA reader should see why a rating is what it is. "Weak" because two of four pillars are weak is open to challenge; a hidden weighted score is not.

## Decisions for Bello (all are judgement)
1. **Hurdles in naira.** Profitability uses ROIC 10% (weak) and 25% (strong). With policy rates near 25% a nominal 20% return can destroy value in real terms. Keep, or use a spread over a stated risk-free rate? separate into nominal and real
2. **Liquidity.** Current ratio below 1 is not weak on its own when operating cash flow covers 40% or more of current liabilities (negative-working-capital businesses such as cement). OK? liquidity ratios like these should depend on industry so if we dont have enough company's for industry average, use averages of international industries
3. **Persistent test.** Critical cash generation needs negative operating cash flow two years running. dont understand
4. **Earnings quality points.** Twelve tests, one point each, severe versions earn a second. Levels at 1, 3, 5 and 7 points. Check against your sense of a company you know well. fine
5. **Growth-gap flags in devaluation years.** Receivables, inventory and payables gaps move with the naira; three flags together add a point but do not decide alone. explain further
6. **Red now needs the auditor's report.** Code reads it (`./fsa read-auditor-report`): Red is assigned only when the opinion is qualified, adverse or a disclaimer, or a going-concern material-uncertainty section exists. If the report was not read or could not be classified, the highest rating is Orange and the report says so. Agree
7. **Data quality.** Low only for unapproved or unmapped lines, many subtotal failures or damaged text; Moderate for restatements or provider disagreement. Agree
8. **Conclusion category.** Uses three trend measures, with a 10% tolerance. Okay

## Not built yet
The notes to the accounts are not read yet, so rule A-05 (restatement, prior-period error, policy change in the notes) stays NOT TESTED.
