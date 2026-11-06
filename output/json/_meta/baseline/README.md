# Baseline before the FY2024/FY2025 correction project (user-approved plan, 2026-11-06)

- Commit: 2d21ba9b8e0d51386fa53574fa1175bd1381a09d (output/json tree 2ac66f124758653d9b459300c75c428e0d8474af)
- Workbook SHA-256 (first 16): cashflow_one_tab_2024.xlsx 9c3db18707b8a77f (69,435 rows);
  cashflow_one_tab_2025.xlsx 73be54b187e83d4c (118,459 rows); cashflow.xlsx 0841f351ad88be1e;
  cashflow_core.xlsx 38252fb43b319abd. A local rebuild (scripts/build_csv.py) reproduces both one-tab
  workbooks row for row (scripts/compare_workbooks.py: 0 differences).
- baseline_2d21ba9.csv.gz: every money value in output/json (256,185 values, all years), written by
  `python3 scripts/baseline.py snapshot`. Compare any later state with
  `python3 scripts/baseline.py compare output/json/_meta/baseline/baseline_2d21ba9.csv.gz`.
- Authorised changes are logged in output/json/_meta/corrections_log.csv (one row per change, with type
  extraction_error / report_discrepancy / category_change / supported_calculation, page and evidence).
- Known state at baseline: `user_rules.py --all --check` is not a no-op. 9 context-decision files left after the
  1e8707c revert would change 17 categories in 8 reports (Ainsworth Airport Authority FY2025, Ansley FY2024,
  Arlington FY2024, Atkinson FY2024, Blue Hill PS FY2025, Central City FY2024, Fort Calhoun CS FY2025,
  Mullen FY2024). They are not applied until the user decides.
