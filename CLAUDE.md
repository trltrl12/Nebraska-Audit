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
- `scripts/user_rules.py` re-applies the user's naming/category rules after extraction (any year): label renames,
  category_decisions.csv for other/miscellaneous lines, utility cost types, balance-sheet categories and the entity
  rulings (Lincoln, Clay Center, Lincoln Public Schools, Douglas County).
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
  Sale of Property, Loan Repayments (loan repayments received, e.g. LB 840 / CDBG loans; user 2026-11-05), other. `other`/`other taxes`/miscellaneous are last resorts; every such line,
  any category that doesn't match its label, and any fund type with >10% in `other` goes to review,
  except lines whose printed label itself says other/miscellaneous. Budget-form (waiver) reports are
  marked by the workbook's filing_type column ("Waiver/budget only"), not by per-line review notes.
- **Label-only vs context decisions (user 2026-11-05):** `config/category_decisions.csv` has `apply` = `label` (the label
  alone is enough; user_rules.py applies it everywhere) or `context` (the same words can be different transactions, e.g.
  Local Receipts, Insurance, Deposits, Transportation, County Treasurer Receipts). Context labels are NEVER categorized from
  the label: user_rules.py queues each line in `output/json/_meta/context_check.csv`; the `context_checker` agent reads the
  page and writes `_meta/context_decisions/<stem>.csv` (category + evidence, or `unclear`), applied per line only. The category
  list is not definitive (user 2026-11-05): the PDF always comes first over any list or decision file; a new category
  name may be created when nothing in use fits the report, reusing an existing name for the same concept (consistent names). Never
  extend a context decision to another line, report or similar label. Unchecked/unclear lines go to review; a context
  category without evidence is flagged. 316 labels decided 2026-11-05 (183 label, 133 context; config/category_cleanup.xlsx).
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
  Personal Property Tax -> Property Tax (also "Property and real estate (taxes)", user 2026-10-28); Occupation / Occupation tax -> Occupation Tax; Investment income / Investment
  earnings -> Investment Earnings; Turnback / Turnback Tax -> Turnback, its own receipt category (out of state aid).
  "Reimbursement for Services" lines are their own receipt category, Reimbursement for Services.
  Sundry and In Lieu / Sundry and In Lieu Tax -> Sundry and in Lieu (category unchanged: In Lieu of Tax).
  Workbook (cashflow_one_tab_<year>.xlsx, one file per fiscal year, user 2026-11-04): detail lines in section "other financing" get category "Total Other Financing
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
- **User label decisions (2026-10-27, all reports):** detail other-financing "Transfer(s) in/out" -> "Transfers In" /
  "Transfers Out"; (property labels: "Property Tax", see 2026-10-26; the 2026-10-27
  "Property and Real Estate Taxes" label was reverted by the user 2026-10-28). Categories debt service principal and debt
  service interest -> "Debt Service" (old part kept in `debt_service_part`).
  Douglas County: the nonmajor "Miscellaneous (taxes)" 5,452,600 (Tourism fund) is category Miscellaneous Tax, not
  lodging tax (the only lodging-tax line labelled miscellaneous in any report).
- **Utility spending by cost type (user, 2026-10-28, all reports):** business-type spending lines printed by cost type
  (Personal Services, Supplies, Repairs, Insurance...) have the cost type as category (Salaries and Wages,
  Professional/Contract Services, Supplies and Materials, Repairs and Maintenance, Depreciation, Insurance, Other Taxes
  Paid, other, ...), with `function: "utilities"`; function lines (Water, Sewer, Distribution) stay utilities.
- **School spending (user, 2026-10-27):** spending lines labelled categorical grants from corporations / private
  interests are category "Categorical Corporation and Private" (out of Instruction; all reports). Lincoln Public Schools:
  the statement's "Staff" line is category Instruction; its "Other" 14,686 line is category other (out of Debt Service).
  Lincoln Public Schools "3180 Prorate Motor Vehicle" 653,562 (p7) is category intergovernmental/state aid (one line only).
  Lincoln Public Schools "1925 Categorical Grants" 530,334 is category Categorical Grants (out of donations/contributions);
  the p6 "Grants and donations" 6,488,219 is split into Contributions and Donations from Private Sources 5,957,885 and
  Categorical Grants 530,334 (split_from, from the p7 schedule).
  Lincoln Public Schools "5690 Other Receipts" 317,143 and "5300 Sale of Property" 63,387 (p8) are category Other
  Non-Revenue Receipts.
- **Balance sheet basis (user, 2026-10-27):** cash-basis reports take governmental balance-sheet lines from the
  governmental funds statement of assets, liabilities and fund balances (Total column; fund balance by classification)
  where its totals equal the government-wide figures; reports whose government-wide figures include capital assets,
  debt or interfund amounts keep the Statement of Net Position. GAAP reports keep the Statement of Net Position.
