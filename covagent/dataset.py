from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "agentic-bank-public"
CACHE = ROOT / "cache"


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

    @property
    def workdir(self) -> Path:
        """Where this corpus's derived artefacts live: caches, facts, derivations, provenance.

        The published corpus keeps the top-level cache/, where its committed facts and
        provenance already sit and where the README points. Every other corpus gets its own
        subdirectory, so a run against a held-out set can neither overwrite nor read what a
        run against a different one left behind -- the reason to keep them apart is not disk
        space but that a stale artefact from another corpus is indistinguishable from a fresh
        one, and would be believed.
        """
        root = self.root.resolve()
        return CACHE if root == DEFAULT.resolve() else CACHE / root.name

    def artefact(self, name: str) -> Path:
        return self.workdir / name

    @property
    def submission(self) -> Path:
        """Where a run against this corpus writes its answer.

        The published corpus writes the submission.json at the repository root, which is the
        file actually submitted. A held-out corpus keeps its answer beside its own artefacts,
        so a rehearsal cannot overwrite the answer being submitted.
        """
        return ROOT / "submission.json" if self.workdir == CACHE else self.artefact("submission.json")

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
