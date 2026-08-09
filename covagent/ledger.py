from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path

# The closed vocabulary shared by three things: the ledger aggregates, the formulas the
# extractor is allowed to author, and the strict name check in evaluate.py. Adding a name
# here is the one place to widen what an unseen covenant can refer to.
CATEGORIES: tuple[str, ...] = (
    "revenue",
    "opex",
    "payroll",
    "utilities",
    "taxes",
    "insurance",
    "lease",
    "interest_expense",
    "capex",
    "telecom",
    "marketing",
    "advisory",
    "financing_inflow",
    "transfer_subsidiary",
    "depreciation",
    "freight",
    "professional_fees",
    "provisions",
    "distributions",
    "principal_repayment",
    "disposal_proceeds",
    "other_income",
    "other_expense",
)

# Derived at aggregate time rather than assigned per row.
DERIVED = ("related_party", "transfer_unrestricted", "transfer_restricted")

# Rules are ordered; the first pattern to match a description wins. They are written
# against `description` only -- `counterparty` is deliberate noise in this dataset and
# any rule touching it poisons the aggregates.
CATEGORY_RULES: list[tuple[str, str]] = [
    ("revenue", r"sales settlement"),
    ("financing_inflow", r"facility drawdown|loan drawdown|term loan|bond issue proceeds|financing proceeds"),
    ("transfer_subsidiary", r"transfer of .*(asset|equipment)|asset transfer|capital contribution to"),
    ("capex", r"purchase of .*equipment|capital expenditure|acquisition of"),
    (
        "opex",
        r"operating and maintenance expenses|servicing and operating costs|operating costs"
        r"|servicing|repair works|remediation|clearance works|inspection and survey|arbitration",
    ),
    # Interest must precede tax/lease: "interest on finance sublease" is interest, not lease.
    ("interest_expense", r"\binterest\b|coupon|overdraft|revolver"),
    ("insurance", r"insurance|insurer|underwrit|fidelity bond|premium refund"),
    (
        "marketing",
        r"marketing|advertis|ad campaign|media buy|sponsorship|exhibition|brand|newsletter|collateral|artwork|press insertion",
    ),
    ("telecom", r"telecom|leased line|broadband|mobile plan|antenna mast"),
    ("payroll", r"payroll|staff|personnel|wage|salary"),
    ("utilities", r"electricit|utility|water|heating|gas supply|power supply"),
    ("taxes", r"\btax\b|levy|duty|excise|vat\b|customs"),
    ("lease", r"\brent\b|rental|lease|sublet"),
    ("advisory", r"advisory|consult|retainer|professional fee"),
    ("depreciation", r"depreciation|amortis|amortiz|impairment"),
    ("freight", r"freight|haulage|shipping|courier|logistics charge|demurrage"),
    ("provisions", r"provision|write-down|write down|writeoff|write-off|bad debt|allowance for"),
    ("distributions", r"dividend|distribution to|share buyback|capital return"),
    (
        "principal_repayment",
        r"principal repayment|repayment of principal|loan amortisation|scheduled repayment",
    ),
    ("disposal_proceeds", r"disposal proceeds|proceeds from (sale|disposal)|sale of (asset|equipment|land)"),
]

COMPILED = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in CATEGORY_RULES]

# Contra-credit wording. A positive amount whose description matches one of these is a
# reduction of the matching expense family, not revenue.
CONTRA_RE = re.compile(
    r"refund|rebate|returned|credit received|reversal|incentive received|released|recovered"
    r"|adjustment credit|co-operative funding|discount|overbilling|unused|unclaimed|sweep back",
    re.IGNORECASE,
)


@dataclass
class Txn:
    txn_id: str
    scenario: str
    date: str
    account_id: str
    counterparty: str
    description: str
    amount: float | None
    currency: str
    category: str
    is_contra: bool
    amount_missing: bool = False
    fx_rate: float | None = None  # None on a non-USD row we could not convert

    @property
    def quarter(self) -> int:
        return (int(self.date[5:7]) - 1) // 3 + 1


# Model-assigned stem -> category, learned for descriptions the rules above don't match.
# Populated by covagent.taxonomy; a reviewable, hand-correctable JSON artefact.
LEARNED: dict[str, str] = {}


def stem_of(description: str) -> str:
    """Strip the '— <place>, <period>' qualifier and trailing year to get a stable key."""
    head = description.split(" — ")[0]
    head = re.sub(r"\s*\b(19|20)\d\d\b\s*$", "", head)
    return head.strip().lower()


def load_learned(path: Path) -> None:
    if path.exists():
        LEARNED.update(json.loads(path.read_text()))


def categorise(description: str) -> str:
    for name, pattern in COMPILED:
        if pattern.search(description):
            return name
    return LEARNED.get(stem_of(description), "uncategorised")


def rates_for(fx_rates: dict, scenario: str) -> dict[str, float]:
    """Rates disclosed for one borrower.

    Accepts either the per-borrower shape {scenario: {"EUR": 1.16}} or a flat {"EUR": 1.16}
    applied to everyone, so an older facts file still loads.
    """
    if fx_rates and all(isinstance(v, dict) for v in fx_rates.values()):
        return fx_rates.get(scenario, {})
    return fx_rates or {}


