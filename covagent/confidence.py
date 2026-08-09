from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from .dataset import Dataset, add_data_argument
from .levels import add_level_argument, degrade
from .run import build_submission

ROOT = Path(__file__).resolve().parent.parent

# On a corpus with an answer key the score tells you whether you were right. On one without,
# nothing does -- so the only honest substitute is a list of the things that would have to be
# true for the answer to be right, each checked. These are ordered by how badly a failure hurts.
SEVERE = "must fix"
WATCH = "look at"
NOTE = "for information"


def check_routing(warnings: list[str]) -> list[tuple[str, str]]:
    out = []
    for w in warnings:
        if "no extracted facts" in w or "no covenant extracted" in w or "no ledger rows at all" in w:
            out.append((SEVERE, w))
    return out


def check_cells(submission: dict, derivations: dict) -> list[tuple[str, str]]:
    """Per-cell checks that need no key: did we answer, and is the answer well formed."""
    out = []
    for scenario, cells in sorted(submission["answers"].items()):
        for clause, cell in sorted(cells.items()):
            where = f"{scenario} {clause}"
            if cell["status"] is None or cell["actual"] is None:
                out.append((SEVERE, f"{where}: unanswered -- an empty cell scores what a wrong one does"))
                continue
            if cell["actual"] <= 0:
                out.append((SEVERE, f"{where}: actual is {cell['actual']}, but it must be positive"))
            d = derivations.get(scenario, {}).get(clause, {})
            empty = [n for n, ids in (d.get("contributing_rows") or {}).items() if not ids]
            if empty:
                out.append((WATCH, f"{where}: {', '.join(empty)} is referenced but no ledger row fills it"))
    return out


def check_distribution(submission: dict, derivations: dict) -> list[tuple[str, str]]:
    """Two shape checks that per-cell tests cannot see.

    A run where every verdict agrees, or where nothing sits anywhere near its limit, is more
    likely to be a pipeline that under-filled its aggregates than a book of healthy borrowers.
    """
    out = []
    statuses = [c["status"] for cells in submission["answers"].values() for c in cells.values()]
    counts = collections.Counter(statuses)
    total = len(statuses)
    if len(counts) == 1 and total > 1:
        out.append((SEVERE, f"all {total} cells came out {statuses[0]} -- check the aggregates are being filled"))
    elif counts and max(counts.values()) / total > 0.85:
        common, n = counts.most_common(1)[0]
        out.append((WATCH, f"{n} of {total} cells are {common}; a real book is usually more mixed"))

    margins = []
    for cells in derivations.values():
        for d in cells.values():
            threshold, actual = d.get("threshold"), d.get("actual")
            if threshold:
                margins.append(abs(actual - threshold) / abs(threshold))
    if margins:
        tight = sum(1 for m in margins if m < 0.05)
        if tight == 0:
            out.append(
                (WATCH, f"no cell is within 5% of its limit across {len(margins)}; aggregates may be under-filled")
            )
        else:
            out.append((NOTE, f"{tight} of {len(margins)} cells sit within 5% of their limit"))
    return out


def check_evidence(submission: dict, derivations: dict) -> list[tuple[str, str]]:
    out = []
    named = sum(1 for cells in submission["answers"].values() for c in cells.values() if c["evidence_txn_id"])
    out.append((NOTE, f"{named} cells name a determining transaction"))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="What would have to be true for this answer to be right, checked. Needs no answer key."
    )
    parser.add_argument("--facts", default=None, help="defaults to <corpus workdir>/facts_extracted.json")
    parser.add_argument("--extraction-log", default=None, help="defaults to <corpus workdir>/extraction_warnings.json")
    add_data_argument(parser)
    add_level_argument(parser)
    args = parser.parse_args()

    data = Dataset(Path(args.data))
    data.check()
    template = json.loads(data.template.read_text())
    facts_path = Path(args.facts) if args.facts else data.artefact("facts_extracted.json")
    facts = degrade(json.loads(facts_path.read_text()), args.level)
    meta = {"team": "", "contact_email": "", "model": facts.get("extraction_model", "")}
    submission, warnings, derivations = build_submission(facts, template, meta, data)

    findings = check_routing(warnings)
    findings += check_cells(submission, derivations)
    findings += check_distribution(submission, derivations)
    findings += check_evidence(submission, derivations)
    for w in warnings:
        if "answered leniently" in w or "flip the verdict" in w:
            findings.append((SEVERE, w))
        elif "uncategorised" in w or "no exchange rate" in w or "contributed 0.00" in w:
            findings.append((WATCH, w))

    # Whatever the extraction stage reported about its own reading is part of the picture, and
    # is otherwise printed by a different command and lost.
    log = Path(args.extraction_log) if args.extraction_log else data.artefact("extraction_warnings.json")
    if log.exists():
        for w in json.loads(log.read_text()):
            # A quantity a covenant reads and no document states is the one extraction warning
            # that cannot be worked around downstream: the cell is a guess until it is resolved.
            unresolved = "stated in no document" in w or "no readable document to search" in w
            if unresolved or ("could not" in w and "scanned" not in w):
                level = SEVERE
            elif "recovered on demand" in w:
                level = WATCH
            else:
                level = NOTE
            findings.append((level, f"extraction: {w}"))
    else:
        findings.append((NOTE, f"no extraction log at {log.name}; run build_facts to record one"))

    print(f"confidence report  ·  level {args.level}  ·  {data.root}")
    print("=" * 96)
    for level in (SEVERE, WATCH, NOTE):
        group = [m for lv, m in findings if lv == level]
        if not group:
            continue
        print(f"\n{level.upper()}  ({len(group)})")
        for m in group:
            print(f"  {m if len(m) < 150 else m[:147] + '...'}")

    severe = sum(1 for lv, _ in findings if lv == SEVERE)
    print()
    print("=" * 96)
    print(f"{severe} blocking, {sum(1 for lv, _ in findings if lv == WATCH)} to look at.", end=" ")
    print("Nothing blocking." if not severe else "Do not submit without reading the blocking list.")


if __name__ == "__main__":
    main()
