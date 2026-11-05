"""Build config/category_cleanup.xlsx from the user's filled decision sheet: answer (or label when blank) next to a suggested standard name.
Usage: python3 scripts/category_cleanup.py config/other_lines_decided_by_user.xlsx config/category_cleanup.xlsx"""
import re
import sys
import openpyxl
from openpyxl.styles import Font, PatternFill

SRC = sys.argv[1]
OUT = sys.argv[2]

# (section R/S, regex on the proposed name or label) -> (suggested, why)
RULES = [
    # ---------- revenue ----------
    ("R", r"loan (collection|payment)|loan principal|principal (collected|payment)|housing loan (collection|receipt|revenue)|"
          r"revolving loan|^loan revenues$|reuse - loan|reuse payments",
     "Loan Repayments", "money paid back on loans the government made (existing category)"),
    ("R", r"loan forgiv", "Loan Forgiveness", "same thing, one spelling"),
    ("R", r"^loan proceeds$", "Loan Proceeds", "borrowing, not earned revenue"),
    ("R", r"inter-?fund loan", "Interfund Loan", "same thing, one spelling"),
    ("R", r"deposit", "Deposits", "customer/utility/meter/tenant/security deposits: one category"),
    ("R", r"^local receipts|^local sources$|local receipts$|^local receipts - empire", "Local Receipts", "same thing, one name"),
    ("R", r"non-?revenue|non ?-?program|nonprogram", "Other Non-Revenue Receipts", "existing category; you mapped 9000 Nonprogram receipts here"),
    ("R", r"property (&|and) (motor vehicle|city)|property motor vehicle", None, None),  # handled below
    ("R", r"allocation from general fund|general fund support", "Allocation from General Fund", "same thing, one name"),
    ("R", r"cash balance from (merged|dissolved)", "Cash Balance from Merged District", "your three answers differed (2 other, 1 named)"),
    ("R", r"collections? in (the )?districts?", "Collection in District", "your three answers differed (1 other, 2 named)"),
    ("R", r"patronage|capital credits", "Patronage Capital Credits", "same thing, one name"),
    ("R", r"budget adjustment|capital asset adjustment", "Adjustments", "accounting adjustments, one name"),
    ("R", r"hous(e)?ing rehab", "Housing Rehab", "typo fixed (Houseing)"),
    ("R", r"opi(o)?ds? settlement", "Opioid Settlement", "spelling fixed"),
    ("R", r"mis(c)?hellaneous local|miscellaneaous local", "Local Receipts", "same thing, one name"),
    ("R", r"^miscellaneaous$|^miscellaneous$", "Miscellaneous", "spelling fixed"),
    ("R", r"local assist", "federal grants", "Local Assistance and Tribal Consistency Fund is a federal program"),
    ("R", r"^sanitation$", "Utility Charges", "existing rule: garbage/sanitation charges -> Utility Charges"),
    ("R", r"^sales tax$", "sales tax", "existing category"),
    ("R", r"sales (&|and) franchise", "Sales and Franchise Tax", "'&' written out"),
    ("R", r"farm income", "Farm Income", "same thing, one name"),
    ("R", r"uncollect", "Uncollectible", "spelling fixed"),
    ("R", r"amortization of deferred", "Amortization of Deferred Inflows", "shorter name"),
    ("R", r"allowances? for funds used", "Allowance for Funds Used During Construction", "capitalized"),
    ("R", r"railroad", "Railroad and Public Service Entities", "'&' written out"),
    ("R", r"receipts (&|and) transfers", "Receipts and Transfers", "'&' written out"),
    ("R", r"^receipts$", "other", "label is only 'RECEIPTS', nothing says what it is"),
    ("R", r"^general$", "other", "label is only 'General', nothing says what it is"),
    ("R", r"^debt service$", "Debt Service Fund Receipts", "'Debt Service' is a spending category; this is a receipt"),
    # ---------- spending ----------
    ("S", r"airport|landing field|terminal|general aviation|runway|air ?park|air service|airfield|air show",
     "Airport", "all airport lines, one name"),
    ("S", r"^contra:|transfer to depreciation", "Contra: Transfers", "same adjustment, one name"),
    ("S", r"^lnk", "LNK Enterprise Park", "your answer was cut off ('LNK ')"),
    ("S", r"interlocal payments|mini grants|tire amnesty|pass thru", "Grants/Payments to Other Entities", "existing category"),
    ("S", r"^salar|payroll|personal service|personnel service|^administrative salaries", "Salaries and Wages", "existing category"),
    ("S", r"employee benefit|benefits paid|social security|retirement|health insurance|health and life|group insurance",
     "Employee Benefits and Payroll Taxes", "existing category"),
    ("S", r"^insurance|risk management|workmen", "Insurance", "existing category"),
    ("S", r"repair|maintenance contract|ground maintenance|^parts$|vehicle expenses|tractor expenses|nonrecurring contract services - fleet",
     "Repairs and Maintenance", "existing category"),
    ("S", r"maintenance and supplies|suppl|small tools|office expense|commodities|uniforms",
     "Supplies and Materials", "existing category"),
    ("S", r"cost of fuel sold|fuel resale|resale of fuel|fuel purchases for resale", "Purchased Power/Cost of Sales",
     "existing category for goods bought to resell"),
    ("S", r"fuel|gas and oil|motor fuels", "Fuel", "existing category"),
    ("S", r"contract(ed)? (services|labor)|professional (services|fees)|technical services|financial services|"
          r"administrative services|purchased services|service providers|restaurant management",
     "Professional/Contract Services", "existing category"),
    ("S", r"travel|training|seminar|meetings|dues", "Dues, Training and Travel", "existing category"),
    ("S", r"print|postage", "Printing, Publishing and Postage", "existing category"),
    ("S", r"advertising|public relations", "Advertising and Marketing", "same thing, one name"),
    ("S", r"utilit|telephone|communications", "Utilities and Telephone",
     "not 'utilities' (that is the utility-fund function category)"),
    ("S", r"loss on|gain \(loss\)", "Loss on Disposal of Assets", "4 wordings, one name"),
    ("S", r"scholarship|educational subsid|student aid|student assistance", "Scholarships and Student Aid", "same thing, one name"),
    ("S", r"bad debt|uncollectible", "Bad Debt", "same thing, one name"),
    ("S", r"closure", "Landfill Closure and Postclosure", "same thing, one name"),
    ("S", r"depreciation", "Depreciation", "existing category"),
    ("S", r"equipment rent", "Rent/Leases", "existing category"),
    ("S", r"service equipment|minor equipment", "Equipment Purchases", "existing category"),
    ("S", r"special assessments|irrigation district assessment", "Special Assessments Paid", "same thing, one name"),
    ("S", r"^sales tax$", "Other Taxes Paid", "existing category for taxes the government pays"),
    ("S", r"non ?-?program|nonprogram", "Non-Program Expenditures", "same thing, one name"),
    ("S", r"deposit refund|deposits refunded|meter refunds|restricted cash deposits", "Deposit Refunds", "same thing, one name"),
    ("S", r"^refunds$", "Refunds", ""),
    ("S", r"licen|^fees$|processing fees|credit card fees|administrative fee|disposal fees", "Licenses and Fees", "same thing, one name"),
    ("S", r"arpa|american rescue plan", "ARPA Spending", "same thing, one name"),
    ("S", r"public transportation|^transportation$", "Mass Transit", "existing category"),
    ("S", r"keno|lottery", "Lottery", "same thing, one name"),
    ("S", r"operating", "Operating", "existing category"),
    ("S", r"venue and meals|food - sports banquet|harvest expenses|hoops mania", "Events", "event costs, one name"),
    ("S", r"inter-?fund loan", "Interfund Loan", "same name as the revenue side"),
    ("S", r"budget adjustment", "Adjustments", "same name as the revenue side"),
    ("S", r"^election$", "Elections", ""),
    ("S", r"donat", "Donations Given", "spending, so not the revenue category donations/contributions"),
    ("S", r"^fees$", "Licenses and Fees", "same thing, one name"),
    ("S", r"^disbursements$|^total disbursements$|^miscellanous$", "other", "label says nothing about what it is"),
]


