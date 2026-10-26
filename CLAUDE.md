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
- Receipt categories: property tax, sales tax, motor vehicle tax (incl. state motor vehicle tax allocation;
  pro-rate is its own category, Motor Vehicle Pro-Rate), occupation/franchise tax, lodging tax,
  inheritance tax, special assessments, keno/lottery, other taxes, taxes (unlabeled),
  intergovernmental/state aid, federal grants, charges for services/fees, licenses and permits,
  Fines and Licenses, investment income, donations/contributions, reimbursements/refunds,
  Sale of Property, other. `other`/`other taxes`/miscellaneous are last resorts; every such line,
  any category that doesn't match its label, and any fund type with >10% in `other` goes to review,
  except lines whose printed label itself says other/miscellaneous. Budget-form (waiver) reports are
  marked by the workbook's filing_type column ("Waiver/budget only"), not by per-line review notes.
- **User category decisions (2026-10-23):** 9,490 lines that were in other/miscellaneous now carry the
  user's detail_category (`config/category_decisions.csv`, `category_decision` on each line). Carline Tax,
  Public Power District Sales Tax, In Lieu of Tax, Nameplate Capacity Tax, Private Sector Share of Projects
  (developer/private payments toward public projects) and Penalties and Interest on Taxes (never inside property tax),
  Homestead Exemption, Property Tax Credit, ESU Receipts and Motor Vehicle Pro-Rate (never inside state aid), Tuition Received (all tuition incl.
  preschool), Interest Income (interest lines; investment income keeps dividends/gains) and Extracurricular Receipts
  (school activity receipts/admissions) are top-level receipt categories. The 29 unclear lines were
  settled from their PDFs.
- **User label decisions (2026-10-26, all reports):** receipt/detail revenue labels renamed (printed text kept in
  `printed_label`): Sales and Use / Sales and use tax -> Sales and Use Tax; Property / Taxes: Property / Real Property and
  Personal Property Tax -> Property Tax; Occupation / Occupation tax -> Occupation Tax; Investment income / Investment
  earnings -> Investment Earnings; Turnback / Turnback Tax -> Turnback, its own receipt category (out of state aid).
  "Reimbursement for Services" lines are their own receipt category, Reimbursement for Services.
  Sundry and In Lieu / Sundry and In Lieu Tax -> Sundry and in Lieu (category unchanged: In Lieu of Tax).
  Workbook (cashflow_one_tab.xlsx): detail lines in section "other financing" get category "Total Other Financing
  Sources (Uses)" (the section keeps its name).
  Lincoln: "Equipment Maintenance" is its own disbursement category (out of general government); schedule lines under a
  "Capital Outlay" heading are category capital outlay (function kept in computed), matching the governmental funds statement.
- **Capital outlay (user, 2026-10-26, all reports):** every capital outlay line, including one inside a department
  (e.g. Sheriff -> Capital outlay), is category capital outlay, labelled "Capital Outlay: <function>" (printed text in
  printed_label, function in computed). Capital outlay lines that are debt/lease payments stay in debt service.
- **Own disbursement categories (user, 2026-10-26):** Mass Transit, Bond Retirement, Leases and SBITA (lines labelled
  Leases and SBITA Principal / Interest) (Lincoln and Clay Center). The last two count in the workbook debt service total.
  Lincoln only: Interest and Fiscal Charges are own categories (in the debt service total); Exp_Miscellaneous relabelled Miscellaneous.
  Developer Purchased TIF Payments is its own disbursement category (out of economic development).
- **Workbook capitalization (user, 2026-10-26):** build_xlsx.py shows every category, detail_category and original_label
  capitalized (cap(): words already containing a capital kept, small words lowercase), and the first letter of every
  section value ("Receipts", "Other financing"). JSON values are unchanged.
- **School district disbursement functions (user, 2026-10-23):** Instruction, Special Education Programs, support services
  (Pupils and Staff only), General Administration, School Administration, Business, Building and Grounds, Community
  Service Operations, State Categorical Programs, Federal Programs (only lines labelled so), Debt Service (Principal /
  Interest incl. bond fees); rules in the extractor agent.
- Transfers are never revenue or spending: transfers_in/out (detail section `other financing`,
  signed); a line labelled "transfer" anywhere else is flagged.
- Disbursement categories: general government, public safety, streets/highways,
  culture/recreation, health/welfare, economic development, utilities, debt service principal,
  debt service interest, debt service (principal and interest combined) (unsplit lines; workbook adds a
  debt service total column), capital outlay, instruction, support services, student transportation,
  food service, student activities (school districts/community colleges), hospital/health care, housing,
  natural resources/conservation, agriculture/fair, other.
- Asset categories: Cash and Cash Equivalents, Investments, cash and investments (only lines combining both), Inventories, Prepaid Items, Plant Operation Assets, Internal Balances,
  Other Assets, receivables, due from other governments,
  inventories/prepaids, restricted assets, capital assets, other assets.
- Liability categories: accounts payable, Accrued Interest Payable, Accrued Liabilities, Commercial Paper, due to other governments, unearned
  revenue, debt - due within one year, debt - due in more than one year, net pension/OPEB
  liability, other liabilities.