- **Per-fund detail (user, 2026-10-27, cities and villages):** where detail had only a combined all-funds schedule,
  detail is one line per individual fund column of the governmental funds and proprietary funds statements
  (Total column never used); the combined schedule lines are removed. User 2026-10-28: when there is no schedule at
  all, detail stays empty; when some funds have their own schedules, statement lines are not added for the others
  (schedule lines show funds, statement lines don't). Reports that already have such lines keep them.
- **User decisions 2026-10-28 (from the 100-report audit, all reports):** labels as printed (school "Support services -
  instruction" is never relabelled "Staff"; only the Lincoln Public Schools ruling uses Staff); school activity fund
  receipts -> Extracurricular Receipts unless the report says they are for something else; school program lines
  (3535 High Ability Learners, 6200 Title I, IDEA...) follow the report's own grouping (State Categorical / Federal
  Programs when the report groups them there, otherwise their own function); county treasurer / cash in bank ->
  Cash and Cash Equivalents; renames also cover "Taxes - property", "Personal and Real Property Taxes",
  "Transfers - in/out", "Operating transfers in/out"; capital outlay lines already in category capital outlay are
  relabelled "Capital Outlay: <function>" too. New receipt categories out of charges for services: School Lunch Sales,
  Rentals, Utility Charges (incl. garbage/refuse), Drivers Education Fees, Recreation Fees, Cemetery Fees, Ambulance Fees,
  Fees of Office, Program Revenue (program income/revenue/fees, user 2026-10-29); postsecondary/tuition -> Tuition Received; reimbursements and licenses/permits to their categories; student fees/admissions/concessions -> Extracurricular Receipts;
  Drivers Education spending is its own category (not Instruction). Food service lines stay food service. Community Betterment spending is its own category (not keno/lottery, not economic development; user 2026-10-29); keno/lottery receipts stay keno/lottery. Spending lines labelled "Operating" that would be other are category Operating (user 2026-10-29). School lines (user 2026-10-29): school reports use School Facility Rentals / School License Fees instead of Rentals / licenses and permits; city/village/county lines naming a school are School-Related Receipts / School-Related Spending.
  Fund names end in "Fund" (e.g. "Water" -> "Water Fund") unless the name already contains "Fund" (user 2026-10-27).
- **Workbook capitalization (user, 2026-10-26):** build_xlsx.py shows every category, detail_category and original_label
  capitalized (cap(): words already containing a capital kept, small words lowercase), and the first letter of every
  section value ("Receipts", "Other financing", underscores removed), level, fund_type and source values
  capitalized, and column headers capitalized without underscores ("Entity Name"). JSON values are unchanged.
- **School district disbursement functions (user, 2026-10-23):** Instruction, Special Education Programs, support services
  (Pupils and Staff only), General Administration, School Administration, Business, Building and Grounds, Community
  Service Operations, State Categorical Programs, Federal Programs (only lines labelled so), Debt Service (Principal /
  Interest incl. bond fees); rules in the extractor agent.
- **As printed (user 2026-10-29, overrides older allocation/contra fixes):** statement and schedule lines keep exactly what the report prints. Never move a printed receipt or spending amount into transfers, never add contra/offset lines, never remove an amount the statement prints (e.g. interfund allocations, Leasing Corporation amounts). The user reads only Category and Original Label; notes and review text are not used, so do not spend effort on them.
- **Tax-line splits (user 2026-11-04, relaxed the same day; counties, cities, villages, governmental):** a tax line
  gets a specific tax type from a printed breakdown, from a fund name that clearly identifies one tax (that fund's tax
  receipts only), or from exact subtraction where the statements establish the remainder's type (never "the rest is
  property tax" by default). Exact reconciliation; rounding only where the report itself shows it. Ambiguous amounts
  stay unlabeled. Fund-name and subtraction classifications carry `computed`; every evaluated line is logged in
  `output/json/_meta/tax_splits.csv` (rules: extractor agent, "Tax-line splits, evidence standard").
  User 2026-11-04 additions: a printed tax-type label beats a fund name; TIF funds' taxes are property tax (fund name);
  911 funds' taxes are other taxes only when the report establishes 911 surcharge (note or printed Other line; not when
  the fund shows a property levy), otherwise unlabeled; split pieces carry the printed row label (never "Taxes (Property)"
  or "<Fund> Taxes"), or the parent's "Taxes" when no row prints the amount; piece source_page is a trimmed page.
