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
- **Combined/unlabeled tax lines** are split using the report's own breakdown (`split_from`
  set) or kept as `taxes (unlabeled)`; both, and any line marked `review`, go to
  output/review.csv (the user's review list).
- Each report covers revenue sources, expenditures, and a balance sheet with all assets and
  liabilities (government-wide Statement of Net Position for GAAP; cash-basis net position
  statement otherwise). The link to the PDF on the Auditor's site (manifest `url`) is added to
  every output by build_csv.py; it is not stored in the JSON.
- Money values are `{"amount", "source_page"}`, where source_page is the page within the trimmed PDF.
  List items are `{category, original_label, amount, source_page}`.
- Receipt categories: property tax, sales tax, other taxes, taxes (unlabeled), intergovernmental/state aid, federal
  grants, charges for services/fees, investment income, other.
- Disbursement categories: general government, public safety, streets/highways,
  culture/recreation, health/welfare, economic development, utilities, debt service principal,
  debt service interest, capital outlay, instruction, support services, student transportation,
  food service (school districts/community colleges), hospital/health care, housing,
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
- Detailed revenue categories: property tax, sales tax, motor vehicle tax, occupation/franchise tax, lodging tax, inheritance tax, other local taxes, taxes (unlabeled), highway allocation, motor vehicle pro-rate, municipal equalization, homestead/tax credits, state school aid (TEEOSA), other state aid, county/other local governments, federal grants, water charges, sewer charges, electric charges, gas charges, refuse/sanitation charges, other utility charges, licenses and permits, fines and forfeitures, fees of office, rentals, recreation/pool/park fees, cemetery fees, tuition and fees, school lunch sales, ambulance/rescue fees, patient service revenue, tenant rent, fair/event revenue, other charges for services, investment income, donations/contributions, reimbursements/refunds, sale of assets, miscellaneous revenue.
- Detailed expenditure categories: salaries and wages, employee benefits and payroll taxes, professional/contract services, supplies and materials, utilities (power, gas, phone), fuel, repairs and maintenance, insurance, rent/leases, dues, training and travel, printing, publishing and postage, grants/payments to other entities, law enforcement contract, equipment purchases, construction/infrastructure, land and buildings, debt principal, debt interest and fees, tax refunds/treasurer fees, purchased power/cost of sales, depreciation, medical supplies and services, miscellaneous expenditure.
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
