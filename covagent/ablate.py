from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from .dataset import Dataset, add_data_argument
from .levels import LEVELS, degrade
from .run import build_submission
from .score import score_submission

ROOT = Path(__file__).resolve().parent.parent

# One knob per class of evidence. Removing each in turn prices it: the drop is what that part
# of the pipeline is worth on this corpus. A part worth 0.00 is not automatically dead -- it may
# be defending against a case this corpus does not contain -- but it is worth knowing which.
KNOBS = {
    "adjustments": "auditor adjustments",
    "related": "related parties (KYC)",
    "unrestricted": "unrestricted subsidiaries",
    "figures": "figures stated in documents",
    "fx": "exchange rates",
    "trigger": "springing triggers",
    "quarter": "quarter scoping",
    "corrections": "owner source corrections",
}


def strip(facts: dict, knobs: set[str]) -> dict:
    out = copy.deepcopy(facts)
    if "fx" in knobs:
        out["fx_rates"] = {}
    for spec in out["scenarios"].values():
        if "adjustments" in knobs:
            spec["adjustments"] = []
        if "related" in knobs:
            spec["related_parties"] = []
        if "unrestricted" in knobs:
            spec["unrestricted_subsidiaries"] = []
        if "figures" in knobs:
            spec["extra"] = {}
            spec["derived_figures"] = {}
        for covenant in spec["covenants"].values():
            if "trigger" in knobs:
                covenant["trigger"] = None
            if "quarter" in knobs:
                covenant["quarter"] = None
            if "corrections" in knobs and (c := covenant.pop("correction", None)) is not None:
                covenant[c["field"]] = c["from"]
    return out


def measure(facts: dict, template: dict, gt: dict, data: Dataset) -> tuple[float, int]:
    meta = {"team": "ablation", "contact_email": "", "model": ""}
    submission, _, _ = build_submission(facts, template, meta, data)
    cells = score_submission(gt, submission)
    return sum(c.total for c in cells), sum(1 for c in cells if c.status_ok)


def main() -> None:
    parser = argparse.ArgumentParser(description="Price each part of the pipeline against the key")
    parser.add_argument("--facts", default=str(ROOT / "cache" / "facts_extracted.json"))
    add_data_argument(parser)
    args = parser.parse_args()

    data = Dataset(Path(args.data))
    data.check()
    if not data.ground_truth.exists():
        raise SystemExit(f"{data.ground_truth} is missing; ablation needs an answer key")
    template = json.loads(data.template.read_text())
    gt = json.loads(data.ground_truth.read_text())
    facts = json.loads(Path(args.facts).read_text())
    total = len(gt["scenarios"]) * len(next(iter(gt["scenarios"].values()))["covenants"])

    base, base_ok = measure(facts, template, gt, data)
    print(f"{'removed':<34}{'score':>9}{'':>3}{'status':>9}{'cost':>9}")
    print("-" * 64)
    print(f"{'nothing (full pipeline)':<34}{base:>9.2f}{'':>3}{base_ok:>6}/{total:<2}{'--':>9}")
    priced = []
    for knob, label in KNOBS.items():
        score, ok = measure(strip(facts, {knob}), template, gt, data)
        priced.append((base - score, label, score, ok))
    for cost, label, score, ok in sorted(priced, reverse=True):
        flag = "   <- earns nothing here" if cost == 0 else ""
        print(f"{label:<34}{score:>9.2f}{'':>3}{ok:>6}/{total:<2}{-cost:>9.2f}{flag}")

    print()
    print(f"{'level':<34}{'score':>9}{'':>3}{'status':>9}{'gain':>9}")
    print("-" * 64)
    previous = 0.0
    for level, described in LEVELS.items():
        score, ok = measure(degrade(facts, level), template, gt, data)
        print(f"{level + '  ' + described:<34}{score:>9.2f}{'':>3}{ok:>6}/{total:<2}{score - previous:>+9.2f}")
        previous = score

    print()
    print(
        f"Individual costs sum to {sum(c for c, _, _, _ in priced):.2f} but the full pipeline is worth "
        f"{base - measure(degrade(facts, 'L1'), template, gt, data)[0]:.2f} over L1: the parts mask "
        "each other, so a cell already lost for one reason cannot be lost again for another."
    )


if __name__ == "__main__":
    main()
