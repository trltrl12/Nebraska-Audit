# Nebraska audit cash-flow pipeline

GitHub Actions does all downloading, trimming, validation and CSV/XLSX building. Claude sessions
only read trimmed PDFs (via the `extractor` subagent) and write JSON.

## Git rules
- Work directly on `main`: no side branches and no pull requests.
- Always run `git pull --rebase` before starting and before every push.
- Claude sessions only ever write to `output/json/` and `CLAUDE.md`. GitHub Actions owns everything
  else in `output/` (and `trimmed/`). This avoids merge conflicts.
- If a push fails, stop and tell the user.

## Layout
- `scripts/download.py` lists filings via the Auditor's search (POST to
  nebraska.gov/auditor/reports/index.cgi: type=audit, subtype, year, csv=1 export / csv=0 results)
  and downloads PDFs to `audits/` (gitignored). Raw search responses go to `output/listing_debug/`.
  `output/file_list.csv` records each filing's county, subdivision type and filing type (Audit/Waiver).
- `scripts/find_pages.py` saves the statement pages to `trimmed/` and writes `output/manifest.csv`.
- `scripts/validate.py` checks `output/json/` and writes `output/flagged.csv`.
- `scripts/build_csv.py` writes `output/cashflow_long.csv`, `cashflow_summary.csv`, `progress.csv`
  and `cashflow.xlsx`.
- `.github/workflows/fetch.yml` is run manually. `validate.yml` runs on each push to `output/json/`.
- `.claude/agents/extractor.md` is the extraction subagent. It holds the full schema and rules.

## Schema and category rules (summary; full rules are in the extractor agent)
One JSON per report: `output/json/<pdf stem>.json`. Fields: entity_name, entity_type,
fiscal_year_end, basis (`cash`|`GAAP`), extraction_attempt, beginning_cash, beginning_investments,
beginning_fund_balance, receipts[], disbursements[], transfers_in, transfers_out, debt_proceeds,
other_financing, adjustments[], reported_totals{receipts,disbursements}, proprietary (GAAP:
operating, noncapital_financing, capital_and_related_financing, investing, beginning_cash,
ending_cash), detail[], detail_totals[], balance_sheet{statement, scope, column, assets[], deferred_outflows[], liabilities[],
deferred_inflows[], equity[], total_assets, total_deferred_outflows, total_liabilities,
total_deferred_inflows, total_equity}, ending_cash, ending_investments, ending_fund_balance, notes.
- **Governmental and business-type (utility/enterprise) funds are separate:** every
  receipts/disbursements/detail line has `fund_type` (governmental | business-type); top-level
  balances, transfers and debt fields are governmental; `business_type{measure, beginning,
  ending, transfers_net, debt_proceeds, other_financing, adjustments}` rolls the utility funds
  forward; `reported_totals{governmental{receipts,disbursements}, business-type{...}}`.
- **All 25 subdivision types** are in scope. Entirely business-type entities (hospitals, power,
  housing, airports, community colleges) have only business-type lines and `business_type`
  balances; governmental fields are null. Entity-type rules are in the extractor agent.
