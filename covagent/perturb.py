from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import shutil
import tempfile
import time
from pathlib import Path

from .dataset import Dataset, add_data_argument
from .run import build_submission
from .score import score_submission

ROOT = Path(__file__).resolve().parent.parent

# Perturbations of the ledger, which need no re-extraction and therefore no API calls. Each one
# breaks an assumption the pipeline might be leaning on without us realising. A perturbation that
# changes the score is telling you where the pipeline is brittle; one that changes nothing is
# telling you that assumption was not load-bearing.

SYNONYMS = {
    r"sales settlement": "counterparty remittance against issued invoice",
    r"operating expen\w*": "routine running outlay for the period",
    r"payroll|salar\w+|wage\w*": "staff remuneration disbursement",
    r"utilit\w+|electricity|water|heating": "site services consumption charge",
}


def read_ledger(path: Path) -> tuple[list[dict], list[str]]:
    with path.open() as fh:
        reader = csv.DictReader(fh)
        return list(reader), list(reader.fieldnames or [])


def write_ledger(rows: list[dict], fields: list[str], path: Path) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def perturb_vocabulary(rows: list[dict], scenario: str) -> str:
    """Rewrite one borrower's signal descriptions to wording no rule matches."""
    hit = 0
    for row in rows:
        if not row["txn_id"].startswith(f"TXN-{scenario}-"):
            continue
        for pattern, replacement in SYNONYMS.items():
            if re.search(pattern, row["description"], re.IGNORECASE):
                row["description"] = replacement
                hit += 1
                break
    return f"{scenario}: {hit} descriptions rewritten to synonyms no rule matches"


def perturb_currency(rows: list[dict], scenario: str) -> str:
    """Re-denominate one borrower's rows into a currency no document discloses a rate for."""
    hit = 0
    for row in rows:
        if row["txn_id"].startswith(f"TXN-{scenario}-") and row["currency"] == "USD":
            row["currency"] = "CHF"
            hit += 1
    return f"{scenario}: {hit} rows re-denominated to CHF, which no document discloses"


def perturb_order(rows: list[dict], _scenario: str) -> str:
    """Shuffle the ledger. Nothing should depend on row order."""
    random.Random(0).shuffle(rows)
    return f"all {len(rows)} rows shuffled"


def perturb_scale(rows: list[dict], _scenario: str) -> str:
    """Ten times the decoy volume, to see what the pipeline costs as the book grows."""
    decoys = [r for r in rows if r["account_id"].startswith("ACC-9")]
    added = []
    for copy_index in range(1, 10):
        for row in decoys:
            clone = dict(row)
            clone["txn_id"] = f"{row['txn_id']}-D{copy_index}"
            clone["account_id"] = f"ACC-9{copy_index}{row['account_id'][5:]}"
            added.append(clone)
    rows.extend(added)
    return f"{len(added)} decoy rows added; ledger is now {len(rows)} rows"


PERTURBATIONS = {
    "vocabulary": perturb_vocabulary,
    "currency": perturb_currency,
    "order": perturb_order,
    "scale": perturb_scale,
}


def evaluate(facts: dict, template: dict, gt: dict | None, data: Dataset) -> tuple[float, int, int]:
    meta = {"team": "perturb", "contact_email": "", "model": ""}
    start = time.monotonic()
    submission, warnings, _ = build_submission(facts, template, meta, data)
    elapsed = time.monotonic() - start
    if gt is None:
        return -1.0, len(warnings), elapsed
    cells = score_submission(gt, submission)
    return sum(c.total for c in cells), len(warnings), elapsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Perturb the ledger and see what moves. No re-extraction, so no API calls."
    )
    parser.add_argument("--facts", default=str(ROOT / "cache" / "facts_extracted.json"))
    parser.add_argument(
        "--scenario",
        default=None,
        help="borrower to perturb where the test targets one; defaults to the first in the template",
    )
    parser.add_argument("--only", choices=list(PERTURBATIONS), help="run a single perturbation")
    add_data_argument(parser)
    args = parser.parse_args()

    source = Dataset(Path(args.data))
    source.check()
    template = json.loads(source.template.read_text())
    gt = json.loads(source.ground_truth.read_text()) if source.ground_truth.exists() else None
    facts = json.loads(Path(args.facts).read_text())

    # Defaulting to a named borrower would silently no-op on a corpus that has no such name,
    # and a perturbation that changes nothing reads as a passing test.
    scenario = args.scenario or min(template["answers"])
    base_score, base_warnings, base_time = evaluate(facts, template, gt, source)
    shown = f"{base_score:.2f}" if gt else "n/a"
    print(f"baseline: score {shown}, {base_warnings} warnings, {base_time:.2f}s\n")
    print(f"{'perturbation':<14}{'score':>8}{'delta':>8}{'warn':>7}{'time':>8}   what changed")
    print("-" * 108)

    work = Path(tempfile.mkdtemp(prefix="covagent-perturb-"))
    try:
        for name, apply in PERTURBATIONS.items():
            if args.only and name != args.only:
                continue
            rows, fields = read_ledger(source.ledger)
            note = apply(rows, scenario)

            corpus = work / name
            corpus.mkdir()
            os.symlink(source.documents, corpus / "documents")
            shutil.copy(source.template, corpus / "submission_template.json")
            write_ledger(rows, fields, corpus / "ledger.csv")

            score, warnings, elapsed = evaluate(facts, template, gt, Dataset(corpus))
            delta = f"{score - base_score:+.2f}" if gt else "n/a"
            shown = f"{score:.2f}" if gt else "n/a"
            print(f"{name:<14}{shown:>8}{delta:>8}{warnings:>7}{elapsed:>7.2f}s   {note}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print()
    print("A perturbation that moves the score found something the pipeline was leaning on.")
    print("One that moves nothing and raises no warning was silently absorbed -- check that is right.")
    print()
    print(
        "These re-run stages 2-4 only. `vocabulary` therefore tests the deterministic rules "
        "without\nthe model classifier that would normally rescue them -- for that, re-run "
        "build_facts against\nthe perturbed corpus. `currency` leaves the score flat because "
        "an unconverted row is counted at\nface value: the number is wrong but the warning "
        "says so, which is the documented behaviour."
    )


if __name__ == "__main__":
    main()
