from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.capabilities import Thinking

from .ledger import CATEGORIES, Txn, stem_of

# Derived from the single source in ledger.py so the vocabulary cannot drift.
CategoryName = Literal[CATEGORIES]  # type: ignore[valid-type]


class StemAssignment(BaseModel):
    stem: str = Field(description="The description stem, copied back verbatim")
    category: CategoryName
    reasoning: str = Field(description="One short clause justifying the category")


class StemTaxonomy(BaseModel):
    assignments: list[StemAssignment]


INSTRUCTIONS = f"""You categorise transaction description stems from a corporate loan ledger so
they can be aggregated for covenant testing.

Assign each stem to exactly one category from: {", ".join(CATEGORIES)}

Rules:
- Categorise on what the description says the money was for. Ignore any company name.
- "revenue" is only for the borrower's own operating sales income, not refunds or credits.
- A credit that reverses an expense ("rebate", "refund", "returned") keeps the category of the
  expense family it reverses -- do NOT call it revenue.
- "opex" is general operating and maintenance cost of running the asset.
- "other_expense" is the honest answer when nothing else fits. Prefer it over a bad guess:
  a wrong category silently corrupts an aggregate, an "other_expense" one merely omits it.
- A payroll-named counterparty does not make a tax payment payroll, and vice versa."""


def classify_stems(stems: list[str], model: str, cache_path: Path, batch: int = 60) -> dict[str, str]:
    """Assign a category to each stem the deterministic rules did not match.

    The result is written to a reviewable JSON file. Stems already present are not re-sent,
    so a hand-correction survives the next run.
    """
    learned: dict[str, str] = {}
    if cache_path.exists():
        learned = json.loads(cache_path.read_text())
    pending = sorted({s for s in stems if s and s not in learned})
    if not pending:
        return learned

    agent = Agent(
        model,
        output_type=StemTaxonomy,
        instructions=INSTRUCTIONS,
        capabilities=[Thinking(effort="low")],
        retries=2,
    )
    for start in range(0, len(pending), batch):
        chunk = pending[start : start + batch]
        result = agent.run_sync("Categorise each of these stems:\n" + "\n".join(f"- {s}" for s in chunk))
        for item in result.output.assignments:
            key = item.stem.strip().lower()
            if key in {s.lower() for s in chunk}:
                learned[key] = item.category
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(learned, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return learned


def unmatched_stems(txns: list[Txn]) -> list[str]:
    return sorted({stem_of(t.description) for t in txns if t.category == "uncategorised"})
