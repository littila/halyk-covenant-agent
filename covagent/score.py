from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CellScore:
    scenario: str
    clause: str
    status_ok: bool
    actual_error: float | None
    actual_points: float
    evidence_points: float
    total: float
    key_status: str
    got_status: str | None
    key_actual: float | None
    got_actual: float | None
    key_evidence: str | None
    got_evidence: str | None


def decay(relative_error: float | None) -> float:
    """CASE.ru scale: full marks at 0 error, linear to zero at 5% relative error."""
    if relative_error is None:
        return 0.0
    return max(0.0, 1.0 - relative_error / 0.05)


def score_cell(scenario: str, clause: str, key: dict, got: dict | None) -> CellScore:
    got = got or {}
    key_status, got_status = key.get("status"), got.get("status")
    key_actual, got_actual = key.get("actual"), got.get("actual")
    key_evidence, got_evidence = key.get("evidence_txn_id"), got.get("evidence_txn_id")

    status_ok = got_status == key_status

    error: float | None = None
    if isinstance(got_actual, (int, float)) and isinstance(key_actual, (int, float)) and key_actual != 0:
        error = abs(got_actual - key_actual) / abs(key_actual)
    fraction = decay(error)

    actual_points = 0.30 * fraction
    if key_evidence is None:
        evidence_points = 0.20 * fraction
    else:
        evidence_points = 0.20 if got_evidence == key_evidence else 0.0

    total = (0.50 + actual_points + evidence_points) if status_ok else 0.0
    return CellScore(
        scenario,
        clause,
        status_ok,
        error,
        actual_points,
        evidence_points,
        total,
        key_status,
        got_status,
        key_actual,
        got_actual,
        key_evidence,
        got_evidence,
    )


def score_submission(ground_truth: dict, submission: dict) -> list[CellScore]:
    answers = submission.get("answers", {})
    cells = []
    for scenario, payload in sorted(ground_truth["scenarios"].items()):
        for clause, key in sorted(payload["covenants"].items()):
            cells.append(score_cell(scenario, clause, key, answers.get(scenario, {}).get(clause)))
    return cells


def report(cells: list[CellScore]) -> str:
    lines = []
    total = sum(c.total for c in cells)
    status_right = sum(1 for c in cells if c.status_ok)
    exact_actual = sum(1 for c in cells if c.actual_error is not None and c.actual_error < 1e-9)
    evidence_needed = [c for c in cells if c.key_evidence is not None]
    evidence_right = sum(1 for c in evidence_needed if c.got_evidence == c.key_evidence)

    lines.append(f"score          {total:.4f} / {len(cells)}  ({total / len(cells) * 100:.2f}%)")
    lines.append(f"status         {status_right}/{len(cells)}")
    lines.append(f"actual exact   {exact_actual}/{len(cells)}")
    lines.append(f"evidence       {evidence_right}/{len(evidence_needed)} (cells with a non-null key)")
    lines.append("")
    header = f"{'cell':9} {'status':22} {'actual (got vs key)':44} {'evidence':28} pts"
    lines.append(header)
    lines.append("-" * len(header))
    for c in sorted(cells, key=lambda x: (x.total, x.scenario, x.clause)):
        mark = "ok " if c.status_ok else "XX "
        status = f"{mark}{c.got_status!s:9}->{c.key_status!s:9}"
        if c.actual_error is None:
            actual = f"{c.got_actual!s:>18} vs {c.key_actual:>18,.2f}   n/a"
        else:
            actual = f"{c.got_actual:>18,.2f} vs {c.key_actual:>18,.2f} {c.actual_error * 100:5.2f}%"
        if c.key_evidence is None:
            evidence = f"{'(null key)':28}"
        else:
            tick = "ok" if c.got_evidence == c.key_evidence else "XX"
            evidence = f"{tick} {c.got_evidence!s:>13}->{c.key_evidence:>13}"[:28]
        lines.append(f"{c.scenario:4} {c.clause:4} {status:22} {actual:44} {evidence:28} {c.total:.3f}")
    return "\n".join(lines)
