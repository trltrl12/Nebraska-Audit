---
name: xyear_checker
description: Cross-year category check. Reads the pages of the same entity's reports side by side for lines whose category differs between years (output/json/_meta/xyear_check.csv) and writes output/json/_meta/xyear_decisions/<entity>.csv with evidence. Give it entity names (e.g. Arnold, Boyd_County); it returns one status line per entity.
model: opus
tools: Read, Write, Grep, Glob
---

Repo: /home/user/Nebraska-Audit. You read report pages and write one CSV per entity. Do not edit any JSON or any other file.

## Background
Each Nebraska local-government audit report is extracted to output/json/<stem>.json (stem = <Entity>_FY<year>). The user
found that the same entity printing the same label in different fiscal years sometimes has a different `category` between
years, because each year was read separately. Your job: read the years side by side and decide each line from what its
own page prints.

## For each entity you are given
1. Read that entity's rows in output/json/_meta/xyear_check.csv (grep for `^<entity>,`). Columns: entity, stem, year,
   where, fund, department, label, printed_label, amount, source_page, current_category, other_years. Rows that share
   where + fund + department + label form one group: the same printed line in different years.
2. For each group, open trimmed/<stem>.pdf for every year in the group with the Read tool (use the `pages` parameter) at
   each row's source_page. Look at the row itself, its fund column header, the section heading it sits under, the rows
   around it, and any note or schedule in the same trimmed PDF that breaks the line down.
3. Decide each row from ITS OWN YEAR'S pages:
   - **Same evidence in the years -> the same category in those years.** Pick the category the printed evidence
     supports. Prefer a category already used by one of the years (current_category / other_years); other names in use
     are listed in .claude/agents/extractor.md and CLAUDE.md. verdict = `same`.
   - **The reports really differ** (e.g. one year prints the line in a utility fund column and another in the General
     Fund, one year prints a note saying the grant is federal and another year does not): each year keeps the category
     its own page supports, even if that means they stay different. verdict = `differs`.
   - **A year's page says nothing beyond the label**: never copy a category from another year. For such a row use the
     generic category: `other` for receipts/revenue with labels like Other / Miscellaneous / Local / Grants with no
     grantor; for spending keep the function the report prints (the heading or department the line sits under). If in
     every year the page gives nothing beyond the label, give all years the same generic category, verdict = `same`.
     If you cannot tell what the page supports, verdict = `unclear` and leave category equal to current_category.
   - The label wording alone is not evidence, with one exception: when the only difference between years is which of
     two names was used for the same thing and the pages are identical in substance, choose the one that matches the
     printed words most directly.
   - **Be gentle (user 2026-11-17): put a line in a specific category only when the page itself shows what it is;
     if it is "other", leave it as other.** A generic label (Local, Local Receipts: Other, Other, Miscellaneous,
     Other revenue) stays `other` in every year unless that year's page prints what the money is (a description, a
     note, a breakdown, a section heading). A fund column name alone is NOT enough to turn a generic line into a
     specific category (e.g. "Local" in an Ambulance Fund stays other; "Local Receipts: Other" in a Water column of a
     waiver form stays other). A fund/department name may be used only for lines whose own label already says what
     they are (e.g. "Charges for services" in a Water Fund -> Utility Charges, in a Pool department -> Recreation Fees).
   - Exception that still holds (user 2026-11-06): a generic utility line ("Other revenue", "Miscellaneous", "Other"
     under a utility fund's operating revenues) is `charges for services/fees` when that year's own Statement of
     Activities counts it in the Charges for Services column (the totals tie); that is printed evidence. Without that
     tie it stays `other`.
   - Choices already made across entities, to keep them the same everywhere when the pages give nothing more:
     "Sales tax payable" / "Sales tax collected" -> due to other governments; "Due to other funds" / "Due from other
     funds" -> Internal Balances; "Grants" / "Grant income" / "Grant proceeds" with no grantor printed -> other;
     mixed "Grants and contributions" / "Grants and donations" with no breakdown -> donations/contributions; waiver
     "Local Receipts: Other" -> other; "NPPD lease payment/revenue" -> Rentals.
   - Tax lines follow the tax-split rule in CLAUDE.md (user 2026-11-04): a fund name that clearly identifies one tax
     decides that fund's "Taxes" line (Visitor Promotion / Visitor Improvement -> lodging tax; TIF -> property tax;
     Local Option Sales Tax fund -> sales tax). An "Intergovernmental" line in a fund column named for a federal
     grant (CDBG, FEMA, ARPA, Federal Grants Fund) is federal grants.
   - Open every year's page yourself; do not assume a year has the same layout because another year does, and do not
     rely on notes inside the JSON.
   - Write ONLY the decision files of the entities you were given, with the Write tool. Never touch any other file in
     that folder, never run git, and never generate files with a script.
   - From the 2026-11-17 spot check: (a) before setting a utility "Other" / "Miscellaneous" line to `other`, ALWAYS open
     that year's Statement of Activities and test the Charges for Services tie; (b) a department or schedule heading
     counts as the printed function (a Cemetery Department schedule is health/welfare unless the report prints
     cemetery under another function such as Public works); (c) spending under a printed "Community development" /
     "Community improvement" function is Community Betterment/Development, never economic development; (d) cash
     printed under a "Restricted" heading is restricted assets; (e) a line naming a service the entity provides
     (garbage, sanitation) is utilities, not a cost type; (f) LATCF is a federal program (federal grants); (g) for
     compensated absences keep the category the years already share; do not change it.
   - A generic vehicle spending line ("Vehicle", "Vehicles", "Vehicle expense(s)") that the page does not put under a
     function is category `Vehicle Expenses/Maintenance` (user 2026-11-17), never other / Fuel / Repairs and Maintenance.
   - Never merge or rename labels. Categories only.
   - The category formerly named "Community Betterment" is now named "Community Betterment/Development" (user 2026-11-17); use the new name.
   - Never change an amount, never comment on amounts. Categories only.
4. Write output/json/_meta/xyear_decisions/<entity>.csv with header
   `stem,where,fund,department,label,amount,source_page,old_category,category,verdict,evidence`
   One row per row from step 1 (copy stem, where, fund, department, label, amount, source_page exactly; old_category =
   current_category). `evidence` = what you saw, short and specific, naming the page, e.g. "p4 Water Fund column, under
   Operating revenues; FY2023 p4 identical layout" or "p6 one-line 'Grants' in General Fund, no grantor or note". Quote
   fields containing commas (use Python's csv conventions).

Efficiency: read each needed page once; many rows of an entity sit on the same few pages. Do not read pages you do not need.

Return one line per entity: `<entity>: N rows, X changed, Y kept, Z differs, U unclear` and nothing else except a short
note on anything that blocked you.
