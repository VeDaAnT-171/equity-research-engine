# Synthetic SEC fixtures

These files follow the structure of SEC EDGAR `companyfacts` and `submissions` responses but contain
**invented numbers for a fictional company** (CIK 9999002, ticker EXBK). They exist to exercise edge cases:
comparatives repeated across filings, a restated prior year, a 9-month YTD value, an 8-K observation,
an off-quarter instant, year-to-date cash flows (Q1/H1/9M/FY), a balance sheet that breaks
the assets = liabilities + equity identity by 5bn (non-controlling-interest pattern), a cash roll-forward that ties, an incompatible unit, a shadowed revenue concept and a malformed row.
They are not, and must never be presented as, real financial data.
