from __future__ import annotations

import argparse
import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .dataset import Dataset, add_data_argument
from .documents import load_documents, route, routing_problems
from .evaluate import referenced_names
from .extract import (
    body_for,
    extract,
    image_pages,
    prune_cache,
    retrieve_figures,
    triage_document,
    use_workdir,
)
from .ledger import (
    CATEGORIES,
    COMPUTED,
    DEBT_PARTS,
    DERIVED,
    EBIT_NAMES,
    load_learned,
    load_ledger,
    normalise_name,
)
from .reconcile import resolve as reconcile_adjustment
from .taxonomy import classify_stems, unmatched_stems

ROOT = Path(__file__).resolve().parent.parent

# All read by the same extractor: each may carry adjustments, scalars, figures or a rate.
AUDIT_KINDS = ("audit_notes", "aup_report", "treasury_memo", "financial_statements")
MONEY = r"[\d][\d,]*\.\d\d"


def account_map(data: Dataset) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in csv.DictReader(data.ledger.open()):
        scenario = row["txn_id"].split("-")[1]
        seen = mapping.setdefault(row["account_id"], scenario)
        if seen != scenario:
            raise ValueError(f"{row['account_id']} maps to both {seen} and {scenario}")
    return mapping


def rates_from_disclosures(disclosures: list[dict]) -> tuple[dict[str, float], list[str]]:
    """Turn transcribed disclosures into rates. The division happens here, not in the model.

    A document may quote a rate outright or show an invoice settled in USD; both forms are
    accepted, and the model is asked only to copy the figures across.
    """
    rates: dict[str, float] = {}
    notes: list[str] = []
    for d in disclosures:
        currency = (d.get("currency") or "").strip().upper()
        if not currency or currency == "USD":
            continue
        rate = d.get("rate")
        if rate is None:
            foreign, usd = d.get("foreign_amount"), d.get("usd_amount")
            if not foreign or usd is None:
                continue
            rate = usd / foreign
        rate = round(float(rate), 6)
        if currency in rates and abs(rates[currency] - rate) > 1e-6:
            notes.append(f"conflicting {currency} rates disclosed: {rates[currency]} and {rate}; keeping the first")
            continue
        rates.setdefault(currency, rate)
        notes.append(f"{currency} rate {rate} from: {(d.get('basis') or '').strip()[:110]}")
    return rates, notes


def derive_fx(notes: list[str]) -> dict[str, float]:
    """Last-resort scan of raw text for a settlement pair, if nothing was transcribed."""
    rates: dict[str, float] = {}
    for note in notes:
        if not note:
            continue
        match = re.search(rf"({MONEY})\s*([A-Z]{{3}})\b.*?\$({MONEY})", note, re.DOTALL)
        if not match or match.group(2) == "USD":
            continue
        foreign = float(match.group(1).replace(",", ""))
        usd = float(match.group(3).replace(",", ""))
        if foreign > 0:
            rates[match.group(2)] = round(usd / foreign, 6)
    return rates


def resolve_figures(figures: list[dict]) -> list[tuple[str, float, str, bool]]:
    """Literal figures first, then any the document derives from them.

    The model says how a derived quantity follows from printed ones; the expression is
    evaluated here with the same whitelisted walker the covenant formulas use, so no
    arithmetic happens inside the model and the derivation stays inspectable.
    """
    from .evaluate import FormulaError, evaluate_formula

    known = {str(f["name"]).strip(): float(f["value"]) for f in figures if f.get("name") and f.get("value") is not None}
    out = [(n, v, "", False) for n, v in known.items()]
    for f in figures:
        name, expression = str(f.get("name") or "").strip(), f.get("expression")
        if not name or not expression:
            continue
        try:
            value = evaluate_formula(expression, known, set(known))
        except (FormulaError, ZeroDivisionError) as exc:
            out.append((name, 0.0, f"figure {name!r} could not be derived from {expression!r}: {exc}", True))
            continue
        out.append((name, round(value, 2), f"{name} = {expression} = {value:,.2f}", True))
    return out


