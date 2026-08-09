from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .dataset import Dataset, add_data_argument
from .evaluate import Covenant, FormulaError, find_evidence, referenced_names, verdict
from .ledger import aggregates, apply_adjustments, load_learned, load_ledger, normalise_name
from .levels import add_level_argument, degrade

ROOT = Path(__file__).resolve().parent.parent


def build_submission(facts: dict, template: dict, meta: dict, data: Dataset) -> tuple[dict, list[str], dict]:
    scenarios = set(template["answers"])
    load_learned(data.artefact("stem_categories.json"))
    txns = load_ledger(data.ledger, scenarios, facts["fx_rates"])
    submission = json.loads(json.dumps(template))
    submission.update(meta)
    warnings: list[str] = []
    derivations: dict[str, dict] = {}

    # A borrower the ledger never mentions aggregates to 0.00 everywhere and still answers every
    # cell, so nothing downstream can tell it apart from a borrower who simply spent nothing.
    # The likeliest cause is a txn_id that does not carry the scenario the template asks about.
    missing = sorted(scenarios - {t.scenario for t in txns})
    if missing:
        seen = sorted({t.txn_id.split("-")[1] for t in load_ledger(data.ledger, None, {})})[:6]
        warnings.append(
            f"{', '.join(missing)} carries no ledger rows at all; every aggregate for it is 0.00. "
            f"The ledger identifies borrowers as {', '.join(seen)}... -- check that matches the template"
        )

    unknown = sorted({t.description.split(" — ")[0] for t in txns if t.category == "uncategorised"})
    if unknown:
        warnings.append(
            f"{len(unknown)} description stems are uncategorised and contribute to no aggregate: "
            + "; ".join(unknown[:5])
            + ("; ..." if len(unknown) > 5 else "")
        )

    for scenario in sorted(scenarios):
        spec = facts["scenarios"].get(scenario)
        rows = [t for t in txns if t.scenario == scenario]
        if spec is None:
            warnings.append(f"{scenario}: no extracted facts; leaving template nulls")
            continue
        rows = apply_adjustments(rows, spec.get("adjustments", []))
        related = set(spec.get("related_parties", []))
        unrestricted = set(spec.get("unrestricted_subsidiaries", []))
        extra = spec.get("extra", {})
        candidates = evidence_candidates(rows, spec, related, unrestricted)

        for clause in sorted(submission["answers"][scenario]):
            raw = spec["covenants"].get(clause)
            if raw is None:
                warnings.append(f"{scenario} {clause}: no covenant extracted")
                continue
            covenant = Covenant(
                clause=clause,
                metric=raw["metric"],
                operator=raw["operator"],
                threshold=float(raw["threshold"]),
                quarter=raw.get("quarter"),
                trigger=raw.get("trigger"),
                definition_verbatim=raw.get("definition_verbatim", ""),
            )
            try:
                result = verdict(covenant, rows, related, extra, unrestricted)
                evidence, count = find_evidence(covenant, rows, related, extra, result, unrestricted, candidates)
            except FormulaError as exc:
                # An unseen archetype referring to something we cannot aggregate must be
                # visible. It must still be answered: CASE.ru scores an empty cell exactly
                # as it scores a wrong one, so a best effort strictly dominates a null.
                result, evidence, count = best_effort(covenant, rows, related, extra, unrestricted)
                warnings.append(
                    f"{scenario} {clause}: {exc}; answered leniently as "
                    f"{result.status} {result.actual:.2f} rather than left null"
                )
            if count > 1:
                warnings.append(f"{scenario} {clause}: {count} rows flip the verdict; no single determinant")
            submission["answers"][scenario][clause] = {
                "status": result.status,
                "actual": round(result.actual, 2),
                "evidence_txn_id": evidence,
            }
            record = derivation(covenant, rows, related, unrestricted, extra, spec, result, evidence, count, raw)
            derivations.setdefault(scenario, {})[clause] = record
            # A name the evaluator knows but the ledger never fills aggregates to 0.0 silently.
            # Widening the vocabulary makes that more likely, so a covenant that references an
            # empty category is reported rather than quietly scored as if the term were absent.
            # A non-USD row with no disclosed rate sits in the aggregate at face value.
            # If a covenant actually reads its category, say so: the number is wrong.
            unconverted = sorted(
                {
                    f"{r.txn_id} ({r.currency} {abs(r.amount):,.2f})"
                    for r in rows
                    if r.fx_rate is None and r.amount and r.category in record["contributing_rows"]
                }
            )
            if unconverted:
                warnings.append(
                    f"{scenario} {clause}: no exchange rate disclosed for "
                    f"{', '.join(unconverted)}; counted at face value, so the result is understated"
                )
            empty = [n for n, ids in record["contributing_rows"].items() if not ids]
            if empty:
                warnings.append(
                    f"{scenario} {clause}: formula references {', '.join(empty)} but no ledger row "
                    f"carries that category; it contributed 0.00"
                )
    return submission, warnings, derivations


