# Rating rules: what they do and what Bello decides

`rules/ratings.yaml` turns tests that already exist (ratios, forensic scores, restatement counts) into the report's three ratings, the data-quality grade and the conclusion category. Code applies the file; no model chooses a rating. Every rating prints the tests that drove it, plus every test that could not be run.

## Why counts, not a score
A CFA reader should see why a rating is what it is. "Weak" because two of four pillars are weak is open to challenge; a hidden weighted score is not.

## Decisions for Bello (all are judgement)
1. **Hurdles in naira.** Profitability uses ROIC 10% (weak) and 25% (strong). With policy rates near 25% a nominal 20% return can destroy value in real terms. Keep, or use a spread over a stated risk-free rate?
2. **Liquidity.** Current ratio below 1 is not weak on its own when operating cash flow covers 40% or more of current liabilities (negative-working-capital businesses such as cement). OK?
3. **Persistent test.** Critical cash generation needs negative operating cash flow two years running.
4. **Earnings quality points.** Twelve tests, one point each, severe versions earn a second. Levels at 1, 3, 5 and 7 points. Check against your sense of a company you know well.
5. **Growth-gap flags in devaluation years.** Receivables, inventory and payables gaps move with the naira; three flags together add a point but do not decide alone.
6. **Red cannot be assigned yet.** It needs the auditor's report read by code (rules A-01 to A-05). Until then the highest forensic rating is Orange, and the report says so.
7. **Data quality.** Low only for unapproved or unmapped lines, many subtotal failures or damaged text; Moderate for restatements or provider disagreement.
8. **Conclusion category.** Uses three trend measures, with a 10% tolerance.

## Not built yet
The engine that applies this file, the `data_quality` inputs (open mapping lines, subtotals not reproduced, damaged text, provider differences, unchecked peers), the `auditor_adverse` input and the trend counts. Next step: build the engine, run it on all six company-years, and show you the outcomes so you can judge the thresholds.