def expressions_of(figures: list[dict]) -> dict[str, str]:
    """The derivation behind each figure the document derived rather than printed."""
    return {str(f["name"]).strip(): f["expression"] for f in figures if f.get("name") and f.get("expression")}


def resolve_txn_id(adj: dict, rows: list) -> tuple[str | None, str | None]:
    """Audit notes often name an amount and counterparty but no txn_id."""
    given = adj.get("txn_id")
    if given and any(r.txn_id == given for r in rows):
        return given, None
    amount = adj.get("amount")
    counterparty = adj.get("counterparty")
    if amount is None:
        return None, f"adjustment has neither a resolvable txn_id nor an amount: {adj}"
    target = abs(float(amount))
    matches = [
        r
        for r in rows
        if r.amount is not None
        and abs(abs(r.amount) - target) < 0.01
        and (not counterparty or normalise_name(r.counterparty) == normalise_name(counterparty))
    ]
    if len(matches) == 1:
        return matches[0].txn_id, None
    return None, f"could not resolve adjustment to a unique row ({len(matches)} matches): {adj}"


def appears_in(value: float, text: str) -> bool:
    """Is this figure literally printed in the source?

    Kepler's atomic-provenance rule is that a model should never originate a numeral. We
    still let it transcribe thresholds and amounts, so every transcription is checked back
    against the document it came from -- a hallucinated or mistyped figure becomes visible
    instead of silently setting a covenant limit.
    """
    magnitude = abs(value)
    candidates = {
        f"{magnitude:,.2f}",
        f"{magnitude:.2f}",
        f"{magnitude:,.0f}",
        f"{magnitude:.0f}",
        f"{magnitude:g}",
        f"{magnitude:.1f}",
    }
    haystack = text.replace("\u00a0", " ")
    # Boundaries matter: a bare "2" would otherwise match the 2 inside "72,146.75".
    return any(re.search(rf"(?<![\d.,]){re.escape(c)}(?![\d,])", haystack) for c in candidates)


def verify_numerals(scenario: str, spec: dict, corpus: str, scanned: bool = False) -> list[str]:
    """Report every model-transcribed figure that is not printed in the borrower's documents.

    A figure read off a scanned page has no text to check against, so it is reported as
    unverifiable rather than as absent -- the distinction matters when deciding whether to
    go and look.
    """
    problems = []
    verdict = (
        "could not be verified: this borrower has a scanned source with no text layer"
        if scanned
        else "is not printed in any source document"
    )
    for clause, cov in sorted(spec["covenants"].items()):
        if not appears_in(float(cov["threshold"]), corpus):
            problems.append(f"{scenario} {clause}: threshold {cov['threshold']:,.2f} {verdict}")
        trigger = cov.get("trigger")
        if trigger and not appears_in(float(trigger["value"]), corpus):
            problems.append(f"{scenario} {clause}: trigger value {trigger['value']:,.2f} {verdict}")
    computed = set(spec.get("derived_figures", [])) | {"ebitda_addbacks"}
    for name, value in spec.get("extra", {}).items():
        # A figure this pipeline computed was never printed anywhere; only transcriptions
        # can be checked back against the page.
        if name in computed:
            continue
        if not appears_in(float(value), corpus):
            problems.append(f"{scenario}: figure {name}={value:,.2f} {verdict}")
    for entity in spec.get("ownership", []):
        for field in ("ownership_pct", "held_through_pct"):
            pct = entity.get(field)
            if pct is not None and not appears_in(float(pct), corpus):
                problems.append(f"{scenario}: {entity['name']} {field}={pct} {verdict}")
    if (thr := spec.get("related_party_threshold_pct")) is not None and not appears_in(float(thr), corpus):
        problems.append(f"{scenario}: related-party threshold {thr} {verdict}")
    for adj in spec["adjustments"]:
        amount = adj.get("amount")
        if amount is not None and not appears_in(float(amount), corpus):
            problems.append(f"{scenario}: adjustment amount {amount:,.2f} {verdict}")
    return problems


