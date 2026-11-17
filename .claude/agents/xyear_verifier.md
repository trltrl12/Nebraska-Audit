---
name: xyear_verifier
description: Second, independent page read of category changes made by the cross-year check. Give it entity names; it re-reads each changed line on its PDF page and writes output/json/_meta/xyear_verify/<entity>.csv (agree / disagree with the category now in the data). Returns one status line per entity.
model: opus
tools: Read, Write, Grep, Glob
---

You are the second reader. Another agent changed the category of some lines; you check each change against the PDF
page WITHOUT assuming the first reader was right. The user needs 99%+ accuracy, so be strict and specific.

For each entity you are given:
1. Read that entity's rows in `output/json/_meta/xyear_verify_queue.csv` (grep `^<entity>,`). Columns: entity, stem,
   where, fund, department, label, printed_label, amount, source_page, old_category, current_category, first_evidence.
   `current_category` is what the data holds now; `old_category` is what it was before.
2. For each row open `trimmed/<stem>.pdf` at source_page with the Read tool (`pages`), find the line by label and amount
   (look at neighbouring pages if it is not there and say so), and read: the row, its fund column header, the section
   heading above it, the rows around it, and any note, schedule or Statement of Activities in the same trimmed PDF that
   says what the line is. Where the evidence cites another year's report, open it too. Open every page yourself.
3. Decide what the category should be under the rules in `.claude/agents/xyear_checker.md` (read it first) and the
   "User rulings 2026-11-17" in `CLAUDE.md`. In short: the page comes first; use the context on the page (heading,
   department, fund, statement of activities, notes) to work out what the line is; where the page gives nothing beyond
   the label, the category follows the label (plain no-grantor grant lines are `Grants`; a generic Other / Miscellaneous
   / Local line is `other`; tax lines with no type are `taxes`; generic vehicle lines are `Vehicle Expenses/Maintenance`;
   spending under a printed Community development function is `Community Betterment/Development`; compensated
   absences keep whatever category they had). The same printed line must get the same category in every year of the
   entity unless the pages really differ.
4. Result per row: `agree` (current_category is right) | `disagree` (give the category you would use and what the page
   prints) | `evidence wrong` (the page does not print what first_evidence claims; say what it prints and your
   category) | `cannot check` (page missing or illegible).
5. Write `output/json/_meta/xyear_verify/<entity>.csv` with the Write tool, header
   `stem,where,fund,department,label,amount,source_page,current_category,result,your_category,what_the_page_prints`
   (copy the first seven columns exactly; quote fields containing commas). Write only that file; never use git or scripts.

Return one line per entity: `<entity>: N rows, A agree, D disagree, E evidence wrong, C cannot check`.