- Any value not printed as-is (sums, splits, derived balances) carries `computed: "<how>"`.
- Lines printed below a statement's own total line carry `outside_printed_total: true`; printed
  totals are never altered. Where the printed report itself doesn't add up, the figures stay as
  printed and `report_discrepancies[]` records it (listed for review as "the report itself
  doesn't add up").
- **Combined/unlabeled tax lines** are split using the report's own breakdown (`split_from`
  set) or kept as `taxes (unlabeled)`; both, and any line marked `review`, go to
  output/review.csv (the user's review list).
- Each report covers revenue sources, expenditures, and a balance sheet with all assets and
  liabilities (government-wide Statement of Net Position for GAAP; cash-basis net position
  statement otherwise). The link to the PDF on the Auditor's site (manifest `url`) is added to
  every output by build_csv.py; it is not stored in the JSON.
- Money values are `{"amount", "source_page"}`, where source_page is the page within the trimmed PDF.
  List items are `{category, original_label, amount, source_page}`.
- Receipt categories: property tax, sales tax, motor vehicle tax, occupation/franchise tax, lodging tax,
  inheritance tax, special assessments, keno/lottery, other taxes, taxes (unlabeled),
  intergovernmental/state aid, federal grants, charges for services/fees, licenses and permits,
  fines and forfeitures, investment income, donations/contributions, reimbursements/refunds,
  sale of assets, other. `other`/`other taxes`/miscellaneous are last resorts; every such line,
  any category that doesn't match its label, and any fund type with >10% in `other` goes to review.
- Transfers are never revenue or spending: transfers_in/out (detail section `other financing`,
  signed); a line labelled "transfer" anywhere else is flagged.
- Disbursement categories: general government, public safety, streets/highways,
  culture/recreation, health/welfare, economic development, utilities, debt service principal,
  debt service interest, capital outlay, instruction, support services, student transportation,
  food service, student activities (school districts/community colleges), hospital/health care, housing,
  natural resources/conservation, agriculture/fair, other.
- Asset categories: cash and investments, receivables, due from other governments,
  inventories/prepaids, restricted assets, capital assets, other assets.
- Liability categories: accounts payable, accrued liabilities, due to other governments, unearned
  revenue, debt - due within one year, debt - due in more than one year, net pension/OPEB
  liability, other liabilities.
- **detail[]** = every line from the supplementary schedules (budget-to-actual, departmental,
  combining, fund schedules): {schedule, fund, department, section (revenue|expenditure|other
  financing), category (high-level list above; function for spending), detail_category (fine list
  below; object for spending), original_label, amount (actual), budget_final, source_page}, plus
  detail_totals[] {schedule, fund, section, amount, source_page} for the printed totals.
  No double counting across schedules.
- Detailed revenue categories: property tax, sales tax, motor vehicle tax, occupation/franchise tax, lodging tax, inheritance tax, special assessments, keno/lottery, other local taxes, taxes (unlabeled), highway allocation, motor vehicle pro-rate, municipal equalization, homestead/tax credits, state school aid (TEEOSA), other state aid, county/other local governments, federal grants, water charges, sewer charges, electric charges, gas charges, refuse/sanitation charges, other utility charges, licenses and permits, fines and forfeitures, fees of office, rentals, recreation/pool/park fees, cemetery fees, tuition and fees, school lunch sales, ambulance/rescue fees, patient service revenue, tenant rent, fair/event revenue, student activity receipts, other charges for services, investment income, donations/contributions, reimbursements/refunds, sale of assets, miscellaneous revenue.
- Detailed expenditure categories: salaries and wages, employee benefits and payroll taxes, professional/contract services, supplies and materials, utilities (power, gas, phone), fuel, repairs and maintenance, insurance, rent/leases, dues, training and travel, printing, publishing and postage, grants/payments to other entities, law enforcement contract, equipment purchases, construction/infrastructure, land and buildings, debt principal, debt interest and fees, tax refunds/treasurer fees, purchased power/cost of sales, depreciation, medical supplies and services, student activity spending, not itemized (function total with no object breakdown), miscellaneous expenditure.
- Balance check: assets + deferred outflows = liabilities + deferred inflows + equity.
- Identity: beginning + receipts − disbursements + transfers_in − transfers_out + debt_proceeds +
  other_financing + adjustments = ending (cash for cash basis, fund balance for GAAP).

## Batch procedure
1. `git pull --rebase`.
2. Read `output/progress.csv`. Take the next `pending` reports, plus any `flagged` reports whose
   `extraction_attempt` is 1 (re-check each flagged report once; after that leave it for a human).
   You can do this with a short `python -c`/`grep` that prints filenames only.
3. Send them to the `extractor` agent in groups of about 3 reports per agent (trimmed PDFs with
   detail schedules run 15-30 pages) (several agents in
   parallel is fine). Pass only the filenames.
4. Send the same reports to the `verifier` agent (about 3 per agent). It re-reads each trimmed PDF
   and writes `output/json/verify/<stem>.json` with every discrepancy (errors and warnings).
5. `git add output/json/ && git commit -m "extract: N reports"`, then `git pull --rebase && git push`.
   GitHub Actions validates (exact math: >$1 flagged, any smaller difference to review; every
   amount checked against its page text/OCR; verifier errors flagged, warnings to review) and
   updates progress.
6. Repeat until the user's usage runs low, then report how many are done and how many remain
   (counts from `output/progress.csv` after a pull).

Keep the main session lean: don't open PDFs or print JSON in the main conversation. Rely on the
agents' one-line statuses.

## Open follow-ups (remove each line once done)
- find_pages.py v6 is pushed (debt-service schedules were being dropped, plus the fixes below). After
  the next fetch re-trims, compare each extracted report's manifest `pages_kept` with
  `output/json/_meta/pages_at_extraction.csv`; re-extract and re-verify every report whose pages
  changed (their source_page numbers no longer match).
These were diagnosed and fixed in find_pages v6; re-extract and re-verify them after the re-trim:
- Trimmed PDF is missing pages: Arapahoe_FY2025 (other
  nonmajor combining page), Auburn_Public_Schools_FY2025 (report p27 Employee Benefit Fund and p30 School Nutrition; still missing after v6 re-trim),
  Battle_Creek_FY2025 (report pp10-12, proprietary statement), Butte_FY2025 (report p31, rest of
  General Fund spending), Cody_Kilgore_Public_School_FY2025 (p18 activity accounts cut off; residual
  lines from p17 used meanwhile).
  Litchfield_Public_Schools_FY2025 (left half of the governmental receipts/disbursements statement,
  report p17-18 spread; receipt labels inferred from fund schedules, marked review).
  Leigh_FY2025 (General Fund departments schedule continues past trimmed p12: Housing/Park/Pool/Library
  columns missing; p9 budget schedule used meanwhile).
  Lawrence-Nelson_Public_Schools_FY2025 (student activities schedule, ~424,692 of receipts, not trimmed;
  General Fund detail by object pp18-19 only partly there).
  Beatrice_Public_Schools_FY2025 (report p7, governmental receipts page, not trimmed; receipts built from
  fund schedules, marked computed).
  Boelus_FY2025 (Exhibit A page 2, lines 29-52 spending by function, not trimmed; fund totals used).
  Cross_County_Community_Schools_FY2025 (trim stops partway through Exhibit N; remaining programs from the
  budget schedule).
  Deshler_FY2025 (governmental receipts/disbursements statement not trimmed; business-type data complete).
  Fillmore_County_School_District_25_FY2025 (trim stops partway through Exhibit N after 2162; later programs from the budget schedule).
  Fairbury_FY2025 (report pp42-47 Light/Water/Sewer budget schedules 6-9, plus pp8, 11, 62, not trimmed).
  Chadron_Public_Schools_FY2025 (report p34 not trimmed: trimmed p15 = report p33, p16 = report p35).
  Grand_Island_Public_Schools_Foundation_FY2025 (all 5 trimmed pages cut off on the right: Total and Jack Martin
  columns missing; skipped, no JSON).
  Garfield_County_Agricultural_Society_FY2025 (report p25 not trimmed: trimmed jumps p24 to p26).
  Lyons_FY2025 (report pp8-9, probably the utilities revenue/expense statement, and p21 not trimmed).
  Kimball_County_FY2025 (report p31 not trimmed), Kimball_FY2025 (report pp17, 19, 59 not trimmed); totals tie, check source.
  Lancaster_County_FY2025 (Veterans Aid and Broadband sub-fund budget pages, report pp85-86, not trimmed).
  Humboldt_FY2025 (governmental fund statements, report pp5-7, not trimmed; budget schedule used).
  Hemingford_Public_Schools_FY2025 (report pp34-35, probably the Activity Fund schedule, not trimmed).
  Gothenburg_FY2025 (report pp42 and 49 not trimmed; may hold schedule lines).
  Grand_Island_Public_Schools_FY2025 (report p39, probably the Activities Fund schedule, not trimmed).
  Grand_Island_FY2025 (nonmajor combining schedule's Total column page not trimmed; nonmajor detail left out).
  Gering_Ft_Laramie_Irrigation_District_FY2025 (nonoperating/capital-grant statement page not trimmed; 9,257,241
  derived as an adjustment meanwhile).
- Budget forms whose Total All Funds page wasn't found (manifest flag no_budget_actuals_found; skipped, no
  JSON): Lewis_Township_Clay_County_B2526 and any others with that flag. Check why the finder misses it.
- Filed under FY2025 but the PDF holds an earlier year (extracted as printed; check the Auditor's site for
  the FY2025 filing): Bristow_FY2025 (period 10/1/23-9/30/24).
- Trimmed to a budget page only (skipped, no JSON): Cass_County_SID_9_FY2025, Cass_County_SID_10_FY2025.
  Also trimmed to one page (extracted from that page; check the source PDF for missing statements):
  Douglas_County_SID_520/575/583/584/615/640_FY2025, Hickman_FY2025 (all-fund-types budget schedule only;
  disbursements recorded).
- ~97 Audit filings marked no_statements_found (mostly SIDs) and 72 with no balance statement: page
  finder misses their titles.
- ~1,000 Waiver filings link only a budget form (Budgets_Filed/…_B2526.pdf): check
  output/page_debug/_alt_urls.csv for a real waiver PDF; otherwise read prior-year actuals from the
  budget form. The probe found real FY2025 PDFs only for Silver_Creek_Township_Merrick_County and
  Spencer_Township_Boyd_County; the rest are extracted from the budget form (rules in the extractor
  agent) once v6 trims them to the budget pages.
