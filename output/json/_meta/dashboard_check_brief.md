# Dashboard category check (2026-11-18): first page read

The user's dashboard showed label/category pairs that look inconsistent. `output/json/_meta/dashboard_check.csv` lists
the flagged lines (columns: entity, stem, where, fund, department, label, printed_label, amount, source_page,
current_category, reason, prior_page_check). For each entity you are given, read every one of its rows on the page
(`trimmed/<stem>.pdf`, page = source_page; also look at the statement of activities, the fund statement headings, the
notes and schedules in the same trimmed PDF, and the same entity's other years) and decide the category.

Rules (the project's CLAUDE.md applies; the PDF page decides):
- Change a category ONLY when the page shows what the money is. If the page prints nothing more than the label, keep
  the current category. Do not copy another report's or another year's category without this report's page supporting it.
- A row with a `prior_page_check` was already decided from the page by earlier readers: keep it unless you find it is
  clearly wrong on the page (say exactly why).
- Use category names already in use (see CLAUDE.md lists and other lines in the same JSON). Guidance per reason:
  - Airport spending: a spending line printed as its own "Airport" function/department -> `Airport`; keep `other` only
    if the page shows it is something else.
  - Power district lease (NPPD / public power lease payments for the electric distribution system) -> `Rentals`,
    unless the page itself calls it a franchise or occupation tax/fee.
  - Keno/lottery spending: the printed function decides (under a Community betterment/development heading ->
    `Community Betterment/Development`; under General government -> `general government`; ...). A bare "Keno" /
    "Lottery" expense line with no function printed stays `other`.
  - Casino / gaming tax receipts: if the page or notes show it is casino or racetrack gaming tax (not keno/lottery)
    -> `Gaming Tax`; keno or lottery proceeds stay `keno/lottery`.
  - Generic label under keno/lottery (Charges for services, Operating revenue, Miscellaneous ...): keep only if the
    page shows it is the keno/lottery fund's keno proceeds.
  - "Contributions and grants" (and similar mixed labels): user ruling: mixed grant/donation labels are
    `donations/contributions` unless the report prints a breakdown that adds up exactly. Under state/federal aid they
    stay there only if the page shows the money comes from a government.
  - Odd labels under state aid, "Intergovernmental" under federal grants, payroll taxes as a receipt, Public Health
    and Safety, Animal Control, Aging Services, Other Public Services, TIF payments (printed under Debt service ->
    `debt service`; printed as a current function -> `economic development`; "Developer Purchased TIF Payments" is
    its own category): the page heading / function / fund the line is printed under decides.
- Never change an amount or a label. Categories only.

Write `output/json/_meta/dash_decisions/<entity>.csv` with one row per flagged row, columns exactly:
`stem,where,fund,department,label,amount,source_page,old_category,category,verdict,evidence`
(copy stem, where, fund, department, label, amount, source_page from dashboard_check.csv unchanged; old_category =
current_category; category = your decision (= old_category when keeping); verdict = `keep` | `change` | `unclear`;
evidence = page number and the printed words that decide it, one short sentence).

Write only your own files. No git, no scripts. Final message: one line per entity:
`<entity>: N rows, K keep, C change, U unclear` plus, for each change, `label old -> new`.
