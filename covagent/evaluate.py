from __future__ import annotations

import ast
import operator
from dataclasses import dataclass

from .ledger import CATEGORIES, DERIVED, Txn, aggregates

BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

FUNCS = {"max": max, "min": min, "abs": abs, "sum": lambda *a: sum(a)}

COMPARISONS = {
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}


class FormulaError(Exception):
    pass


def evaluate_formula(expr: str, values: dict[str, float], known: set[str] | None = None) -> float:
    """Evaluate a covenant metric over category aggregates.

    Only arithmetic, max/min/abs/sum and bare category names are permitted, so an
    extractor-authored formula can never execute arbitrary code.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"cannot parse {expr!r}") from exc

    def visit(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise FormulaError(f"non-numeric constant {node.value!r}")
            return float(node.value)
        if isinstance(node, ast.Name):
            # An unknown name must not quietly become 0.0: on an unseen archetype that
            # yields a confident wrong number instead of a visible failure.
            if known is not None and node.id not in known:
                raise FormulaError(f"unknown identifier {node.id!r} in {expr!r}")
            return float(values.get(node.id, 0.0))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and type(node.op) in BINOPS:
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise FormulaError(f"division by zero in {expr!r}")
            return BINOPS[type(node.op)](left, right)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name not in FUNCS:
                raise FormulaError(f"unknown function {name}")
            return float(FUNCS[name](*(visit(a) for a in node.args)))
        raise FormulaError(f"unsupported expression element {ast.dump(node)[:60]}")

    return visit(tree)


def referenced_names(expr: str) -> set[str]:
    """Category names a formula reads, for building its derivation record."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - called


@dataclass
class Covenant:
    clause: str
    metric: str
    operator: str
    threshold: float
    quarter: int | None = None
    trigger: dict | None = None
    definition_verbatim: str = ""


@dataclass
class Verdict:
    status: str
    actual: float
    evidence_txn_id: str | None = None
    trigger_active: bool = True


def compute(
    covenant: Covenant,
    txns: list[Txn],
    related_parties: set[str],
    extra: dict[str, float],
    unrestricted: set[str] | None = None,
) -> tuple[float, bool]:
    totals = aggregates(
        txns, related_parties, extra, quarter=covenant.quarter, unrestricted_subsidiaries=unrestricted
    )
    known = set(CATEGORIES) | set(DERIVED) | set(extra) | set(totals)
    actual = abs(evaluate_formula(covenant.metric, totals, known))
    trigger_active = True
    if covenant.trigger:
        trig = covenant.trigger
        observed = abs(evaluate_formula(trig["metric"], totals, known))
        trigger_active = COMPARISONS[trig["operator"]](observed, float(trig["value"]))
    return actual, trigger_active


def verdict(
    covenant: Covenant,
    txns: list[Txn],
    related_parties: set[str],
    extra: dict[str, float],
    unrestricted: set[str] | None = None,
) -> Verdict:
    actual, trigger_active = compute(covenant, txns, related_parties, extra, unrestricted)
    if not trigger_active:
        return Verdict("COMPLIANT", actual, None, False)
    compliant = COMPARISONS[covenant.operator](actual, covenant.threshold)
    status = "COMPLIANT" if compliant else "BREACH"
    return Verdict(status, actual, None, True)


def find_evidence(
    covenant: Covenant,
    txns: list[Txn],
    related_parties: set[str],
    extra: dict[str, float],
    baseline: Verdict,
    unrestricted: set[str] | None = None,
    candidates: set[str] | None = None,
) -> tuple[str | None, int]:
    """Leave-one-out over rows the documents single out.

    CASE.ru defines evidence as the row whose reclassification, inclusion, exclusion or
    correction causes the breach -- not any row that happens to move the arithmetic.
    Removing a borrower's only revenue row flips almost every ratio, so the candidate set
    is restricted to auditor-adjusted rows and rows inside an identity-restricted set
    (related party, unrestricted-subsidiary transfer). A row that merely contributes to an
    aggregate is explicitly not evidence.
    """
    flippers = []
    for i, txn in enumerate(txns):
        if txn.amount in (None, 0.0):
            continue
        if candidates is not None and txn.txn_id not in candidates:
            continue
        reduced = txns[:i] + txns[i + 1 :]
        try:
            trial = verdict(covenant, reduced, related_parties, extra, unrestricted)
        except (FormulaError, ZeroDivisionError):
            continue
        if trial.status != baseline.status:
            flippers.append((abs(txn.amount), txn.txn_id))
    if not flippers:
        return None, 0
    # Several rows can each carry the verdict on their own. Naming none scores nothing where
    # the key names one, and nothing is lost where it names none, so the largest is offered
    # and the ambiguity is still reported by the caller rather than hidden.
    flippers.sort(reverse=True)
    return flippers[0][1], len(flippers)