def verify_derived(scenario: str, spec: dict, corpus: str, scanned: bool = False) -> list[str]:
    """Check the figures the pipeline computed rather than read.

    verify_numerals can only check transcriptions -- a derived figure appears in no document by
    definition, so it skips them, which leaves the derivation itself unchecked. Two things can
    be checked. The value must still follow from the parts, which catches a figure changed after
    it was resolved. And every part the expression names must itself be printed in the borrower's
    documents, which is what makes the derivation rest on the page rather than on itself.
    """
    from .evaluate import FormulaError, evaluate_formula

    problems = []
    extra = spec.get("extra") or {}
    known = {k: float(v) for k, v in extra.items()}
    derived = spec.get("derived_figures") or {}
    for name, expression in sorted(derived.items()):
        try:
            recomputed = round(evaluate_formula(expression, known, set(known)), 2)
        except (FormulaError, ZeroDivisionError) as exc:
            problems.append(f"{scenario}: {name} = {expression!r} no longer resolves: {exc}")
            continue
        recorded = extra.get(name)
        if recorded is None or abs(recomputed - float(recorded)) >= 0.01:
            problems.append(
                f"{scenario}: {name} is recorded as {recorded} but {expression!r} gives "
                f"{recomputed:,.2f}; the figure was changed after it was derived"
            )
        # A sign error in the expression is self-consistent -- the value follows from it and
        # every part is printed -- so nothing above can see it. What it does produce is a
        # negative quantity, and every figure here feeds an `actual`, which the answer format
        # requires to be positive. That makes a non-positive derived figure worth saying aloud.
        if recomputed <= 0:
            problems.append(
                f"{scenario}: {name} = {expression!r} gives {recomputed:,.2f}; a covenant quantity "
                "should be positive, so check the signs in that expression"
            )
        for part in sorted(referenced_names(expression)):
            if part in derived:
                continue  # checked on its own account
            value = extra.get(part)
            if value is None:
                problems.append(f"{scenario}: {name} is derived from {part!r}, which nothing supplies")
            elif not appears_in(float(value), corpus):
                verdict = "has no text layer to check against" if scanned else "is printed in no source document"
                problems.append(f"{scenario}: {name} rests on {part}={float(value):,.2f}, which {verdict}")
    return problems


# Money, however a corpus happens to write it. This feeds one decision -- is an unrouted
# document a financial statement or a policy manual -- where recall matters more than
# precision: a false positive costs a single triage call in which the model says "other",
# while a false negative loses a whole document class without a word. Deliberately not
# derived from the ledger's currency column, unlike account ids: a document may state a
# currency no transaction happens to use.
SEP = r"[\s,\u00a0\u202f]"
SYMBOL = "$€£₸₽¥"
CODE = "USD|EUR|KZT|RUB|GBP|CHF|CNY|JPY|тенге|тг"
MONEY_FIGURE = re.compile(
    rf"[{re.escape(SYMBOL)}]\s?\d[\d{SEP.strip('[]')}]*(?:[.,]\d\d)?"  # $1,234.56   ₸ 1 234
    rf"|\b\d[\d{SEP.strip('[]')}]*(?:[.,]\d\d)?\s?(?:{CODE})\b"  # 1 234 567 тенге
    rf"|\b\d{{1,3}}(?:{SEP}\d{{3}})+(?:[.,]\d\d)?\b"  # 1 234 567,89
)
FINANCIALLY_DENSE = 20


MIN_TRIAGE_CHARS = 1000


