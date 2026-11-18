# Dashboard category check: second, independent page read

`output/json/_meta/dash_verify_queue.csv` lists category changes proposed by a first reader (columns: entity, stem,
where, fund, department, label, amount, source_page, old_category, category (= proposed), evidence (first reader's)).
For each entity you are given, read each row on its page yourself (`trimmed/<stem>.pdf`, source_page; also the
statement of activities, fund statement headings, budget schedules and notes in the same trimmed PDF) BEFORE looking
at the first reader's evidence, decide what the page shows, and only then compare.

Rules (CLAUDE.md applies; `output/json/_meta/dashboard_check_brief.md` has the guidance the first reader used):
- A change stands only when the page shows what the money is. If the page prints nothing beyond the label, the line
  keeps its old category (verdict disagree), except where a user ruling settles it from the label: mixed
  grant/donation labels with no grantor printed are `donations/contributions`; a spending line printed as its own
  "Airport" function is `Airport`; a bare "Keno"/"Lottery" expense line with no function is `other`.
- `Gaming Tax` is a new category for casino / racetrack gaming tax: agree only if the page prints it as a gaming
  tax separate from keno/lottery (own row or own fund next to a keno row/fund); otherwise disagree.

Write `output/json/_meta/dash_verify/<entity>.csv`, one row per queue row, columns exactly:
`stem,where,fund,department,label,amount,source_page,old_category,proposed_category,verdict,my_category,evidence`
(verdict = `agree` | `disagree`; my_category = the category you would give; evidence = page and printed words).

Write only your own files. No git, no scripts. Final message: one line per entity: `<entity>: N rows, A agree, D disagree`
plus one line for each disagreement.