- **Category fixes (user 2026-11-04, all reports, in user_rules.py):** Noxious weed spending -> natural
  resources/conservation; "Community development/betterment" -> Community Betterment; lottery-fund gross keno receipts ->
  keno/lottery (Waverly); statement receipts from the Total column (Roca local option sales tax is one line).
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
- Deferred outflow categories (user, 2026-10-26, all reports): Deferred Loss on Refunding (refunding lines), Deferred
  Outflows for Other Postemployment Benefits (OPEB lines), Deferred Outflows for Pensions (pension lines); others unchanged.
  Deferred inflows likewise: Deferred Gain on Refunding, Deferred Inflows for Leases, Deferred Inflows for Other
  Postemployment Benefits, Deferred Inflows for Pensions. Liability "Due to pension trust fund" -> Due to Pension Trust Fund.
  Liability lines labelled "<X> - Due Within One Year" / "<X> - Due in More Than One Year" for X = Bonds Payable, Leases
  Payable, Subscriptions, Compensated Absences, Landfill Postclosure Costs, Workers' Compensation Claims, Other
  Postemployment Benefit Liability: category X, original_label the "Due ..." part (printed text in printed_label).
- **Waiver Exhibit A per-fund detail (user 2026-11-05, overrides the 2026-10-28 "no schedule -> detail empty" rule for
  waiver forms):** waiver (all-funds Exhibit A) reports get one detail line per printed row per fund column (never the
  Total column), with fund ("<Name> Fund"), fund_type governmental | business-type (same split as the statement lines),
  section revenue / expenditure / other financing (signed transfers), and detail_totals per fund. FY2024: done for all
  229 waiver reports 2026-11-05. FY2025: 49 waiver reports still have empty detail (not done yet).
- **Balance-sheet fund type (user 2026-11-05, all years):** every balance_sheet line carries fund_type governmental or
  business-type (eliminations only where the statement prints an Eliminations column), read from the statement's
  Governmental / Business-type columns (waiver forms: governmental fund columns vs utility fund columns, summed per row);
  never Total-column lines; total_* stay the printed Total-column totals. Same values as FY2025's Fund Type column.
  FY2024 done 2026-11-05 except Broadwater, Chapman, Ragan (single-column statements; cannot be split).
