"""Best Fit Category for the one-tab workbook (user 2026-11-04).

`best_fit_category` is the row's own category unless a person has accepted, after checking the source PDF, a
more specific category for that one record (config/best_fit_verified.csv). Rules in config/best_fit_rules.csv
only *propose* a category; a proposal is never published until it is accepted.

Accuracy rules (user 2026-11-04):
- only in-scope rows are considered: counties, cities and villages, governmental fund type, and categories that
  are catch-alls (receipts taxes (unlabeled) / other taxes / other, disbursements other);
- proposals use the printed label only (never entity, fund or government type); only parentheticals that are
  explicitly fund/column identifiers ("(General Fund)", "(Water and Sewer Funds)", "(Fire Column)") are ignored;
- every rule is evaluated: two different targets -> "conflict - review"; a label joining concepts (and, &, /, +,
  comma, including) that is not an approved compound term -> "combined - review";
- a decision applies to one record_id and only while the report's JSON (sha256), its extraction_attempt and the
  record's fingerprint are unchanged; otherwise it is "stale - unresolved" and the original category is kept;
- no amount, category, label or any other existing value is changed.
"""
import csv
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES_CSV = ROOT / "config" / "best_fit_rules.csv"
COMPOUNDS_TXT = ROOT / "config" / "best_fit_compounds.txt"
VERIFIED_CSV = ROOT / "config" / "best_fit_verified.csv"

SCOPE_ENTITY_TYPES = {"county", "city", "village"}
SCOPE_FUND_TYPE = "governmental"
CATCH_ALL = {("receipts", "taxes (unlabeled)"), ("receipts", "other taxes"), ("receipts", "other"),
             ("disbursements", "other")}

# a parenthetical that is only a fund / column identifier: words ending in Fund(s) / Column(s), no digits
_FUND_PAREN = re.compile(r"\s*\((?=[^()]*\b(?:Funds?|Columns?)\)\s*)[A-Za-z&'.,/\- ]+\)", re.I)
_COMBINER = re.compile(r"\band\b|&|/|\+|,|\bincluding\b", re.I)

LOG_COLS = ["record_id", "entity_name", "entity_type", "filename", "section", "source", "schedule", "fund",
            "original_label", "amount", "pages", "page_link", "original_category", "proposed_category",
            "best_fit_category", "rule_id", "matched_text", "status", "decision", "reject_type", "source_used",
            "decision_pages", "evidence", "json_sha256", "extraction_attempt", "fingerprint"]


def _read_csv(p):
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load():
    rules = []
    for r in _read_csv(RULES_CSV):
        r["rx"] = re.compile(r"(?:%s)" % r["pattern"], re.I)
        r["section"], r["from_category"] = r["section"].strip().lower(), r["from_category"].strip().lower()
        rules.append(r)
    compounds = [ln.strip() for ln in (COMPOUNDS_TXT.read_text(encoding="utf-8").splitlines()
                                       if COMPOUNDS_TXT.exists() else []) if ln.strip() and not ln.startswith("#")]
    decisions = {}
    for r in _read_csv(VERIFIED_CSV):
        if r.get("record_id"):
            if r["record_id"] in decisions:
                raise SystemExit(f"best_fit_verified.csv: record_id {r['record_id']} listed twice")
            decisions[r["record_id"]] = r
    return rules, compounds, decisions


def matched_text(label):
    """The printed label with only fund/column-identifier parentheticals removed (label itself unchanged)."""
    return re.sub(r"\s+", " ", _FUND_PAREN.sub("", str(label or ""))).strip()


def is_combined(text, compounds):
    t = text
    for c in compounds:
        t = re.sub(re.escape(c), "#", t, flags=re.I)
    return bool(_COMBINER.search(t))


