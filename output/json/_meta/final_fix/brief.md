# Final fix: independent page read (one reader of three)

You are one of three independent readers. Do NOT look at any other reader's folder (`read1/`, `read2/`, `read3/` other
than your own output file) or at any decision / correction file. Read only: your batch file, the trimmed PDFs, and
(optionally) the report JSON to locate a line. Change NO file except your one output file.

## Input
`output/json/_meta/final_fix/batches/batch_NNN.json`: a list of items. Each item is one line of a report
(`stem` -> PDF `trimmed/<stem>.pdf`; `source_page` = page inside that trimmed PDF; `label`, `printed_label`, `amount`,
`fund`, `department`, `fund_type`, `section`, `category` = what the data holds now) and ONE `question`.
`same_label_other_years` shows how the same label is recorded in the same entity's other years: a difference is a
reason to look carefully at this page, never a reason to copy a category.

## What to do for each item
Open the PDF page (Read tool with `pages`). Find the row by label, amount and fund column (look at neighbouring pages
if it is not on the cited page). Answer ONLY the item's question. Nothing else about the line is in question.

### question = "section"  (schedule lines now filed under "other financing")
Report the printed heading the row sits under, word for word (`printed_heading`), and decide `section`:
- `revenue`: the row is printed among Receipts / Revenues / Operating receipts / Nonoperating receipts or revenues
  (for a mixed heading "Nonoperating receipts (disbursements)" a money-in row is `revenue`).
- `expenditure`: printed among Disbursements / Expenditures / Expenses / Nonoperating expenses or disbursements
  (e.g. interest paid on debt printed in the disbursements block or as a nonoperating expense).
- `other financing`: printed under a heading that says Other financing sources (uses), or printed after an
  "income (loss) before contributions and transfers" line (capital contributions, contributed capital, transfers,
  special items, annexation), or it is borrowing / debt proceeds.
- `not found`: you cannot find the row.
If `revenue` or `expenditure`, also give `category` (the high-level Category the line should carry there) using the
project's existing category names (CLAUDE.md lists them): e.g. Interest income -> Interest Income; Donations ->
donations/contributions; "Grants and donations" / mixed grant-donation label with no grantor -> donations/contributions;
plain Grants with no grantor -> Grants; a label naming a federal source (FEMA, ARPA, CDBG, federal) -> federal grants;
a label naming the State -> intergovernmental/state aid; TIF receipts -> property tax; interest paid on debt ->
Debt Service; anything generic -> other. Also give `amount_printed` (the number as printed, positive unless the page
prints it in brackets within its own block).

### question = "category"
Decide whether the current `category` is right for what the page prints for this row. Give `category`: the SAME text
as the current category if it is right or if the page does not show enough to say otherwise; a different existing
category name only when the page itself shows what the money is. Rules (the user's):
- The PDF page decides. Be gentle: a generic label (Local, Other, Miscellaneous, Local Receipts: Other, Operating)
  keeps its current category unless this page prints what the money is. A fund column name alone is not enough,
  except for a line whose own label says what it is.
- Spending lines follow the function heading the report prints them under. A line labelled only "Operating" with no
  printed function is category Operating.
- Mixed grant/donation labels with no grantor: donations/contributions. Plain grant with no grantor: Grants.
- Casino / gaming tax stays keno/lottery. Compensated absences: never change.
- Tax lines: a specific tax type only when the label or a printed breakdown says it; otherwise `taxes`.
- Balance-sheet lines: Cash and Cash Equivalents, Investments, cash and investments (only for a line combining both),
  restricted assets (only when the row is printed as restricted), Inventories, capital assets, accrued liabilities,
  other liabilities, debt - due within one year, unearned revenue, Deferred Inflows of Resources ... as the row and
  its heading print it.
- Use an existing category name; do not invent one.

### question = "breakdown" (Bartlett_FY2024 only)
For the item's `fund`, list in `rows` every printed disbursement row of Exhibit A page 2 (trimmed p4) in that fund's
column as [{"label","amount"}], excluding transfer rows and the total row; give `draft_total` (line 52 on that page)
and `final_total` (line 23 "Total Disbursements & Transfers" on trimmed p1, the page that is NOT crossed out) for the
fund, and `transfers_out` printed for that fund on p4.

## For every item also give
- `evidence`: one short sentence quoting what the page prints (heading, row label, amount, page).
- `confidence`: `certain` or `unsure`. Use `unsure` whenever the page is unreadable, the row is not found, or the
  answer is a judgement call. Unsure answers are not applied.
- `noticed`: anything else you see wrong on that line (amount, label), or "" - it will NOT be changed, only listed.

## Output
Write `output/json/_meta/final_fix/read<K>/batch_NNN.json` (K = your reader number): a JSON list, one object per item:
{"id", "section" (section questions only), "category", "amount_printed", "printed_heading", "rows"/"draft_total"/
"final_total"/"transfers_out" (breakdown only), "evidence", "confidence", "noticed"}. Every item id in the batch must
appear exactly once. Then reply with one line: `batch NNN reader K: <n> items, <m> differ from current, <u> unsure`.