def best_effort(covenant, rows, related, extra, unrestricted):
    """Answer a cell whose formula referenced something we cannot aggregate.

    Unknown names fall back to 0.0 -- the reading the strict evaluator refuses -- because an
    unanswered cell and a wrong one score the same, so a guess can only help. The caller
    records that this happened.
    """
    from .evaluate import COMPARISONS, Verdict, evaluate_formula
    from .ledger import aggregates

    totals = aggregates(rows, related, extra, quarter=covenant.quarter, unrestricted_subsidiaries=unrestricted)
    try:
        actual = abs(evaluate_formula(covenant.metric, totals))
    except FormulaError:
        actual = 0.0
    compliant = COMPARISONS[covenant.operator](actual, covenant.threshold)
    return Verdict("COMPLIANT" if compliant else "BREACH", actual, None, True), None, 0


def derivation(covenant, rows, related, unrestricted, extra, spec, result, evidence, count, raw) -> dict:
    """The lineage behind one cell: clause text -> inputs -> rows -> arithmetic -> verdict.

    Every figure is traceable to either a ledger row or a named document, so the answer can be
    replayed and re-verified rather than taken on trust.
    """
    totals = aggregates(rows, related, extra, quarter=covenant.quarter, unrestricted_subsidiaries=unrestricted)
    names = referenced_names(covenant.metric)
    related_norm = {normalise_name(n) for n in related}
    contributing: dict[str, list[str]] = {}
    for name in sorted(names):
        if name in extra:
            continue
        if name == "related_party":
            ids = [
                r.txn_id for r in rows if normalise_name(r.counterparty) in related_norm and r.amount and r.amount < 0
            ]
        elif name in ("transfer_unrestricted", "transfer_restricted"):
            # Derived by subsidiary status, not carried on the row's own category.
            unrestricted_norm = {normalise_name(n) for n in unrestricted}
            want_unrestricted = name == "transfer_unrestricted"
            ids = [
                r.txn_id
                for r in rows
                if r.category == "transfer_subsidiary"
                and r.amount
                and r.amount < 0
                and (normalise_name(r.counterparty) in unrestricted_norm) is want_unrestricted
            ]
        else:
            ids = [
                r.txn_id
                for r in rows
                if r.category == name and r.amount and (covenant.quarter is None or r.quarter == covenant.quarter)
            ]
        contributing[name] = sorted(ids)
    return {
        "clause_text": raw.get("definition_verbatim", ""),
        "source_correction": raw.get("correction"),
        "metric": covenant.metric,
        "operator": covenant.operator,
        "threshold": covenant.threshold,
        "test": f"{covenant.metric} {covenant.operator} {covenant.threshold}",
        "quarter": covenant.quarter,
        "trigger": covenant.trigger,
        "trigger_active": result.trigger_active,
        "inputs": {n: round(totals.get(n, 0.0), 2) for n in sorted(names)},
        "scalars_from_documents": {k: v for k, v in extra.items() if k in names},
        "contributing_rows": contributing,
        "adjustments_applied": [
            {k: a[k] for k in ("kind", "txn_id", "to_category", "amount", "source") if k in a}
            for a in spec.get("adjustments", [])
        ],
        "actual": round(result.actual, 2),
        "status": result.status,
        "evidence_txn_id": evidence,
        "evidence_basis": (
            "sole document-implicated row whose removal flips the verdict"
            if evidence and count == 1
            else f"{count} rows each flip the verdict on their own; the largest is named"
            if evidence
            else "no document-implicated row flips the verdict"
        ),
    }


