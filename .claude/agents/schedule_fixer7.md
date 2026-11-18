---
name: schedule_fixer7
description: Pass 7 of the schedule fix - reports whose governmental schedule (detail) still differs by more than 10% from the statement. Finds out exactly why from the pages and writes output/json/_meta/schedule_fix7/<stem>.json. Give it stems; returns one status line per report.
model: opus
tools: Read, Write, Grep, Glob
---

Read `.claude/agents/schedule_fixer3.md` first (file format, printed-amounts rules). Changes for this pass:

- Write to `output/json/_meta/schedule_fix7/<stem>.json`; detail_index values against the CURRENT
  `output/json/<stem>.json`.
- The user's final rule: "put what is on the PDFs into the files; if the PDFs themselves do not tie that is OK; do
  whatever is needed to make it correct and show accurately based on the PDFs". No tie is required. Nothing derived.
- These reports' governmental schedule lines total more than 10% above or below the statement lines (rows of the stem
  in `output/json/_meta/schedule_gap.csv`: statement_total, schedule_total, gap = statement - schedule). Earlier
  passes explained the gap as a basis difference. Do not take that on trust. Re-read every statement and schedule
  page of the trimmed PDF and establish, fund by fund and section by section, where the difference comes from:
  1. **Schedule BELOW the statement (positive gap):** look for a fund column, a statement row or a whole schedule
     page that is printed and has no detail lines (e.g. an "Other Governmental Funds" / nonmajor column, a Debt
     Service or Capital Projects column, rows such as capital outlay / debt service / intergovernmental). Add every
     printed fund-column line that no detail line holds. If the missing funds' own pages are not in the trimmed PDF
     (e.g. nonmajor combining statements), say exactly which statement/schedule is missing (title, and the printed
     page number if the table of contents or a page reference shows it) with cause "page not in trim".
  2. **Schedule ABOVE the statement (negative gap):** look for money counted twice in detail: the same fund present
     from two sources (its budget schedule AND a statement/combining column), a combined all-funds schedule next to
     per-fund lines, subtotal or total rows recorded as lines, a prior-year or budget column read as actual, beginning
     balances or transfers recorded as revenue lines. Remove only true duplicates / non-lines (say which line holds
     the money, or why the line is not a transaction line). If nothing is counted twice and the budget schedule simply
     prints a larger figure than the statement for the same fund (budgetary vs GAAP/cash basis), explain it with both
     printed totals per fund.
  3. Also check the statement side: if a statement line in receipts[]/disbursements[] of the JSON is itself wrong
     against the page (wrong column, missing row), say so exactly (page, row, printed amount) with cause
     "statement issue"; do not change it.
- Every gap row gets an entry with the per-fund arithmetic. Keep explanations factual and short.

Write only your own files. No git, no scripts. Final message: ONLY one line per stem:
`<stem>: +A lines, -R lines, cause in a few words`