- **detail[]** = every line from the supplementary schedules (budget-to-actual, departmental,
  combining, fund schedules): {schedule, fund, department, section (revenue|expenditure|other
  financing), category (high-level list above; function for spending), detail_category (fine list
  below; object for spending), original_label, amount (actual), budget_final, source_page}, plus
  detail_totals[] {schedule, fund, section, amount, source_page} for the printed totals.
  No double counting across schedules.
- Detailed revenue categories: property tax, sales tax, motor vehicle tax, occupation/franchise tax, lodging tax, inheritance tax, special assessments, keno/lottery, other local taxes, taxes (unlabeled), highway allocation, motor vehicle pro-rate, municipal equalization, homestead/tax credits, state school aid (TEEOSA), other state aid, county/other local governments, federal grants, water charges, sewer charges, electric charges, gas charges, refuse/sanitation charges, other utility charges, licenses and permits, Fines and Licenses, fees of office, rentals, recreation/pool/park fees, cemetery fees, tuition and fees, school lunch sales, ambulance/rescue fees, patient service revenue, tenant rent, fair/event revenue, student activity receipts, other charges for services, investment income, donations/contributions, reimbursements/refunds, Sale of Property, miscellaneous revenue.
- Detailed expenditure categories: salaries and wages, employee benefits and payroll taxes, professional/contract services, supplies and materials, utilities (power, gas, phone), fuel, repairs and maintenance, insurance, rent/leases, dues, training and travel, printing, publishing and postage, grants/payments to other entities, law enforcement contract, equipment purchases, construction/infrastructure, land and buildings, debt principal, debt interest and fees, debt principal and interest (combined), tax refunds/treasurer fees, purchased power/cost of sales, depreciation, medical supplies and services, student activity spending, not itemized (function total with no object breakdown), miscellaneous expenditure.
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

## Review scope (user, 2026-10-23)
The user reviews only cities, villages, school districts and counties (`config/review_scope.txt`), and only
the cash flow, balance sheet and revenue/expense detail. `output/cashflow_core.xlsx` (built with cashflow.xlsx)
holds just those reports and sheets. Prioritise fixes and re-checks for these types; the others stay extracted.

