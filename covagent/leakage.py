from __future__ import annotations

import argparse
import ast
import csv
import json
import re
from pathlib import Path

from .dataset import Dataset, add_data_argument

ROOT = Path(__file__).resolve().parent.parent

# Modules whose whole job is the published instance: the scorer, the tools that price the
# pipeline against a key, and this check itself. Everything else must run on a corpus it has
# never seen, and a scenario id or transaction id baked into it is evidence that it will not.
EVALUATION_ONLY = {"score.py", "ablate.py", "perturb.py", "leakage.py"}


def public_tokens(data: Dataset) -> set[str]:
    rows = list(csv.DictReader(data.ledger.open()))
    return (
        set(json.loads(data.template.read_text())["answers"])
        | {r["account_id"] for r in rows}
        | {r["txn_id"] for r in rows}
        | {p.stem for p in data.documents.iterdir()}
    )


# Filenames that exist on every Windows share, not facts about this corpus.
UNIVERSAL = {"Thumbs", "Thumbs.db", ".DS_Store"}


def violations(source: Path, tokens: set[str]) -> list[str]:
    tokens = tokens - UNIVERSAL
    patterns = [
        (t, re.compile(rf"(?<![A-Za-z0-9-]){re.escape(t)}(?![A-Za-z0-9-])"))
        for t in sorted(tokens, key=len, reverse=True)
    ]
    found = []
    for path in sorted(source.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for token, pattern in patterns:
                    if pattern.search(node.value):
                        found.append(f"{path}:{node.lineno}: literal {token!r} from the published data")
                        break
        # Only a module-level import is a dependency. Pulling the scorer in inside an
        # `if a key exists` branch is precisely how the dependency is avoided.
        if path.name not in EVALUATION_ONLY and imports_scorer(tree.body):
            found.append(f"{path}: imports the scorer at module level, so a keyless run depends on it")
    return found


def imports_scorer(body: list[ast.stmt]) -> bool:
    for node in body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("score"):
            return True
        if isinstance(node, ast.Import) and any(a.name.endswith("score") for a in node.names):
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assert no production module is written against the published instance"
    )
    parser.add_argument("--source", default=str(ROOT / "covagent"))
    add_data_argument(parser)
    args = parser.parse_args()

    data = Dataset(Path(args.data))
    data.check()
    found = violations(Path(args.source), public_tokens(data))
    for line in found:
        print(f"  {line}")
    print(f"{len(found)} public-instance literal(s) in {args.source}")
    if found:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