def title(s):
    small = {"and", "or", "of", "the", "for", "to", "in", "on", "a"}
    out = []
    for i, w in enumerate(s.split()):
        out.append(w if any(c.isupper() for c in w) else (w if i and w in small else w[:1].upper() + w[1:]))
    return " ".join(out)


def suggest(sec, label, answer):
    base = (answer or label or "").strip()
    probes = [base.lower(), (label or "").lower(), re.sub(r"^\d{4}\s+", "", (label or "").lower())]
    if base.lower() == "other":
        probes = probes[1:]
    for s, pat, sug, why in RULES:
        if s != sec or not any(re.search(pat, p) for p in probes):
            continue
        if sug is None:  # combined property tax lines
            if any("city" in p for p in probes):
                return "Property and City Taxes", "combined tax line the report doesn't split; '&' written out"
            return "Property and Motor Vehicle Taxes", "combined tax line the report doesn't split; '&' written out"
        return sug, why
    if answer:
        if answer.strip().lower() == "other":
            return "other", "your answer"
        return answer.strip(), "your answer"
    return title(re.sub(r"^\d{4}\s+", "", base).strip()), "label used (left blank)"


wb = openpyxl.load_workbook(SRC)
ws = wb["Decide"]
rows = list(ws.iter_rows(min_row=2, values_only=True))

