from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import Thinking
from pydantic_ai_harness import CodeMode

from .ledger import Txn, normalise_name


@dataclass
class LedgerView:
    """The one borrower's rows an adjustment may legitimately point at."""

    scenario: str
    rows: list[Txn]


class Resolution(BaseModel):
    txn_id: str | None = Field(
        description="The single ledger row this adjustment refers to, or null if not identifiable"
    )
    reasoning: str = Field(description="Which evidence identified the row, in one sentence")


INSTRUCTIONS = """You match an auditor's adjustment to the single ledger row it refers to.

You have deterministic lookup tools over one borrower's ledger. Use `run_code` to combine them:
search by amount, by counterparty, by description keyword, and intersect the results.

The note may identify the row by transaction id, by an exact amount, by a counterparty name
spelled differently from the ledger, by a description, or by a combination. Amounts in the note
are positive; ledger expenses are negative, so compare magnitudes.

Return a txn_id only when exactly one row fits all the evidence. If several rows fit equally, or
none does, return null -- a wrong match silently corrupts a covenant, a null one is reported."""


def build_agent(model: str) -> Agent[LedgerView, Resolution]:
    agent = Agent(
        model,
        deps_type=LedgerView,
        output_type=Resolution,
        instructions=INSTRUCTIONS,
        capabilities=[Thinking(effort="low"), CodeMode()],
        retries=2,
    )

    @agent.tool
    def find_by_amount(ctx: RunContext[LedgerView], amount: float, tolerance: float = 0.01) -> list[dict]:
        """Ledger rows whose magnitude matches `amount` within `tolerance`."""
        target = abs(amount)
        return [
            _row(t)
            for t in ctx.deps.rows
            if t.amount is not None and abs(abs(t.amount) - target) <= tolerance
        ]

    @agent.tool
    def find_by_counterparty(ctx: RunContext[LedgerView], name: str) -> list[dict]:
        """Ledger rows whose counterparty matches `name`, ignoring legal-form and punctuation."""
        key = normalise_name(name)
        return [_row(t) for t in ctx.deps.rows if key and key in normalise_name(t.counterparty)]

    @agent.tool
    def search_descriptions(ctx: RunContext[LedgerView], substring: str) -> list[dict]:
        """Ledger rows whose description contains `substring`, case-insensitively."""
        needle = substring.lower()
        return [_row(t) for t in ctx.deps.rows if needle in t.description.lower()]

    @agent.tool
    def get_row(ctx: RunContext[LedgerView], txn_id: str) -> dict | None:
        """One ledger row by transaction id."""
        return next((_row(t) for t in ctx.deps.rows if t.txn_id == txn_id), None)

    return agent


def _row(t: Txn) -> dict:
    return {
        "txn_id": t.txn_id,
        "date": t.date,
        "counterparty": t.counterparty,
        "description": t.description,
        "amount": t.amount,
        "currency": t.currency,
        "category": t.category,
    }


_AGENTS: dict[str, Agent] = {}


def resolve(adjustment: dict, rows: list[Txn], scenario: str, model: str) -> tuple[str | None, str]:
    """Last-resort match for an adjustment the deterministic rule could not place.

    The deterministic path stays primary; this only runs on the residue, where the note
    identifies its row in prose rather than by id or a clean amount + counterparty pair.
    """
    if model not in _AGENTS:
        _AGENTS[model] = build_agent(model)
    described = ", ".join(f"{k}={v!r}" for k, v in adjustment.items() if v not in (None, ""))
    result = _AGENTS[model].run_sync(
        f"Adjustment recorded by the auditor for borrower {scenario}: {described}\n\n"
        "Identify the single ledger row it refers to.",
        deps=LedgerView(scenario=scenario, rows=rows),
    )
    return result.output.txn_id, result.output.reasoning