- **Governmental net position (user 2026-10-29, GAAP reports):** `governmental_net_position{beginning, restatement, change, ending}` from the Statement of Activities governmental activities column; shown in the one-tab workbook as cash flow / Governmental / "Net position (statement of activities)" rows, next to the fund-balance roll-forward (which is unchanged).
- Equity lines for net investment in capital assets (any wording, incl. "Invested in capital assets, net of related
  debt") have category Net Investment in Capital Assets (user, 2026-10-27, all reports).
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

- **User rulings 2026-11-06 (all reports):**
  - Cash and fund balance are separate measures, each filled only where printed; fund balance is never treated as cash
    and cash is never derived.
  - Bond/loan proceeds follow the printed section: printed among revenues -> receipt lines "Bond Proceeds" / "Loan
    Proceeds" (generic labels "Debt Proceeds"); printed under other financing -> debt_proceeds. Gibbon FY2024 and
    Plattsmouth Community Schools FY2025 stay as printed. "taxes (unlabeled)" is now category "taxes" (Taxes and Other
    Taxes stay separate). Scottsbluff FY2024 loss 2,342 stays in nonoperating, category Loss on Disposal of Assets.
  - Mixed labels ("Licenses, fees and permits", "Rental and fees", "Grants and contributions") are split only when the
    report prints a breakdown that adds up exactly; otherwise kept with their category. Grants and donations stay
    separate categories; year-to-year category differences without printed evidence are left as they are. A generic
    utility "Other revenue" stays charges for services only when the report counts it in Charges for Services.
  - Split pieces carry the label of the printed row they came from (e.g. "3130 Homestead exemption", not "State
    receipts"); fund-column cells of the parent row, sums and remainders keep the parent label. Capital outlay lines keep
    "Capital Outlay: <function>".
  - Inheritance Tax Fund receipts printed as "Local fees..." stay as printed (no fund-name inheritance tax).
  - Sales tax label variants (Sales tax, Local Option Sales Tax, Taxes: Sales, City sales tax, Sales ...) -> "Sales and
    Use Tax"; plain property tax variants (General property tax, Taxes levied, 1100 Local property taxes ...) ->
    "Property Tax" (incl. real and personal property / real estate and property); combined property + motor vehicle
    rows -> "Property Tax and Motor Vehicle Taxes" (kept as one row); TIF variants (TIF proceeds/receipts, Tax increments ...) -> "TIF Proceeds" (category property tax).
    Qualified lines (motor vehicle / LB840 sales tax; debt service / special building property tax) keep their labels.
  - "Current: Public Safety" -> "Public safety"; capital asset lines -> "Capital Assets - Depreciable - Net of
    Depreciation" / "Capital Assets - Non-Depreciable" (all wordings).
  - Label merges (all levels, all years): config/label_merges.csv (1,686 spellings -> one label each; built 2026-11-06
    from the user's Combined_Labels rules: case, spacing, punctuation, hyphens, & and /, singular/plural, possessives,
    Restricted/Assigned/Committed prefixes, footnote refs, typos; word order still matters; today's standard labels
    win; no parentheses where avoidable). user_rules.py applies it (exact label, or a new spelling whose normalized
    form matches one group).
  - 103 label synonym pairs unified (output/json/_meta/label_synonyms_proposed.csv; printed text kept in printed_label).
  - Corrections from the audit review need two independent page reads that agree (a third read settles disagreements);
    every change is in output/json/_meta/corrections_log.csv, open items in _meta/mixed_label_open.csv and
    _meta/held_items.csv, audit finding status in _meta/audit_findings_status.csv.

- **User ruling 2026-11-12 (FY2022/FY2023 consistency):** "match with 2024 and 2025 if that is actually correct and what is
  on the report": copy a later year's category only when this report's page supports it. Otherwise generic: context labels
  stay other; capital outlay detail_category not itemized and function only when printed; "Intergovernmental"/"State"
  stays other state aid unless highway allocation is printed; grant lines with no grantor printed are category other,
  detail_category Grants; mixed grant/donation labels are donations/contributions.

- **User rulings 2026-11-13 (FY2023 audit verification; FY2023 only, other years untouched):** equity lines with a printed
  classification (Restricted / Committed / Assigned / Unassigned / Unrestricted / Nonspendable) take it as category (as FY2025);
  waiver "Ending Balance" equity lines stay with no category (as FY2024/25); capital outlay with no printed object ->
  detail_category not itemized; a single-purpose fund column name counts as the printed capital outlay function (Street ->
  Streets/Highways, Fire/Rescue -> Public Safety, Water/Sewer/Electric -> Utilities; General and mixed funds: plain "Capital
  Outlay"); printed spending function "Community development"/"Community improvement" -> economic development (as FY2024/25);
  waiver "Local Receipts: Other" stays other (as printed), also in utility columns; internal service funds are not added
  (no double counting); memo balances outside a statement's totals are not recorded; department/object lines keep the
  function the report prints. Intra-governmental transfers printed in fund columns are recorded gross even when the
  Total Governmental column nets them (as FY2025); a one-line schedule amount is split when another statement prints its
  exact breakdown (more detail; split_from + computed); a statement-level Total-column line stays one line; statement
  object lines are split by function only where the same report prints the breakdown. Generally "whatever is printed",
  and where the printed label alone doesn't decide, the category FY2024/FY2025 use for the same printed line. County
  audits keep their CO..-..._Audit_Report filenames (entity_name is correct).
  Verification files: scratchpad only; changes logged in _meta/corrections_log.csv.

- **User rulings 2026-11-17 (all years):**
  - **Fund balance / net position classes:** equity lines printed Restricted / Committed / Assigned have category
    `Assigned`; Unrestricted / Unassigned have `Unassigned`; Nonspendable stays. The label is "<Category> - <Purpose>"
    (plain "Assigned" / "Unassigned" when no purpose is printed); the printed text stays in printed_label. Purposes are
    unified by `config/equity_purposes.csv` (the user's consolidation: Community Betterment/Development includes development and
    improvement; Road Improvements/Maintenance and Road and Bridge Improvements/Maintenance are one label each;
    Keno/Lottery; sewer, water, street, library, building etc. keep their printed detail such as Maintenance,
    Improvements, Projects; anything printed with "reserve" is its own label; Capital Projects and Capital Outlay,
    911 Emergency and 911 Wireless stay separate). A purpose not in the file keeps its printed words. Net Investment in
    Capital Assets and waiver Ending Balance lines are unchanged. Lines printing two classes at once are decided per
    line from the page (`_meta/equity_page_check.csv`). Applied by user_rules.py (`equity_class_rule`).
  - The category "Community Betterment" and the equity label are named "Community Betterment/Development".
  - **Label unification outside fund balances (user approved 2026-11-17):** account-code numbers are removed from
    labels ("1990 Miscellaneous local receipts" -> "Miscellaneous local receipts"; printed text stays in
    printed_label), and wordings that are the same words (case, punctuation, plural, small words) or one misspelled
    word share one label (`config/label_unify.csv`; user_rules.py `label_unify_rule`). Anything beyond same-words
    groups needs the user's ruling first.
  - Generic vehicle spending lines ("Vehicle", "Vehicles", "Vehicle expense(s)") are category **Vehicle Expenses/Maintenance**, never
    other (also replaces Fuel / Repairs and Maintenance for that label); lines under a printed function keep it.
  - A plain grant line ("Grants", "Grant income/proceeds/revenue") with no grantor printed is category **Grants** (not
    other); the category "other taxes" is now **taxes**. Where the page gives nothing more than the label, the category
    is taken from the label (Operating Revenue, School ...), not guessed.
  - Compensated absences lines are left in the category each report already has (user 2026-11-17: leave them split).
  - **Everything else: be gentle.** No further label consolidation without the user's ruling. A line gets a specific
    category only when the page shows what it is; "other" stays other. A generic label (Local, Local Receipts: Other,
    Other, Miscellaneous) stays `other` unless that year's page prints what the money is; a fund column name alone is
    not enough (it may be used only for lines whose own label says what they are, e.g. Charges for services in a
    Water Fund).
  - **Cross-year check:** `python3 scripts/xyear_check.py` lists lines where the same entity prints the same label in
    different years with different categories (`_meta/xyear_check.csv`). A mismatch is a reason to read the pages side
    by side, never a reason to copy a category from another year. Decisions go to `_meta/xyear_decisions/<entity>.csv`
    (category, verdict same | differs | unclear, evidence), are applied per line by user_rules.py and logged in
    `_meta/corrections_log.csv`. The agent brief is in the session notes (same rules as above).
  - **FY2022 numbers:** FY2022 ending balances were tied to FY2023 beginning balances (56 entities differed; all read
    from the pages: no extraction errors, only printed restatements, report differences and fund-scope changes), and
    a 30-report random sample was independently re-read.
  - **Verification and alignment (2026-11-17, done):** every cross-year category change was read a second time on its
    page (3,824 lines); disputed lines and all Cemetery spending lines got a third, deciding read (1,145 lines) and a
    blind spot check (122). A disputed change stands only where two readers agree; otherwise the line keeps its earlier
    category. **Cemetery spending follows the function the report itself prints** (General Fund Public works / Culture
    and recreation / Public safety totals), never a blanket health/welfare. Decisions: `_meta/xyear_verify/`,
    `_meta/xyear_third/`, applied through `_meta/xyear_decisions/` (verdict `differs`).
  - **Alignment rules (user_rules.py `align_rules`, all years):** footnote references ("(note E)") are removed from
    labels; waiver "Ending Balance" / "Subtotal of Ending Balances" equity lines carry that category in every year
    (this replaces the 2026-11-13 "no category" wording); Sales tax payable -> due to other governments; due to/from
    other funds -> Internal Balances; detail_category follows a Grants or donations/contributions category;
    "Transfers from/to other funds" -> Transfers In / Transfers Out; FY2022/FY2023 bare capital outlay in a
    single-purpose fund (Street, Fire/Rescue, Water/Sewer/Electric) is "Capital Outlay: <function>".
  - FY2022 full audit: 147 of 595 reports read page by page (`_meta/fy2022_audit/`): no amount errors; the user narrowed
    it (2026-11-17) to the pattern fixes above plus the findings marked certain. McCook department functions
    (Building and Zoning, Senior Center ...) are corrected for all four years from the printed headings.
  - **Schedule adds up to the statement (user 2026-11-17, cities, villages and counties, all years; overrides the
    2026-10-28 "statement lines are not added for the others" rule):** for each fund type and section the detail lines
    must total the statement lines, with nothing missing. A fund column the statement prints but the schedules do not
    cover (other governmental funds, utility funds), printed transfers, bond/loan proceeds and other-financing rows are
    added to detail from the statement's fund columns: printed amounts only, never a derived or plug amount, never the
    Total column; a line is removed only when it is a true duplicate. Where the report itself uses a different basis
    the gap stays and is explained, not forced. Lincoln_FY2022 is left as printed (user).
  - **Fund name on added lines = the statement column label:** "Other Governmental Funds" only when that is all the
    heading says; "Other Governmental Funds (Keno)" -> "Keno Fund"; a fund already on the schedule keeps its spelling.
  - Procedure: `python3 scripts/schedule_gap.py` (lists gaps in `_meta/schedule_gap.csv`); `schedule_fixer` agent
    (pass 2 brief: `.claude/agents/schedule_fixer2.md`) writes `_meta/schedule_fix/<stem>.json` (pass 2:
    `_meta/schedule_fix2/`); `schedule_spot` agent independently re-reads a sample (`_meta/schedule_spot/`); reports with
    an unconfirmed line go to `_meta/schedule_fix_hold.csv`; `python3 scripts/schedule_fix_apply.py [--pass2] [--write]`
    applies a report only when its gaps really tie and no other gap opens (`_meta/schedule_fix_status.csv`); then
    `user_rules.py` on the changed stems. Pass 1 applied 2026-11-17: 280 reports, 2,933 lines added, 98 duplicates removed.
    Pass 2 applied 2026-11-17 (utility fund columns, utility transfers, bond/loan proceeds, other-financing rows): 368
    reports, 12,317 lines added, none removed; spot check of 106 reports (`_meta/schedule_spot2/`, 3,437 items, 99.1%
    confirmed, no wrong amounts). Pass 2 never removes or relabels a printed line: a transfer printed with an odd sign
    on a "Transfers in"/"Transfers out" row keeps its printed label. Bond premiums, discounts and escrow payments count
    with other_financing in the tie check, not with debt_proceeds.
    Still open after pass 2 (52 reports with a pass-2 type gap; list: schedule_gap.csv): held reports
    (`schedule_fix_hold.csv`), bond proceeds not tying (Grand Island FY2022-24, Papillion FY2022/24, Lancaster County
    FY2022, Omaha FY2025), Scottsbluff FY2022/23, unresolved Huntley FY2025, Lamar FY2025, Tekamah FY2022/23, and
    reports whose difference is a printed basis difference. Governmental transfers printed gross in fund columns but
    absent from a budget schedule are not flagged by the gap check; only the reports the spot check found were added.
  - **As printed, no tie required (user 2026-11-17, final):** "just put what is on the PDFs into the files; if the
    PDFs themselves do not tie that is OK; we report what is in the files, we do not correct them". A printed line
    missing from detail is added even where the schedule then differs from the statement; no derived, contra or plug
    line is kept or added; the user does not want to be asked to decide tie questions. Remaining schedule-vs-statement
    differences are differences in the report itself and are fine.
    Pass 3 (2026-11-17, `_meta/schedule_fix3/`, every report page-checked in `_meta/schedule_spot3/`): the 51 reports
    still open were re-read in full; 34 changed (1,095 lines added, 51 duplicate/replaced summary lines removed); 14
    have nothing printed to add. Pass 4 (`_meta/schedule_fix4/`, `schedule_fix_apply.py --pass4`, no tie gate): the
    page-confirmed printed lines that pass 3 had left out because they did not tie.
  - **Full page check and pass 6 (2026-11-18):** every pass-1 and pass-2 report that the earlier samples had not
    covered was checked on the page (`schedule_spot_full` / `schedule_spot_full1` agents; 254 pass-2 reports, 8,995
    items, and 140 pass-1 reports, 1,162 items; results in `_meta/schedule_spot2/` and `_meta/schedule_spot/`). No
    wrong amounts except one derived line (Axtell_FY2022 Water debt issue cost, removed). Fixed from the findings:
    transfer labels as printed (a positive amount on a "Transfers to other funds" row is labelled Transfers Out with
    its printed sign), DeWitt_FY2025 duplicate transfer removed, gross fund-column transfers added where the schedule
    had only a budget net line (Decatur FY2022-24, Walthill FY2022-24, Columbus_FY2023; the net line is replaced only
    when the gross pair nets to it exactly), other single missing lines. The 27 first-pass leftovers were re-read and
    page-checked. Pass 6 (`_meta/schedule_fix6/`, `_meta/schedule_spot6/`, `schedule_fix_apply.py --pass6`): 32 reports,
    310 lines added, 73 replaced/duplicate lines removed. Not added by rule: debt proceeds printed only on a utility
    cash-flow statement (the schedule follows the net-position statement); internal service fund columns.
  - **Workbook, statement other financing (user 2026-11-18):** in the one-tab workbook the statement's transfers in / out,
    debt proceeds and other financing (governmental) and transfers (net), debt proceeds, other financing (business-type)
    also appear under level Revenue and Expenses, section Other financing, source Statement, next to the schedule's
    other-financing rows; they stay in the Cash Flow roll-forward as well (build_xlsx.py `one_tab`).
  - **Pass 7 and McCook (2026-11-18):** the 52 city/village/county reports whose governmental schedule differed more
    than 10% from the statement were re-read fund by fund (`.claude/agents/schedule_fixer7.md`, `_meta/schedule_fix7/`,
    `_meta/schedule_spot7/`, `schedule_fix_apply.py --pass7`). 48 are differences inside the report (bond proceeds or
    debt payments printed as receipts/spending on the budget schedule and as other financing on the statement; budget
    or cash basis schedules vs the statement's basis): nothing missing, nothing counted twice. 4 changed: Ansley_FY2022
    (12 combined all-funds budget lines replaced by 83 per-fund statement lines; 116,756 printed only on the budget
    schedule is not carried, as with Ansley_FY2023's 2,400), Columbus_FY2024 (+3), Lancaster_County_FY2024 (+1),
    Papillion_FY2025 (+2). McCook_FY2022-24 had no utility schedule lines (missed in pass 2): 176 lines added and
    page-checked; McCook_FY2025 Transportation / Miscellaneous income / Grant income categories follow its statement.
  - **Court Settlements (user 2026-11-18):** receipt lines labelled "Court settlements" are their own category, Court
    Settlements (out of other; `config/category_decisions.csv`, label rule). Only Lincoln FY2022 and FY2023 print it.
  - Sargent_FY2022: the Auditor's PDF does not contain printed pp19, 22, 24, 52 (left-hand statement pages); it cannot
    be re-trimmed. Governmental lines come from the Total column.

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
5. Run `python3 scripts/user_rules.py <stems>` (re-applies the user's naming/category rules; `--check` to preview).
   Then `git add output/json/ && git commit -m "extract: N reports"`, then `git pull --rebase && git push`.
   GitHub Actions validates (exact math: >$1 flagged, any smaller difference to review; every
   amount checked against its page text/OCR; verifier errors flagged, warnings to review) and
   updates progress.
6. Repeat until the user's usage runs low, then report how many are done and how many remain
   (counts from `output/progress.csv` after a pull).

Keep the main session lean: don't open PDFs or print JSON in the main conversation. Rely on the
agents' one-line statuses.

## Context check procedure (lines whose label alone is not enough; user 2026-11-05)
Run this when the user says "run the context check". Use a fresh session and do about 15-30 reports per session.
1. `git pull --rebase`.
2. List the next reports (stems in the queue that have no decision file yet):
   `python3 -c "import csv,os;d='output/json/_meta/context_decisions';s=sorted({r['stem'] for r in csv.DictReader(open('output/json/_meta/context_check.csv'))}-{f[:-4] for f in os.listdir(d)});print(len(s),'left');print(' '.join(s[:15]))"`
3. Send them to the `context_checker` agent, 3-5 stems per agent (several in parallel is fine). Pass only the stems.
4. `python3 scripts/user_rules.py --all` applies the page-checked decisions and rebuilds the queue.
5. `git add output/json/ && git commit -m "context check: N reports"`, then `git pull --rebase && git push`.
6. Report how many reports and lines are left (step 2's count). Lines marked `unclear` go to the user's review list.

## Cross-year check procedure (user 2026-11-17; IN PROGRESS, resume here)
1. `git pull --rebase`, then `python3 scripts/xyear_check.py` (rebuilds `_meta/xyear_check.csv`).
2. Entities still to do = entities in xyear_check.csv with no file in `_meta/xyear_decisions/`:
   `python3 -c "import csv,os;d='output/json/_meta/xyear_decisions';e=sorted({r['entity'] for r in csv.DictReader(open('output/json/_meta/xyear_check.csv'))}-{f[:-4] for f in os.listdir(d)});print(len(e),'left');print(', '.join(e[:60]))"`
3. Send them to the `xyear_checker` agent (Opus), about 4-6 entities or ~80 rows per agent, ~15 agents per wave. Pass only
   the entity names.
4. `python3 scripts/user_rules.py --all` (applies the decisions per line), `python3 scripts/xyear_log.py` (logs the
   changes), `git add output/json/ && git commit -m "Cross-year page check: wave N"`, `git pull --rebase && git push`.
5. Entities whose remaining rows are all verdict `differs`/`unclear` drop out of the queue; rows marked `hold` stay.
Status 2026-11-17: DONE for all 516 entities that had differences (9,289 line decisions; about 3,200 category changes,
each in `_meta/corrections_log.csv`). An independent spot check of 80 random changes confirmed 89%; the error patterns it
found were corrected across all decisions and added to the `xyear_checker` rules. What still differs between years
(about 220 label groups in 103 entities) is intended: reports that really print the line differently (verdict
`differs`), compensated absences (left as they are by the user's ruling), lines marked `unclear` (39) and lines held
for the user. Run steps 1-4 again after new extractions or re-trims; only new differences are listed.
Do not push while a validate run is needed: the workflow cancels the run in progress on every push (cancel-in-progress),
so many small pushes mean the workbooks are never rebuilt. Push once per wave.

## Running another fiscal year (e.g. 2024)
Works for any year. Upload the Auditor's search export as config/export_<year>.xlsx (as was done for 2025), then run the
fetch workflow with input year=<year>; without an export file it uses the live search. Files are
named <entity>_FY2024, so nothing collides with 2025; the workbooks then hold both years (filter on fiscal year end).
Then follow the batch procedure above; the extractor carries the user's rules (section "User naming and category
rules") and scripts/user_rules.py re-applies them. Entity rulings (Lincoln, Clay Center, Lincoln Public Schools,
Douglas County) are matched by entity name, so user_rules.py applies them to every year; the extractor carries them too.

## Review scope (user, 2026-10-23)
The user reviews only cities, villages, school districts and counties (`config/review_scope.txt`), and only
the cash flow, balance sheet and revenue/expense detail. `output/cashflow_core.xlsx` (built with cashflow.xlsx)
holds just those reports and sheets. Prioritise fixes and re-checks for these types; the others stay extracted.

## Open follow-ups (remove each line once done)
- FY2023: all 630 trimmed reports extracted (2026-11-12) and aligned with FY2024 choices (354 reports, brief items 1-10:
  same business-type source/measure, transfers, detail sources, categories, capital outlay labels; FY2023 printed amounts).
  Re-trimmed 2026-11-12 (config/extra_pages.csv) and re-extracted/verified: 31 reports. Not re-trimmable (page not in the
  Auditor's PDF): Fairfield, Lawrence, Wilsonville; Sutton and Wilcox needed no page.
- Missing-page re-trim 2026-10-27 (cities/villages/schools/counties): done. 29 reports re-trimmed with
  config/extra_pages.csv, pages remapped, new pages added, verified, verifier errors fixed (0 flags). No page needed:
  Butte (re-check its extraction), Kimball County, Chadron PS, Springfield.
- Re-trim 2026-11-17/18: DONE. 8 reports re-trimmed with config/extra_pages.csv + retrim.csv (one fetch run re-trims
  every queued report whatever year is typed: the download step works from the whole file list; the year only steers
  the search): McCool_Junction_FY2022, South_Sioux_City_FY2022, Lincoln_FY2023/FY2024, Omaha_FY2022-FY2025. Page
  references renumbered to the new trims; lines from the added pages put in by pass 5 (`_meta/schedule_fix5/`, every
  line page-checked in `_meta/schedule_spot5/`, `schedule_fix_apply.py --pass5`): 606 lines added for funds that had
  no schedule lines, 85 combined "Other/Nonmajor Enterprise Funds" lines replaced by their individual funds (rows add
  up exactly). Omaha per-fund debt issuance/premium rows are not added: the combined "Nonmajor Governmental Funds"
  lines from pass 2 hold that money. McCool Junction needed no line (Bond Fund already in from the statement).
  Not re-trimmable (page not in the Auditor's PDF): Ansley_FY2024 (budget revenue page, printed p47), Sargent_FY2022.
  Grant_County_FY2023 needs no page (the report itself does not add up).
- Lincoln re-trim 2026-11-18: DONE for Lincoln (user: make Lincoln FY2022 like the other years; this replaces the
  earlier "leave Lincoln_FY2022 as printed" ruling). Pass 8 (`_meta/schedule_fix8/`, every line page-checked in
  `_meta/schedule_spot8/`, `schedule_fix_apply.py --pass8`): Lincoln_FY2022: the 13 budget-basis nonmajor schedules
  (107 lines) replaced by 153 lines, one per printed row per nonmajor fund column of the subcombining statements
  (trimmed pp15-19), and 96 utility lines added (three major systems from p11, six nonmajor enterprise funds from
  p37; the combined Other Enterprise Funds column is not used); Lincoln_FY2025: 21 combined "Other Enterprise Funds"
  lines replaced by 74 individual-fund lines (p22; new p16 is only a summary of pp17-21, nothing added). Both now
  equal the statement totals exactly for both fund types.
  STILL WAITING for one fetch run (any year; Cities and Villages; Audit): South_Sioux_City_FY2025 source pp81, 82
  (first two pages of the nonmajor governmental combining statement; pp83-85 are already trimmed). After the fetch:
  renumber source_page to the new trim, add lines only for nonmajor funds with no budget schedule lines, page-check
  every line, apply with a new pass and run user_rules.py.
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
- Missing-page reports (cleaned up 2026-10-29): 30 of the 40 listed earlier were re-trimmed with config/extra_pages.csv,
  re-extracted and verified (2026-10-27). Not re-trimmed: Butte_FY2025 (page already in the trim; extraction re-check
  2026-10-29), Chadron_Public_Schools_FY2025, Kimball_County_FY2025, Springfield_FY2025 (no page needed, totals tie),
  Norfolk_FY2025 (beginning governmental cash null: no prior-year balance sheet), and out-of-scope Central_Platte_NRD,
  Garfield_County_Agricultural_Society, Northeast_Nebraska_Economic_Development_District, Plattsmouth_Airport_Authority,
  Two_Rivers_Public_Health_Department. Grand_Island_Public_Schools_Foundation_FY2025: re-trimmed 2026-10-29 (pp5-12), extracted and
  verified (0 errors). Fetch 2026-10-29: 0 of 879 tracked reports had changed pages.
- SIDs: user 2026-10-29: out of scope for now, leave as is. Rules the second-pass audit (2026-10-22) found applied
  inconsistently: SID "Warrants issued not registered" (long-term
  debt on SIDs 235/321/324, current payable on SID 291); SID detail actual column (budgetary basis on most, GAAP on
  SIDs 297-300); unlabeled SID "Maintenance" (utilities vs streets/highways).
- Filed under FY2025 but the PDF holds an earlier year (extracted as printed; check the Auditor's site for
  the FY2025 filing): Bristow_FY2025 (period 10/1/23-9/30/24), Naper_FY2025 (period 10/1/23-9/30/24; not yet checked
  on the Auditor's site). Checked 2026-10-21: the Auditor's only FY2025 file
  (approved waiver for FY ending 2025) attaches the 10/1/23-9/30/24 statement; no other filing exists. Leave as is.
- config/extra_pages.csv (stem,pages): source pages the finder must keep. Used for Ruskin_FY2025 (re-trimmed and
  re-extracted 2026-10-26, verified 0 errors).
- Sutton_FY2025: re-trimmed 2026-10-30 with source pp25, 27 (proprietary statement right halves) and re-extracted;
  Sewer and Trash funds now in the by-fund detail.
- Fort_Calhoun_Community_School_FY2025: re-trimmed 2026-10-29 with source p13 (Total column page) and re-extracted from the
  Total Governmental Funds column (no reclass lines).
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