out = openpyxl.Workbook()
o = out.active
o.title = "Review"
head = ["Revenue or Spending", "Label", "Times Seen", "Total $", "Your Answer", "Suggested", "Why", "Final (edit if needed)", "key"]
o.append(head)
for c in o[1]:
    c.font = Font(bold=True)
changed = 0
names = set()
for r in rows:
    kind, label, n, amt, now, ans, key = r[:7]
    sec = "R" if kind == "Revenue" else "S"
    sug, why = suggest(sec, label, ans)
    if (ans or label) != sug:
        changed += 1
    names.add((kind, sug))
    o.append([kind, label, n, amt, ans or "(blank: label used)", sug, why, sug, key])
yellow = PatternFill("solid", fgColor="FFF2CC")
for row in o.iter_rows(min_row=2):
    row[3].number_format = "#,##0"
    row[7].fill = yellow
for col, w in zip("ABCDEFGHI", (12, 50, 8, 13, 34, 36, 60, 36, 10)):
    o.column_dimensions[col].width = w
o.column_dimensions["I"].hidden = True
o.freeze_panes = "C2"
o.auto_filter.ref = f"A1:H{len(rows) + 1}"

s = out.create_sheet("Final categories")
s.append(["Revenue or Spending", "Category", "Labels"])
for c in s[1]:
    c.font = Font(bold=True)
from collections import Counter
cnt = Counter((("Revenue" if (r[0] == "Revenue") else "Spending"), suggest("R" if r[0] == "Revenue" else "S", r[1], r[5])[0]) for r in rows)
for (k, n), v in sorted(cnt.items(), key=lambda t: (t[0][0], t[0][1].lower())):
    s.append([k, n, v])
s.column_dimensions["A"].width = 12
s.column_dimensions["B"].width = 44
out.save(OUT)
print(f"{len(rows)} rows; {changed} suggestions differ from answer/label; {len(cnt)} distinct final categories "
      f"({sum(1 for k in cnt if k[0]=='Revenue')} revenue, {sum(1 for k in cnt if k[0]=='Spending')} spending)")