def json_sha256(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def fingerprint(filename, list_key, item):
    """Identifies the record's content at extraction time; any change makes a decision stale."""
    keys = {"filename": filename, "list": list_key}
    for k in ("section", "schedule", "fund", "department", "original_label", "amount", "source_page",
              "category"):
        keys[k] = item.get(k) if isinstance(item, dict) else None
    return hashlib.sha256(json.dumps(keys, sort_keys=True, default=str).encode()).hexdigest()[:20]


def in_scope(row):
    return (str(row.get("entity_type") or "").lower() in SCOPE_ENTITY_TYPES
            and str(row.get("fund_type") or "").lower() == SCOPE_FUND_TYPE
            and (str(row.get("section") or "").lower(), str(row.get("category") or "").lower()) in CATCH_ALL)


def propose(row, rules, compounds):
    """(proposed_category, rule_id, matched_text, status) from the label alone."""
    sec, cat = str(row.get("section") or "").lower(), str(row.get("category") or "").lower()
    text = matched_text(row.get("original_label"))
    hits = {}
    for r in rules:
        if r["section"] == sec and r["from_category"] == cat and r["rx"].fullmatch(text):
            hits.setdefault(r["best_fit"].lower(), []).append(r)
    if not hits:
        # a label that only *contains* a rule's term and joins it with something else ("Salaries and Benefits")
        # is listed for review; it is never proposed
        part = sorted({r["rule_id"] for r in rules if r["section"] == sec and r["from_category"] == cat
                       and r["rx"].search(text)})
        if part and is_combined(text, compounds):
            return None, ";".join(part), text, "combined - review"
        return None, "", text, "no rule"
    if len(hits) > 1:
        ids = ";".join(r["rule_id"] for rs in hits.values() for r in rs)
        return None, ids, text, "conflict - review"
    (rs,) = hits.values()
    rule = rs[0]
    if is_combined(text, compounds):
        return rule["best_fit"], rule["rule_id"], text, "combined - review"
    if rule.get("status", "active").strip().lower() == "suspended":
        return rule["best_fit"], rule["rule_id"], text, "rule suspended"
    return rule["best_fit"], rule["rule_id"], text, "proposed"


def apply(rows, rules, compounds, decisions, record_info):
    """Set row['best_fit_category'] on every row; return the log rows.
    record_info: record_id -> (json_sha256, extraction_attempt, fingerprint)."""
    log = []
    for row in rows:
        row["best_fit_category"] = row.get("category")
        if not in_scope(row):
            continue
        rid = row.get("record_id")
        proposed, rule_id, text, status = propose(row, rules, compounds)
        sha, attempt, fp = record_info.get(rid, ("", "", ""))
        dec = decisions.get(rid)
        entry = {c: row.get(c) for c in ("record_id", "entity_name", "entity_type", "filename", "section", "source",
                                          "schedule", "fund", "original_label", "amount", "pages", "page_link")}
        entry.update(original_category=row.get("category"), proposed_category=proposed, rule_id=rule_id,
                     matched_text=text, json_sha256=sha, extraction_attempt=attempt, fingerprint=fp,
                     decision="", reject_type="", source_used="", decision_pages="", evidence="")
        if dec:
            entry.update(decision=dec.get("decision", ""), reject_type=dec.get("reject_type", ""),
                         source_used=dec.get("source_used", ""), decision_pages=dec.get("pages", ""),
                         evidence=dec.get("evidence", ""))
            same = (dec.get("json_sha256") == sha and str(dec.get("extraction_attempt")) == str(attempt)
                    and dec.get("fingerprint") == fp)
            d = (dec.get("decision") or "").strip().lower()
            if not same:
                status = "stale - unresolved"
            elif d == "accept":
                if status in ("proposed", "rule suspended") and (dec.get("proposed") or "").lower() == \
                        str(proposed or "").lower():
                    status = "accepted"
                    row["best_fit_category"] = dec.get("proposed")
                else:   # the accepted category no longer matches what the rules propose: hold it for review
                    status = "stale - unresolved"
            elif d == "reject":
                status = "rejected"
            elif d == "unresolved":
                status = "unresolved"
        entry["status"] = status
        entry["best_fit_category"] = row["best_fit_category"]
        log.append(entry)
    return log


SUMMARY_COLS = ["level", "section", "category", "rule_id", "records", "amount", "no_rule", "proposed",
                "proposed_amount", "accepted", "accepted_amount", "rejected", "unresolved", "conflict_review",
                "combined_review", "rule_suspended", "stale_unresolved", "remaining_in_catch_all",
                "remaining_amount"]


def summary(log):
    """Counts and $ per catch-all category and per rule (in-scope rows only)."""
    out = []

    def tally(level, key_of):
        groups = {}
        for e in log:
            groups.setdefault(key_of(e), []).append(e)
        for k, es in sorted(groups.items(), key=lambda x: tuple(str(v) for v in x[0])):
            a = lambda xs: round(sum(float(x.get("amount") or 0) for x in xs), 2)  # noqa: E731
            by = lambda s: [x for x in es if x["status"] == s]  # noqa: E731
            remaining = [x for x in es if x["status"] != "accepted"]
            out.append(dict(level=level, section=k[0], category=k[1], rule_id=k[2] if len(k) > 2 else "",
                            records=len(es), amount=a(es), no_rule=len(by("no rule")),
                            proposed=len(by("proposed")), proposed_amount=a(by("proposed")),
                            accepted=len(by("accepted")), accepted_amount=a(by("accepted")),
                            rejected=len(by("rejected")), unresolved=len(by("unresolved")),
                            conflict_review=len(by("conflict - review")), combined_review=len(by("combined - review")),
                            rule_suspended=len(by("rule suspended")), stale_unresolved=len(by("stale - unresolved")),
                            remaining_in_catch_all=len(remaining), remaining_amount=a(remaining)))
    tally("category", lambda e: (e["section"], str(e["original_category"]).lower()))
    tally("rule", lambda e: (e["section"], str(e["original_category"]).lower(), e["rule_id"] or "(no rule)"))
    return out