def evidence_candidates(rows, spec: dict, related: set[str], unrestricted: set[str]) -> set[str]:
    """Rows a document singles out: auditor-adjusted, or inside an identity-restricted set."""
    ids = {adj["txn_id"] for adj in spec.get("adjustments", []) if "txn_id" in adj}
    related_norm = {normalise_name(n) for n in related}
    unrestricted_norm = {normalise_name(n) for n in unrestricted}
    for row in rows:
        name = normalise_name(row.counterparty)
        if name in related_norm:
            ids.add(row.txn_id)
        if row.category == "transfer_subsidiary" and name in unrestricted_norm:
            ids.add(row.txn_id)
    return ids


def main() -> None:
    parser = argparse.ArgumentParser(description="Build and score the covenant submission")
    # The extracted set, not the hand-transcribed one: cache/facts.json was written by a human
    # from this corpus and describes only it, so defaulting to it would answer a different
    # corpus from the wrong documents without failing.
    parser.add_argument("--facts", default=None, help="defaults to <corpus workdir>/facts_extracted.json")
    parser.add_argument("--out", default=None, help="defaults to submission.json for the published corpus")
    parser.add_argument("--team", default="")
    parser.add_argument("--email", default="")
    parser.add_argument("--model", default=None, help="defaults to the model that produced the facts")
    parser.add_argument("--no-score", action="store_true")
    add_data_argument(parser)
    add_level_argument(parser)
    args = parser.parse_args()

    data = Dataset(Path(args.data))
    data.check()
    facts_path = Path(args.facts) if args.facts else data.artefact("facts_extracted.json")
    out = Path(args.out) if args.out else data.submission
    data.workdir.mkdir(parents=True, exist_ok=True)
    facts = degrade(json.loads(facts_path.read_text()), args.level)
    template = json.loads(data.template.read_text())
    # The template is where the organisers ask for these, so it wins unless a flag overrides it.
    meta = {
        "team": args.team or template.get("team", ""),
        "contact_email": args.email or template.get("contact_email", ""),
        "model": args.model or facts.get("extraction_model", ""),
    }
    if not meta["team"] or not meta["contact_email"]:
        print("  warn: team or contact_email is blank in both the template and the flags")

    submission, warnings, derivations = build_submission(facts, template, meta, data)
    validate(submission, template)
    out.write_text(json.dumps(submission, indent=2, ensure_ascii=False) + "\n")
    trail = data.artefact("derivations.json")
    trail.write_text(json.dumps(derivations, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out}")
    print(f"wrote {trail}  (audit trail: one derivation per cell)")
    for warning in warnings:
        print(f"  warn: {warning}")

    gt_path = data.ground_truth
    if not args.no_score and gt_path.exists():
        # Local, so producing a submission never depends on the evaluation code -- on a corpus
        # with no answer key this import is never reached.
        from .score import report, score_submission

        print()
        print(report(score_submission(json.loads(gt_path.read_text()), submission)))


def validate(submission: dict, template: dict) -> None:
    if set(submission["answers"]) != set(template["answers"]):
        raise ValueError("scenario key set does not match the template")
    for scenario, cells in template["answers"].items():
        if set(submission["answers"][scenario]) != set(cells):
            raise ValueError(f"{scenario}: clause key set does not match the template")
        for clause, cell in submission["answers"][scenario].items():
            if set(cell) != {"status", "actual", "evidence_txn_id"}:
                raise ValueError(f"{scenario} {clause}: unexpected field set")
            if cell["status"] not in ("COMPLIANT", "BREACH", None):
                raise ValueError(f"{scenario} {clause}: bad status {cell['status']!r}")
            actual = cell["actual"]
            # CASE.ru: "Положительное число до 2 знаков после запятой" -- a non-numeric,
            # negative or over-precise value makes the cell unscoreable.
            if not isinstance(actual, (int, float)) or isinstance(actual, bool):
                raise ValueError(f"{scenario} {clause}: actual is not a number: {actual!r}")
            if not math.isfinite(actual):
                raise ValueError(f"{scenario} {clause}: actual is not finite: {actual!r}")
            if actual < 0:
                raise ValueError(f"{scenario} {clause}: actual must be positive, got {actual}")
            if round(actual, 2) != actual:
                raise ValueError(f"{scenario} {clause}: actual exceeds 2 decimal places: {actual}")


if __name__ == "__main__":
    main()