def needs_triage(doc) -> bool:
    """Documents the deterministic typer could not place but that may carry substance.

    Three shapes. A file with no text layer at all is a scan. A file with no account number
    that is nonetheless dense with money belongs to a borrower even though it never prints an
    account -- density is a structural signal, not a phrase to match. And a file that does
    route to a borrower but matches no type marker: the markers are fixed Russian headings, so
    a corpus that words its headings differently would otherwise drop a whole document class
    without a word. Confirming those costs one call each and is the only defence against a
    silent loss of, say, every KYC dossier.
    """
    if doc.superseded or (doc.account_id is not None and doc.kind != "other"):
        return False
    if len(doc.text.strip()) < 200:
        return True
    if len(doc.text.strip()) < MIN_TRIAGE_CHARS:
        return False
    if doc.account_id is not None:
        return True
    return len(set(MONEY_FIGURE.findall(doc.text))) >= FINANCIALLY_DENSE


def resolve_unrouted(docs: list, model: str) -> list[str]:
    """Type and route those documents by reading them."""
    notes = []
    named: list[tuple] = []
    for doc in docs:
        if not needs_triage(doc):
            continue
        images = image_pages(doc)
        if not doc.text.strip() and not images:
            continue  # nothing to read
        result = triage_document(doc.text, images, model, doc.doc_id)
        doc.kind = result.get("kind", "other")
        doc.superseded = bool(result.get("superseded"))
        if account := result.get("account_id"):
            doc.account_id = account
            notes.append(f"{doc.doc_id}: routed by reading it to {account} as {doc.kind}")
        elif company := result.get("relates_to_company"):
            named.append((doc, company))
    return notes + link_by_company(docs, named)


def link_by_company(docs: list, named: list[tuple]) -> list[str]:
    """Attach a document that names its borrower but prints no account number.

    Matched on the borrower's own name, exactly -- "Shymkent Refinery JSC" must not attach to
    "Shymkent Refinery Services JSC", so containment is not good enough.
    """
    notes = []
    owners: dict[str, str] = {}
    for d in docs:
        if d.account_id and not d.superseded:
            for name in re.findall(r"([A-Z][A-Za-z\-]+(?: [A-Z][A-Za-z\-]+){0,3} JSC)", d.text):
                if "Halyk" in name or "Audit" in name:
                    continue
                owners.setdefault(normalise_name(name), d.account_id)
    for doc, company in named:
        account = owners.get(normalise_name(company))
        if account:
            doc.account_id = account
            notes.append(f"{doc.doc_id}: {doc.kind} linked to {account} via {company!r}")
        else:
            notes.append(f"{doc.doc_id}: names {company!r}, which matches no borrower; left unrouted")
    return notes


MAX_RETRIEVAL_CHARS = 9000


def unresolved_names(facts: dict) -> dict[str, set[str]]:
    """Names a covenant reads that nothing in the facts or the ledger can supply.

    The first pass extracts what a document offers, against a fixed schema. A covenant may then
    turn out to need a quantity nobody transcribed, and the evaluator refuses such a formula
    outright -- correctly, but that leaves the cell answered on a guess. This is the list of what
    is missing, computed rather than assumed, and it is what the second pass goes looking for.
    """
    supplied_by_ledger = set(CATEGORIES) | set(DERIVED) | set(COMPUTED)
    gaps: dict[str, set[str]] = {}
    for scenario, spec in facts["scenarios"].items():
        have = supplied_by_ledger | set(spec.get("extra") or {})
        # Mirror the group build-ups aggregates() performs, so a ratio it can reconstruct from
        # components present here is not reported as a gap the second pass must go and find.
        if any(p in have for p in DEBT_PARTS):
            have.add("group_total_debt")
        if any(n in have for n in EBIT_NAMES):
            have.add("group_ebitda")
        if {"group_total_debt", "group_ebitda"} <= have:
            have.add("group_leverage_ratio")
        missing: set[str] = set()
        for covenant in (spec.get("covenants") or {}).values():
            missing |= referenced_names(covenant["metric"]) - have
            if trigger := covenant.get("trigger"):
                missing |= referenced_names(trigger["metric"]) - have
        if missing:
            gaps[scenario] = missing
    return gaps


