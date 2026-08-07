from __future__ import annotations

import copy

# What each source of facts contributes, as a ladder. L1 reads nothing but the loan agreement
# and the ledger; each level adds one class of evidence. The point is not to ship L1 -- it is
# to know what every layer is worth, and to have somewhere to fall back to when a layer fails
# on a corpus we have not seen.
LEVELS: dict[str, str] = {
    "L1": "agreement + ledger only",
    "L2": "+ triggers, period scoping",
    "L3": "+ KYC related parties",
    "L4": "+ auditor adjustments",
    "L5": "+ document figures, FX",
    "L6": "+ source corrections (full)",
}

# Each capability switches on at a level and stays on above it.
_FROM = {
    "trigger": "L2",
    "quarter": "L2",
    "kyc": "L3",
    "adjustments": "L4",
    "figures": "L5",
    "fx": "L5",
    "corrections": "L6",
}


def enabled(capability: str, level: str) -> bool:
    return level >= _FROM[capability]


def degrade(facts: dict, level: str) -> dict:
    """Strip a facts file back to what the given level is allowed to know.

    Operates on the facts rather than on the pipeline, so the code under test is byte-identical
    at every level -- the only thing that changes is what it was told.
    """
    if level not in LEVELS:
        raise ValueError(f"unknown level {level!r}; expected one of {', '.join(LEVELS)}")
    if level == "L6":
        return facts

    out = copy.deepcopy(facts)
    if not enabled("fx", level):
        out["fx_rates"] = {}
    for spec in out["scenarios"].values():
        if not enabled("kyc", level):
            spec["related_parties"] = []
            spec["unrestricted_subsidiaries"] = []
        if not enabled("adjustments", level):
            spec["adjustments"] = []
        if not enabled("figures", level):
            spec["extra"] = {}
            spec["derived_figures"] = {}
        for covenant in spec["covenants"].values():
            if not enabled("trigger", level):
                covenant["trigger"] = None
            if not enabled("quarter", level):
                covenant["quarter"] = None
            # Undo any correction applied during extraction, so a level below L6 reads what
            # the document actually prints.
            correction = covenant.pop("correction", None) if not enabled("corrections", level) else None
            if correction is not None:
                covenant[correction["field"]] = correction["from"]
    return out


def add_level_argument(parser) -> None:
    parser.add_argument(
        "--level",
        default="L6",
        choices=list(LEVELS),
        help="how much evidence to use: " + "; ".join(f"{k} {v}" for k, v in LEVELS.items()),
    )
