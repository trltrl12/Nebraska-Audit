---
name: schedule_fixer2
description: Second pass of schedule_fixer - utility (business-type) fund columns with no schedule lines, bond/loan proceeds and transfers printed in fund columns. Writes output/json/_meta/schedule_fix2/<stem>.json. Give it stems; returns one status line per report.
model: opus
tools: Read, Write, Grep, Glob
---

Follow `.claude/agents/schedule_fixer.md` in full (same method, same file format, same checks), with these changes:

- Write the fix file to `output/json/_meta/schedule_fix2/<stem>.json` (NOT schedule_fix/).
- The gap rows are again in `output/json/_meta/schedule_gap.csv` for the stem. This pass is about:
  1. **A fund type with no schedule lines** (rows where schedule_total is 0): the user wants every fund column the
     statements print to be on the schedule, in every year. For business-type this means each fund column of the
     proprietary funds statement of revenues, expenses and changes in net position (cash-basis reports: the
     proprietary receipts/disbursements statement): add one detail line per printed non-zero row per fund column -
     operating revenues, operating expenses (including depreciation when printed), non-operating revenues and expenses
     (interest income, grants, interest expense ...), capital contributions, and transfers / other financing rows
     (section "other financing", transfers out negative). `fund` = the column heading exactly as printed. Never the
     Total column, never a subtotal/total row, never internal service funds or component units. The added revenue lines
     must add up to the JSON statement receipts total for that fund type and the added expenditure lines to its
     disbursements total (within 3); look at how the JSON statement lines for that fund type were built (which rows
     are in receipts[] / disbursements[]) and put the same rows in section revenue / expenditure, so both tie. If the
     report already prints a schedule for those funds that is simply not in detail[], use that schedule instead.
  2. **debt_proceeds** rows (governmental): bond / loan / lease proceeds printed in a governmental fund column or
     schedule that are not in detail[]. Add each as section "other financing", original_label as printed, in its fund.
  3. **transfers_net** rows (business-type): transfers printed in the proprietary fund columns that are not in
     detail[]. Add each fund's Transfers In / Transfers Out (out negative).
- While you are on the statement pages, also list in the same file (as extra entries with section "other financing",
  cause "missing lines") any other printed non-zero other-financing row of a fund column that is not in detail[]
  (sale of capital assets, insurance recoveries, bond issuance costs ...), and any existing transfer line whose label
  is the wrong way round (a negative amount labelled "Transfers In" or a positive one labelled "Transfers Out"):
  for those, `remove` the mislabelled line and `add` it back with the right label and the same amount.
- Printed amounts only. Never add a derived figure (a remainder or a difference). If a printed row would have to be
  split, leave it and explain. If the only way to tie is a derived figure, the cause is "different basis".
- Do not touch gaps the first pass already explained (different basis for revenue/expenditure where a schedule
  exists); leave them out of your file.

Final message: only one line per stem: `<stem>: +A lines, -R lines, G gaps fixed, E explained, U unresolved`.
