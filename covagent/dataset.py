from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "agentic-bank-public"


@dataclass(frozen=True)
class Dataset:
    """A corpus to run against: documents, one ledger, and the answer template.

    The ledger is found by pattern rather than by name so a differently-dated file still
    resolves. ground_truth.json is optional -- a scoring key exists for the published data
    and will not for a held-out one.
    """

    root: Path

    @property
    def documents(self) -> Path:
        return self.root / "documents"

    @property
    def ledger(self) -> Path:
        found = sorted(self.root.glob("*ledger*.csv"))
        if not found:
            raise FileNotFoundError(f"no *ledger*.csv in {self.root}")
        if len(found) > 1:
            raise ValueError(f"{self.root} holds several ledgers: {[p.name for p in found]}")
        return found[0]

    @property
    def template(self) -> Path:
        return self.root / "submission_template.json"

    @property
    def ground_truth(self) -> Path:
        return self.root / "ground_truth.json"

    def check(self) -> None:
        for path in (self.documents, self.template):
            if not path.exists():
                raise FileNotFoundError(f"{path} is missing")
        _ = self.ledger  # raises here, rather than midway through a run


def add_data_argument(parser) -> None:
    parser.add_argument(
        "--data",
        default=str(DEFAULT),
        help="dataset folder holding documents/, a *ledger*.csv and submission_template.json",
    )