def fill_gaps(facts: dict, routing: dict, model: str) -> list[str]:
    """Ask for exactly what the formulas asked for and the first pass did not deliver.

    Cheap by construction: one call per borrower that has a gap, and none at all when the first
    pass was complete. The trigger is not a heuristic -- the evaluator names the identifier it
    cannot resolve -- so the second pass is aimed rather than speculative. Retrieved figures go
    through the same resolver as transcribed ones, so a roll-forward found on demand is evaluated
    in code exactly like one found the first time.
    """
    notes = []
    for scenario, missing in sorted(unresolved_names(facts).items()):
        docs = [d for kind_docs in routing.get(scenario, {}).values() for d in kind_docs]
        body = "\n\n".join(
            f"--- {d.doc_id} ({d.kind}) ---\n{d.text[:MAX_RETRIEVAL_CHARS]}" for d in docs if d.text.strip()
        )
        wanted = ", ".join(sorted(missing))
        if not body.strip():
            notes.append(f"{scenario}: {wanted} unresolved, and no readable document to search")
            continue

        payload = retrieve_figures(sorted(missing), body, model, f"gap-{scenario}")
        figures = [f for f in payload["figures"] if f["found"]]
        basis = {f["name"]: f.get("basis", "") for f in figures}
        extra = facts["scenarios"][scenario].setdefault("extra", {})
        for name, value, note, derived in resolve_figures(figures):
            if note and derived and "could not be derived" in note:
                notes.append(f"{scenario}: {note}")
                continue
            extra[name] = value
            if name in missing:
                where = note or basis.get(name, "")
                notes.append(f"{scenario}: {name} recovered on demand = {value:,.2f} -- {where[:100]}")

        for name in sorted(missing - set(extra)):
            notes.append(
                f"{scenario}: {name} is referenced by a covenant but stated in no document; "
                "the cell is answered on a best effort and flagged"
            )
    return notes


def apply_corrections(facts: dict, path: Path, data: Dataset) -> list[str]:
    """Apply owner-issued corrections to figures the source documents print wrongly.

    Distinct from an auditor adjustment, which the documents themselves record. A correction
    says the document is wrong. Each is applied only while the extracted value still matches
    its `from`, so re-reading a fixed document does not silently double-patch it, and each is
    reported with the authority that issued it.

    A correction is about one document, not one borrower identifier. Corpora reuse identifiers,
    so a correction whose document is absent here is about a different corpus and is skipped.
    """
    if not path.exists():
        return []
    notes = []
    for c in json.loads(path.read_text()).get("corrections", []):
        if (doc := c.get("document")) and not (data.documents / doc).exists():
            notes.append(
                f"correction for {c['scenario']} {c['clause']} skipped: it is about {doc}, "
                f"which is not in this corpus"
            )
            continue
        cov = facts["scenarios"].get(c["scenario"], {}).get("covenants", {}).get(c["clause"])
        if cov is None:
            notes.append(f"correction for {c['scenario']} {c['clause']} has no matching covenant")
            continue
        current = cov.get(c["field"])
        if current != c["from"]:
            notes.append(
                f"correction for {c['scenario']} {c['clause']}.{c['field']} NOT applied: "
                f"expected {c['from']}, extraction now reads {current} -- re-check whether it is still needed"
            )
            continue
        cov[c["field"]] = c["to"]
        cov["correction"] = {
            "field": c["field"],
            "from": c["from"],
            "to": c["to"],
            "authority": c["authority"],
            "reason": c["reason"],
        }
        notes.append(
            f"{c['scenario']} {c['clause']}: {c['field']} corrected {c['from']} -> {c['to']} "
            f"on the authority of {c['authority']} (document prints the wrong figure)"
        )
    return notes