## Open follow-ups (remove each line once done)
- Targeted re-trims (config/retrim.csv; FINDER_VERSION stays 8 so other reports are NOT re-trimmed; never bump it
  without the user's OK). Done 2026-10-22: 12 reports re-trimmed (find_pages v9 + Waterloo/Plainview fixes),
  re-extracted and re-verified; Sargent_Public_Schools_FY2025 followed (OCR-garbled repeated title fix), Activities
  Fund schedule now complete. Blair_FY2025 needs nothing: it has no Debt Service budget schedule (report pp63-65, 67 = source
  pp64-66, 68: divider, combining balance sheet, and two pages already trimmed).
- find_pages.py v7 is pushed (v6 debt-service fix plus: budget-form Total All Funds page, scanned/rotated/garbled
  page OCR, title variants, fund-name exclusions, detail soft cap 25). Butte_FY2025 (report p31 already in the trim:
  re-check the extraction) and Cass_County_SID_9/10 (v6 already keeps their statements) were stale entries. After
  the next fetch re-trims, compare each extracted report's manifest `pages_kept` with
  `output/json/_meta/pages_at_extraction.csv`; re-extract and re-verify every report whose pages
  changed (their source_page numbers no longer match). Checked 2026-10-22: 0 of the 870 tracked reports had
  changed pages; check again after each fetch.
These were diagnosed and fixed in find_pages v6; re-extract and re-verify them after the re-trim:
- Trimmed PDF is missing pages: Arapahoe_FY2025 (other
  nonmajor combining page),
  Butte_FY2025 (report p31, rest of
  General Fund spending),
  Litchfield_Public_Schools_FY2025 (left half of the governmental receipts/disbursements statement,
  report p17-18 spread; receipt labels inferred from fund schedules, marked review).
  Leigh_FY2025 (General Fund departments schedule continues past trimmed p12: Housing/Park/Pool/Library
  columns missing; p9 budget schedule used meanwhile).
  Boelus_FY2025 (Exhibit A page 2, lines 29-52 spending by function, not trimmed; fund totals used).
  Fairbury_FY2025 (report pp42-47 Light/Water/Sewer budget schedules 6-9, plus pp8, 11, 62, not trimmed).
  Chadron_Public_Schools_FY2025 (report p34 not trimmed: trimmed p15 = report p33, p16 = report p35).
  Grand_Island_Public_Schools_Foundation_FY2025 (all 5 trimmed pages cut off on the right: Total and Jack Martin
  columns missing; still cut off after the v8 re-trim, so check the source PDF page size/crop; skipped, no JSON).
  Garfield_County_Agricultural_Society_FY2025 (report p25 not trimmed: trimmed jumps p24 to p26).
  Lyons_FY2025 (report p21 not trimmed; pp8-9 proprietary statement now in the v8 trim).
  Kimball_County_FY2025 (report p31 not trimmed), Kimball_FY2025 (report pp17, 19, 59 not trimmed); totals tie, check source.
  Lancaster_County_FY2025 (Veterans Aid and Broadband sub-fund budget pages, report pp85-86, not trimmed).
  Gothenburg_FY2025 (report pp42 and 49 not trimmed; may hold schedule lines).
  Grand_Island_FY2025 (nonmajor combining schedule's Total column page not trimmed; nonmajor detail left out).
  Niobrara_Public_Schools_FY2025 (report p27 not trimmed; totals tie without it).
  Omaha_Public_Schools_FY2025 (report pp81-89 and p94, possibly more fund budget schedules, not trimmed).
  Ogallala_FY2025 (report pp17, 19, 62 not trimmed; probably nonmajor balance sheet/reconciliations).
  Ord_FY2025 (report p61, left half of the Schedule of Expenditures by fund, not trimmed; only the right half is p22).
  ONeill_FY2025 (report pp53, 57, 62 now trimmed: Other Governmental combining, CDBG and Debt Service schedules added; still not trimmed: report pp44, 61, 63-66, 69-72, 75+, probably the utility schedules).
  Plattsmouth_Airport_Authority_FY2025 (report p8, governmental funds revenues/expenditures statement, not trimmed; budget-basis schedule used).
  Polk_FY2025 (odd report pages 27-35 of the combining schedules not trimmed: Bandstand/Playground/Total/budget columns; actuals from Exhibits III-IV).
  Springfield_FY2025 (Note M, explaining 33.2M sales tax and 3.5M permits, not trimmed).
  Two_Rivers_Public_Health_Department_FY2025 (report pp34-36 not trimmed; Statement of Net Position now in the v8 trim).
  Wahoo_FY2025 (no General Fund budget schedule trimmed; source PDF p19 and pp23-27 not kept).
  Found in the 2026-10-21 outside-review recheck (re-extract after the v7 re-trim):
  Beaver_Crossing_FY2025 (budget columns report pp26, 28, 30),
  Box_Butte_County_FY2025 (rest of nonmajor combining
  statement after report p24), Bradshaw_FY2025 (General Fund combining pages report pp32, 34),
  Central_City_Public_Schools_FY2025 (report p41 Bond Fund budget), Central_Platte_NRD_FY2025 (report p11 fund statement),
  Douglas_County_FY2025 (other nonmajor combining pages),
  Falls_City_Public_Schools_FY2025 (report p6 governmental receipts/disbursements), Fillmore_County_FY2025 (report p34,
  right half of nonmajor combining), Furnas_County_FY2025 (second nonmajor combining page), Greeley_FY2025 (report p28,
  end of cash flow statement), Hooker_County_FY2025 (second half of nonmajor combining), Logan_County_FY2025 (source pp26-27 nonmajor combining), Niobrara_FY2025 (report p15
  proprietary statement), Norfolk_FY2025 (beginning governmental cash still null: no prior-year balance sheet),
  Northeast_Nebraska_Economic_Development_District_FY2025 (cash flow statement report pp7-8),
  Ord_FY2025 (also report p15 government-wide assets/liabilities).
- Rules the second-pass audit (2026-10-22) found applied inconsistently; waiting on the user's decision, then make
  all reports match: SID "Warrants issued not registered" (long-term
  debt on SIDs 235/321/324, current payable on SID 291); SID detail actual column (budgetary basis on most, GAAP on
  SIDs 297-300); unlabeled SID "Maintenance" (utilities vs streets/highways).
- Filed under FY2025 but the PDF holds an earlier year (extracted as printed; check the Auditor's site for
  the FY2025 filing): Bristow_FY2025 (period 10/1/23-9/30/24), Naper_FY2025 (period 10/1/23-9/30/24; not yet checked
  on the Auditor's site). Checked 2026-10-21: the Auditor's only FY2025 file
  (approved waiver for FY ending 2025) attaches the 10/1/23-9/30/24 statement; no other filing exists. Leave as is.
- config/extra_pages.csv (stem,pages): source pages the finder must keep. Used for Ruskin_FY2025 (re-trimmed and
  re-extracted 2026-10-26, verified 0 errors).
- Taylor_FY2025: trimmed pp2-8 are rotated, so the page-text check can't confirm the governmental amounts (checked visually).
- Trimmed to one page (extracted from that page; check the source PDF for missing statements):
  Douglas_County_SID_640_FY2025.
- Hickman_FY2025: re-trimmed (doubled-word text layer fix) and re-extracted 2026-10-22; fund balances and
  proprietary statements now in; verified 0 errors.
- ~97 Audit filings marked no_statements_found (mostly SIDs) and 72 with no balance statement: page
  finder misses their titles.
- ~1,000 Waiver filings link only a budget form (Budgets_Filed/…_B2526.pdf): check
  output/page_debug/_alt_urls.csv for a real waiver PDF; otherwise read prior-year actuals from the
  budget form. The probe found real FY2025 PDFs only for Silver_Creek_Township_Merrick_County and
  Spencer_Township_Boyd_County; the rest are extracted from the budget form (rules in the extractor
  agent) once v6 trims them to the budget pages.
