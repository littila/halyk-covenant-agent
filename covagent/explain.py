from __future__ import annotations

import argparse
import json
from pathlib import Path

from .dataset import Dataset, add_data_argument, scenario_order

ROOT = Path(__file__).resolve().parent.parent


def money(value: float) -> str:
    return f"{value:,.2f}"


def explain_cell(scenario: str, clause: str, d: dict, key: dict | None, sources: dict) -> list[str]:
    """One cell, from clause text through inputs and rows to the verdict."""
    out = [f"{scenario} {clause}   {d['status']}   actual {money(d['actual'])}"]
    if key:
        agree = d["status"] == key["status"] and abs(d["actual"] - key["actual"]) < 0.005
        out[0] += f"      key: {key['status']} {money(key['actual'])}  {'match' if agree else 'MISMATCH'}"

    out.append(f"    test        {d['test']}" + (f"   (quarter {d['quarter']})" if d.get("quarter") else ""))
    if d.get("trigger"):
        t = d["trigger"]
        state = "active" if d.get("trigger_active") else "not met, so compliant by default"
        out.append(f"    trigger     {t['metric']} {t['operator']} {money(float(t['value']))} -- {state}")
    if d.get("source_correction"):
        c = d["source_correction"]
        out.append(f"    corrected   {c['field']} {c['from']} -> {c['to']}, on the authority of {c['authority']}")

    for name in sorted(d["inputs"]):
        value = d["inputs"][name]
        rows = d["contributing_rows"].get(name)
        if rows:
            shown = ", ".join(rows[:4]) + (f" +{len(rows) - 4} more" if len(rows) > 4 else "")
            out.append(f"    {name:<22} {money(value):>18}   from {shown}")
        elif name in d.get("scalars_from_documents", {}):
            out.append(f"    {name:<22} {money(value):>18}   from a document, not the ledger")
        else:
            out.append(f"    {name:<22} {money(value):>18}   (no contributing row)")

    for adj in d.get("adjustments_applied", []):
        detail = adj.get("to_category") or (money(adj["amount"]) if adj.get("amount") else "")
        out.append(f"    adjustment  {adj['kind']} {adj.get('txn_id')} {detail}".rstrip())
        if adj.get("source"):
            out.append(f"                {adj['source'][:104]}")

    out.append(f"    evidence    {d['evidence_txn_id'] or 'none'} -- {d['evidence_basis']}")
    clause_text = " ".join((d.get("clause_text") or "").split())
    if clause_text:
        out.append(f'    clause      "{clause_text[:150]}..."')
    for label, doc in sources.items():
        out.append(f"    {label:<11} {doc}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Show the provenance behind every answer")
    parser.add_argument("--derivations", default=None, help="defaults to <corpus workdir>/derivations.json")
    parser.add_argument("--facts", default=None, help="defaults to <corpus workdir>/facts_extracted.json")
    parser.add_argument("--cell", help="limit to one cell, e.g. <scenario>:<clause>")
    add_data_argument(parser)
    args = parser.parse_args()

    data = Dataset(Path(args.data))

    derivations = json.loads((Path(args.derivations) if args.derivations else data.artefact("derivations.json")).read_text())
    facts = json.loads((Path(args.facts) if args.facts else data.artefact("facts_extracted.json")).read_text())
    gt_path = data.ground_truth
    gt = json.loads(gt_path.read_text())["scenarios"] if gt_path.exists() else {}

    from .build_facts import account_map, resolve_unrouted
    from .documents import load_documents, route

    accounts = account_map(data)
    docs = load_documents(data.documents, data.artefact("text"), accounts)
    # Same second look the build does, so a document attached by reading it -- a scan, or a
    # group report that prints no account -- is listed as a source rather than silently absent.
    resolve_unrouted(docs, facts.get("extraction_model") or "anthropic:claude-opus-5")
    routing = route(docs, accounts)

    matched = 0
    total = 0
    for scenario in sorted(derivations, key=scenario_order):
        docs = {
            f"{kind}:": ", ".join(f"{d.doc_id}.pdf" for d in ds)
            for kind, ds in sorted(routing.get(scenario, {}).items())
        }
        for clause in sorted(derivations[scenario]):
            if args.cell and args.cell != f"{scenario}:{clause}":
                continue
            key = gt.get(scenario, {}).get("covenants", {}).get(clause)
            d = derivations[scenario][clause]
            total += 1
            if key and d["status"] == key["status"] and abs(d["actual"] - key["actual"]) < 0.005:
                matched += 1
            print("\n".join(explain_cell(scenario, clause, d, key, docs)))
            print()
    if gt and not args.cell:
        print(f"{matched}/{total} cells match the key on status and actual")
    print(f"model: {facts.get('extraction_model')}   fx: {facts.get('fx_rates')}")


if __name__ == "__main__":
    main()