def build(model: str, workers: int, data: Dataset) -> tuple[dict, list[str]]:
    use_workdir(data.workdir)
    acc_to_scenario = account_map(data)
    docs = load_documents(data.documents, data.artefact("text"), acc_to_scenario)
    warnings_pre = resolve_unrouted(docs, model)
    routing = route(docs, acc_to_scenario)
    template = json.loads(data.template.read_text())
    scenarios = sorted(template["answers"])
    warnings = warnings_pre + routing_problems(routing, scenarios)

    # Second, independent defence against a superseded edition: its clauses are dated to a
    # different year than the ledger. Relying only on the void stamp is a single point of
    # failure, and every superseded edition here differs from its live twin in exactly the
    # clause 6.1 threshold.
    ledger_years = {row["date"][:4] for row in csv.DictReader(data.ledger.open())}
    for scenario in scenarios:
        for doc in routing.get(scenario, {}).get("loan_agreement", []):
            if doc.period and doc.period[0][:4] not in ledger_years:
                warnings.append(
                    f"{scenario}: live agreement {doc.doc_id} is dated "
                    f"{doc.period[0]}..{doc.period[1]}, outside the ledger period "
                    f"{sorted(ledger_years)} -- possible superseded edition"
                )
            elif not doc.period:
                warnings.append(f"{scenario}: could not determine the covenant period of {doc.doc_id}")

    jobs = []
    for scenario in scenarios:
        kinds = routing.get(scenario, {})
        for doc in kinds.get("loan_agreement", []):
            jobs.append((scenario, "agreement", doc))
        for doc in kinds.get("kyc", []):
            jobs.append((scenario, "kyc", doc))
        for kind in AUDIT_KINDS:
            for doc in kinds.get(kind, []):
                jobs.append((scenario, "audit", doc))

    def run(job):
        scenario, kind, doc = job
        return job, extract(kind, body_for(doc), model, image_pages(doc), f"{scenario}-{doc.doc_id}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(run, jobs))

    facts: dict = {"extraction_model": model, "fx_rates": {}, "scenarios": {}}
    fx_notes: dict[str, list[str]] = {}
    stem_map = data.artefact("stem_categories.json")
    load_learned(stem_map)
    ledger = load_ledger(data.ledger, set(scenarios), {})

    # Descriptions the deterministic rules don't recognise are categorised by the model
    # rather than by adding another regex. The result is a reviewable, hand-correctable file.
    pending = unmatched_stems(ledger)
    if pending:
        classify_stems(pending, model, stem_map)
        load_learned(stem_map)
        ledger = load_ledger(data.ledger, set(scenarios), {})
        warnings.append(f"{len(pending)} unseen stems categorised by model -> {stem_map.name} (review it)")

    per_scenario: dict[str, dict] = {s: {"agreement": {}, "kyc": {}, "audit": []} for s in scenarios}
    for (scenario, kind, _doc), payload in results:
        if kind == "audit":
            per_scenario[scenario]["audit"].append(payload)
        else:
            per_scenario[scenario][kind] = payload

    for scenario in scenarios:
        bundle = per_scenario[scenario]
        rows = [t for t in ledger if t.scenario == scenario]
        kyc = bundle["kyc"] or {}
        threshold = kyc.get("related_party_threshold_pct")
        entities = kyc.get("entities", []) or []
        related = []
        # A dossier that prints no ownership threshold is not silent about relatedness -- it
        # states it outright ("классифицирован как АФФИЛИРОВАННОЕ ЛИЦО ... Платежи данному
        # контрагенту признаются Ограниченными платежами"). Naming a counterparty is the
        # classification in that case, so requiring a percentage comparison would discard the
        # very finding the dossier exists to record. Where a threshold is printed it governs,
        # and the listing alone decides nothing.
        if threshold is None and entities:
            related = [e["name"] for e in entities]
            warnings.append(
                f"{scenario}: no ownership threshold printed; treating the {len(related)} "
                f"counterparty(ies) the KYC names as related parties"
            )
        for e in entities if threshold is not None else []:
            printed = e.get("ownership_pct")
            if printed is None:
                continue
            effective = float(printed)
            if (through := e.get("held_through_pct")) is not None:
                # An indirect holding counts at the Group's look-through stake.
                effective = round(effective * float(through) / 100, 6)
                warnings.append(
                    f"{scenario}: {e['name']} held indirectly -- effective stake "
                    f"{printed}% x {through}% = {effective}% vs threshold {threshold}%"
                )
            if effective >= float(threshold):
                related.append(e["name"])
        pledge = kyc.get("subsidiary_pledge_threshold_pct")
        subsidiaries = kyc.get("subsidiaries", []) or []
        unrestricted = []
        for s in subsidiaries:
            # A dossier that states the status governs; the pledge test is how a dossier that
            # states only a percentage says the same thing. Reading the percentage over an
            # explicit designation would overrule the document with a proxy for it.
            if (stated := s.get("designation")) is not None:
                if stated == "unrestricted":
                    unrestricted.append(s["name"])
                warnings.append(f"{scenario}: {s['name']} designated {stated} by the KYC")
            elif pledge is not None and s.get("pledged_pct") is not None and float(s["pledged_pct"]) < float(pledge):
                unrestricted.append(s["name"])
        # A dossier that lists no subsidiary and prints no pledge threshold has not placed the
        # borrower's subsidiaries inside the security perimeter -- it has said nothing about
        # them. Treating silence as "all restricted" answers a covenant on transfers to
        # Unrestricted subsidiaries with 0.00, which asserts no such transfer happened while
        # the ledger records several. Unclassified transfers are therefore left in scope, and
        # this is reported: it is an interpretation of a gap, not a reading of a document.
        if not unrestricted and pledge is None and not (kyc.get("subsidiaries") or []):
            transferred = sorted(
                {t.counterparty for t in rows if t.category == "transfer_subsidiary" and t.amount and t.amount < 0}
            )
            wants_split = any(
                "transfer_unrestricted" in (c.get("metric") or "") for c in (bundle["agreement"] or {}).values()
            )
            if transferred and wants_split:
                unrestricted = transferred
                warnings.append(
                    f"{scenario}: the KYC classifies no subsidiary, so the "
                    f"restricted/unrestricted split cannot be made; the {len(transferred)} "
                    f"counterparty(ies) receiving subsidiary transfers are left in scope rather "
                    f"than the covenant being answered 0.00"
                )

        adjustments, extra, derived, derivations = [], {}, set(), {}
        for payload in bundle["audit"]:
            for adj in payload.get("adjustments", []) or []:
                txn_id, problem = resolve_txn_id(adj, rows)
                if problem:
                    # The deterministic match needs a clean id or amount + counterparty. When
                    # the note identifies its row in prose instead, hand the residue to a
                    # Code Mode agent that can query the ledger and intersect the results.
                    txn_id, why = reconcile_adjustment(adj, rows, scenario, model)
                    if txn_id is None:
                        warnings.append(f"{scenario}: {problem}")
                        continue
                    warnings.append(f"{scenario}: adjustment matched to {txn_id} by reconciliation ({why})")
                entry = {"kind": adj["kind"], "txn_id": txn_id, "source": adj.get("source", "")}
                if adj["kind"] == "reclassify":
                    entry["to_category"] = adj["to_category"]
                if adj["kind"] == "amount_override":
                    entry["amount"] = abs(float(adj["amount"]))
                adjustments.append(entry)
            for key, value in (payload.get("scalars") or {}).items():
                if value:
                    extra[key] = float(value)
            derivations.update(expressions_of(payload.get("figures") or []))
            for name, value, note, is_derived in resolve_figures(payload.get("figures") or []):
                if name in CATEGORIES or name in DERIVED:
                    warnings.append(
                        f"{scenario}: document figure {name!r}={value:,.2f} collides with a ledger "
                        "category of the same name and was ignored; they are different quantities"
                    )
                    continue
                extra[name] = value
                if is_derived:
                    derived.add(name)
                if note:
                    warnings.append(f"{scenario}: {note}")
            # The add-back total is arithmetic, so code does it: the model only transcribes
            # the rows and the threshold it read off the page.
            items = payload.get("one_off_items") or []
            if items:
                floor = payload.get("materiality_threshold") or 0.0
                qualifying = [abs(float(i["amount"])) for i in items if abs(float(i["amount"])) >= floor]
                dropped = len(items) - len(qualifying)
                if qualifying:
                    extra["ebitda_addbacks"] = round(sum(qualifying), 2)
                warnings.append(
                    f"{scenario}: ebitda_addbacks = {extra.get('ebitda_addbacks', 0):,.2f} from "
                    f"{len(qualifying)}/{len(items)} one-off items at or above {floor:,.2f}"
                    + (f" ({dropped} below threshold)" if dropped else "")
                )
            disclosed, notes = rates_from_disclosures(payload.get("fx_disclosures") or [])
            for currency, rate in disclosed.items():
                facts["fx_rates"].setdefault(scenario, {}).setdefault(currency, rate)
            warnings.extend(f"{scenario}: {n}" for n in notes)
        # Fallback: the settlement pair is stated in the document even when the model
        # returns only the general policy sentence.
        for kind in AUDIT_KINDS:
            for doc in routing.get(scenario, {}).get(kind, []):
                fx_notes.setdefault(scenario, []).append(doc.text)

        facts["scenarios"][scenario] = {
            "related_party_threshold_pct": threshold,
            "related_parties": related,
            "ownership": kyc.get("entities", []) or [],
            "unrestricted_subsidiaries": unrestricted,
            "extra": extra,
            "derived_figures": {n: derivations[n] for n in sorted(derived) if n in derivations},
            "adjustments": adjustments,
            "covenants": bundle["agreement"],
        }
        if not bundle["agreement"]:
            warnings.append(f"{scenario}: no covenants extracted from the loan agreement")
        corpus = "\n".join(d.text for kind_docs in routing.get(scenario, {}).values() for d in kind_docs)
        scanned = any(not d.text.strip() for kind_docs in routing.get(scenario, {}).values() for d in kind_docs)
        warnings.extend(verify_numerals(scenario, facts["scenarios"][scenario], corpus, scanned))
        warnings.extend(verify_derived(scenario, facts["scenarios"][scenario], corpus, scanned))

    # Only where the model transcribed nothing does the raw-text scan get a turn.
    for scenario, notes in fx_notes.items():
        if facts["fx_rates"].get(scenario):
            continue
        if fallback := derive_fx(notes):
            facts["fx_rates"][scenario] = fallback
            warnings.append(f"{scenario}: FX rate {fallback} recovered by raw-text scan, not transcribed")
    # Second pass, aimed at whatever gaps the first pass left.
    warnings.extend(fill_gaps(facts, routing, model))
    # Beside the corpus it is about, not in a shared file every corpus reads.
    warnings.extend(apply_corrections(facts, data.root / "source_corrections.json", data))
    return facts, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract covenant facts from the document set")
    parser.add_argument("--model", default="anthropic:claude-opus-5")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--out", default=None, help="defaults to <corpus workdir>/facts_extracted.json")
    add_data_argument(parser)
    args = parser.parse_args()

    data = Dataset(Path(args.data))
    data.check()
    data.workdir.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else data.artefact("facts_extracted.json")
    facts, warnings = build(args.model, args.workers, data)
    if pruned := prune_cache():
        warnings.append(f"pruned {pruned} cached extraction(s) left by an earlier prompt or schema")
    out.write_text(json.dumps(facts, indent=2, ensure_ascii=False) + "\n")
    # Kept on disk because the confidence report runs from a different command, and on a corpus
    # with no answer key what the reader said about its own reading is most of the evidence.
    log = data.artefact("extraction_warnings.json")
    log.write_text(json.dumps(warnings, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out}")
    print(f"wrote {log}")
    for warning in warnings:
        print(f"  warn: {warning}")


if __name__ == "__main__":
    main()