def load_ledger(path: Path, scenarios: set[str] | None, fx_rates: dict) -> list[Txn]:
    """Rows for the borrowers named in `scenarios`; every row when that is None.

    The unfiltered form exists to report what identifiers the ledger actually carries when a
    borrower the template asks about turns out to match none of them.
    """
    txns = []
    for row in csv.DictReader(path.open()):
        scenario = row["txn_id"].split("-")[1]
        if scenarios is not None and scenario not in scenarios:
            continue
        raw = row["amount"].strip()
        missing = raw == ""
        amount = None if missing else float(raw)
        currency = row["currency"]
        # A rate is only ever the one this borrower's auditor disclosed. An undisclosed
        # currency is left unconverted and flagged, never quietly treated as parity.
        rate = 1.0 if currency == "USD" else rates_for(fx_rates, scenario).get(currency)
        if amount is not None and rate is not None:
            amount *= rate
        description = row["description"]
        txns.append(
            Txn(
                txn_id=row["txn_id"],
                scenario=scenario,
                date=row["date"],
                account_id=row["account_id"],
                counterparty=row["counterparty"],
                description=description,
                amount=amount,
                currency=currency,
                category=categorise(description),
                is_contra=amount is not None and amount > 0 and bool(CONTRA_RE.search(description)),
                fx_rate=rate,
                amount_missing=missing,
            )
        )
    return txns


def apply_adjustments(txns: list[Txn], adjustments: list[dict]) -> list[Txn]:
    """Apply auditor amount overrides, reclassifications and period exclusions.

    Adjustment kinds:
      amount_override  {txn_id, amount}        -- fills a null or corrects a figure
      reclassify       {txn_id, to_category}   -- moves a row between categories
      exclude          {txn_id}                -- drops a row from the covenant period
    """
    by_id = {t.txn_id: i for i, t in enumerate(txns)}
    out = list(txns)
    for adj in adjustments:
        idx = by_id.get(adj.get("txn_id", ""))
        if idx is None:
            continue
        txn = out[idx]
        kind = adj["kind"]
        if kind == "amount_override":
            out[idx] = replace(txn, amount=-abs(float(adj["amount"])), amount_missing=False)
        elif kind == "reclassify":
            out[idx] = replace(txn, category=adj["to_category"])
        elif kind == "exclude":
            out[idx] = replace(txn, amount=0.0)
    return out


LEGAL_FORMS = {"llp", "llc", "ltd", "jsc", "inc", "corp", "co", "company", "limited", "plc", "gmbh", "lp"}


def normalise_name(name: str) -> str:
    """Fold a counterparty name to a comparable key.

    Periods are deleted rather than spaced so "L.L.P." folds to "llp", and legal-form
    tokens are stripped only from the tail -- "Atyrau Holding Group LLP" must keep
    "holding" and "group", which are part of the name, not the legal form.
    """
    cleaned = name.lower().replace(".", "")
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    tokens = cleaned.split()
    while tokens and tokens[-1] in LEGAL_FORMS:
        tokens.pop()
    return " ".join(tokens)


def aggregates(
    txns: list[Txn],
    related_parties: set[str],
    extra: dict[str, float] | None = None,
    quarter: int | None = None,
    unrestricted_subsidiaries: set[str] | None = None,
) -> dict[str, float]:
    """Positive magnitudes per category, plus identity-based `related_party`.

    Subsidiary transfers are split by security status: only subsidiaries the KYC places
    outside the security perimeter count as `transfer_unrestricted`.
    """
    rows = [t for t in txns if quarter is None or t.quarter == quarter]
    totals: dict[str, float] = {}
    for txn in rows:
        if txn.amount is None:
            continue
        if txn.category == "revenue":
            if txn.amount > 0 and not txn.is_contra:
                totals["revenue"] = totals.get("revenue", 0.0) + txn.amount
            continue
        if txn.category == "financing_inflow":
            if txn.amount > 0:
                totals["financing_inflow"] = totals.get("financing_inflow", 0.0) + txn.amount
            continue
        if txn.amount < 0:
            totals[txn.category] = totals.get(txn.category, 0.0) + -txn.amount
        elif txn.is_contra:
            totals[txn.category] = totals.get(txn.category, 0.0) - txn.amount

    normalised = {normalise_name(n) for n in related_parties}
    unrestricted = {normalise_name(n) for n in (unrestricted_subsidiaries or set())}
    related = 0.0
    for txn in rows:
        if txn.amount is None or txn.amount >= 0:
            continue
        name = normalise_name(txn.counterparty)
        if name in normalised:
            related += -txn.amount
        if txn.category == "transfer_subsidiary":
            key = "transfer_unrestricted" if name in unrestricted else "transfer_restricted"
            totals[key] = totals.get(key, 0.0) + -txn.amount
            # A transfer of capital equipment is a capital outflow, so it belongs in the
            # borrower's total capital expenditure as well as in its own bucket.
            totals["capex"] = totals.get("capex", 0.0) + -txn.amount
    totals["related_party"] = related

    # Scalars from documents stand on their own; they are not contributions to a ledger total.
    for key, value in (extra or {}).items():
        totals[key] = value
    return {k: max(v, 0.0) if k != "related_party" else v for k, v in totals.items()}
