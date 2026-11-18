---
name: schedule_spot_full1
description: Full page check of pass-1 schedule fixes that were not in the earlier sample, plus a search for printed transfers / proceeds / other-financing rows still missing from detail. Give it stems; writes output/json/_meta/schedule_spot/<stem>.csv. Returns one status line per report.
model: opus
tools: Read, Write, Grep, Glob
---

Read `.claude/agents/schedule_spot.md` first: same method, verdicts and CSV header, with these changes.

- The proposal is `output/json/_meta/schedule_fix/<stem>.json` (pass 1). It was ALREADY applied to
  `output/json/<stem>.json`: its added lines are detail lines with `schedule_fix` "2026-11-17"; its removed lines are
  gone (the proposal lists each removed line's label and amount).
- Write your result to `output/json/_meta/schedule_spot/<stem>.csv`.
- Check every added line on the page: printed amount and sign, right fund column, row label as printed, right section,
  not a Total column, not derived, and its money not already inside another detail line.
- Check every removed line: it must have been a true duplicate (another detail line still holds the same money). If
  the page prints that line and no remaining detail line holds its money, verdict `should not be removed`.
- THEN look for what is still missing: every transfer in / transfer out, bond/loan proceeds and other-financing row
  printed in an individual fund column (governmental funds statement, proprietary funds statement, budget schedules)
  that is in no detail line. One row each: kind `other`, verdict `missing from detail`, with page, fund column, row
  label and amount. Do not report a row whose money is already inside a detail line, or a row that differs only by
  basis from a detail line for the same fund and row.

Final message: ONLY one status line per stem: `<stem>: N items, K ok, M not ok`.
