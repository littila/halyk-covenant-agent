from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent, ModelRetry
from pydantic_ai.capabilities import Thinking

from .documents import Doc, article_6, covenant_supplement
from .ledger import CATEGORIES as LEDGER_CATEGORIES
from .ledger import DERIVED as LEDGER_DERIVED

CACHE = Path(__file__).resolve().parent.parent / "cache" / "extraction"

CATEGORIES = ", ".join(LEDGER_CATEGORIES)
DERIVED = ", ".join(LEDGER_DERIVED)

DOC_KINDS = Literal[
    "loan_agreement",
    "kyc",
    "audit_notes",
    "aup_report",
    "treasury_memo",
    "financial_statements",
    "other",
]


class Trigger(BaseModel):
    metric: str = Field(description="Formula over category names, e.g. financing_inflow")
    operator: Literal[">=", "<=", ">", "<"]
    value: float


class Covenant(BaseModel):
    clause: str = Field(description='Clause number exactly as printed, e.g. "6.1"')
    metric: str = Field(description="Arithmetic formula over category names")
    operator: Literal[">=", "<=", ">", "<"] = Field(description="The relation that must hold for COMPLIANCE")
    threshold: float
    quarter: int | None = Field(default=None, description="1-4 if the clause tests a single fiscal quarter, else null")
    trigger: Trigger | None = Field(default=None, description="Set only for springing/conditional tests")
    definition_verbatim: str = Field(description="The operative sentence, verbatim, trimmed")


class Article6(BaseModel):
    covenants: list[Covenant]


class Entity(BaseModel):
    name: str = Field(description="Exact name as printed, including quotes and punctuation")
    ownership_pct: float = Field(
        description="The percentage printed against this entity, exactly as shown. Never adjust it."
    )
    held_through_pct: float | None = Field(
        default=None,
        description=(
            "Only when the dossier says this holding is indirect and that the Group's EFFECTIVE "
            "stake governs: the Group's percentage in the intermediate holder, as printed. The "
            "look-through multiplication is done downstream -- do not compute it yourself."
        ),
    )


class Subsidiary(BaseModel):
    name: str
    pledged_pct: float


class Kyc(BaseModel):
    related_party_threshold_pct: float | None = Field(
        description="Ownership % at or above which a counterparty is a related party"
    )
    entities: list[Entity]
    subsidiary_pledge_threshold_pct: float | None = Field(
        description="Pledged-asset % below which a subsidiary is Unrestricted, else null"
    )
    subsidiaries: list[Subsidiary]


class Adjustment(BaseModel):
    kind: Literal["reclassify", "amount_override", "exclude"]
    txn_id: str | None = None
    amount: float | None = None
    counterparty: str | None = None
    to_category: str | None = Field(default=None, description="Target category for a reclassify")
    source: str = Field(description="Short reason, quoting the note")


class OneOffItem(BaseModel):
    """One row of a one-off / non-recurring items table, transcribed but never summed."""

    description: str
    counterparty: str | None = None
    amount: float = Field(description="This item's own amount, exactly as printed")


class DocumentFigure(BaseModel):
    """A quantity from a document that no ledger row can supply.

    Either transcribed as printed, or -- when the document explains how a quantity follows
    from figures it does state -- named with the expression that produces it. The expression
    is evaluated downstream, so the arithmetic never runs inside the model.
    """

    name: str = Field(
        description=(
            "snake_case identifier a covenant formula would use for this quantity, e.g. "
            "net_debt, cash_balance, tangible_net_worth, group_capex, severance_liability"
        )
    )
    value: float | None = Field(default=None, description="The figure exactly as printed. Never compute or combine.")
    expression: str | None = Field(
        default=None,
        description=(
            "Use instead of `value` when the document does not print this quantity but does "
            "print the parts, and says how they relate -- e.g. a property roll-forward that "
            "states opening and closing balances, the depreciation charge, and that there were "
            "no disposals, from which additions follow. Write it as arithmetic over the `name`s "
            "of other figures you are transcribing, e.g. "
            "'ppe_closing - ppe_opening + ppe_depreciation'. Do not evaluate it yourself."
        ),
    )
    basis: str = Field(description="The verbatim phrase, or the sentence licensing the expression")


class FxDisclosure(BaseModel):
    """How a document fixes a non-USD rate, in whichever form it states it."""

    currency: str = Field(description="ISO code of the non-USD currency, e.g. EUR")
    rate: float | None = Field(
        default=None,
        description="Units of USD per 1 unit of the currency, only if the document states a "
        "rate outright (e.g. '1 EUR = 1.16 USD'). Never compute it yourself.",
    )
    foreign_amount: float | None = Field(
        default=None, description="Settlement form: the amount in the foreign currency"
    )
    usd_amount: float | None = Field(default=None, description="Settlement form: the USD amount that settled it")
    basis: str = Field(description="The verbatim sentence this comes from")


class FoundFigure(BaseModel):
    """One quantity a covenant asked for, looked up on demand.

    The first pass extracts what a document offers; this one looks for what a formula turned out
    to need. `found` false is a real answer -- better than a plausible number -- so the pipeline
    can say the figure is absent rather than silently treat it as zero.
    """

    name: str = Field(description="Exactly the identifier you were asked to find, unchanged.")
    found: bool = Field(description="Whether the documents actually state this quantity.")
    value: float | None = Field(default=None, description="The figure as printed. Leave null if you set `expression`.")
    expression: str | None = Field(
        default=None,
        description=(
            "Set this instead of `value` when the documents do not print the quantity but do "
            "state the parts it follows from, e.g. 'current_borrowings + non_current_borrowings "
            "- cash_balance'. Every name in it must be a figure you also return. Do not evaluate it."
        ),
    )
    basis: str = Field(
        description=(
            "Quote the line or sentence you took it from, naming the note or clause. If you did "
            "not find it, say where you looked and what was there instead."
        )
    )


class Retrieval(BaseModel):
    figures: list[FoundFigure]


class Scalars(BaseModel):
    severance_liability: float | None = None


class AuditFindings(BaseModel):
    adjustments: list[Adjustment]
    scalars: Scalars
    figures: list[DocumentFigure] = Field(
        default_factory=list,
        description=(
            "Quantities stated by the document that no transaction row carries -- balance-sheet "
            "amounts, group totals, disclosed liabilities. Name each with the conventional "
            "covenant term for the concept, not the document's own label: total capital "
            "expenditure of a group is `group_capex`, a redundancy provision is "
            "`severance_liability`; also `net_debt`, `cash_balance`, `tangible_net_worth`. A "
            "covenant elsewhere will refer to it by that name, so an idiosyncratic one is lost. "
            "Never reuse a ledger category name such as revenue, opex, payroll or capex: those "
            "are the borrower's own totals, and a group or statement figure is a different "
            "quantity. Qualify it -- group_revenue, group_capex -- or omit it."
        ),
    )
    one_off_items: list[OneOffItem] = Field(
        default_factory=list,
        description=(
            "Every individual one-off / non-recurring item offered for EBITDA add-back, each "
            "with its own amount. Transcribe the rows; do NOT add them up and do NOT apply the "
            "materiality threshold yourself -- both are done downstream."
        ),
    )
    fx_disclosures: list[FxDisclosure] = Field(
        default_factory=list,
        description=(
            "Every place this document fixes a non-USD exchange rate, in whatever form: a rate "
            "quoted outright, an invoice in one currency settled by a stated USD payment, or a "
            "row of a rate table. Transcribe the figures; the division is done downstream. A "
            "general accounting-policy sentence carrying no figures is not a disclosure."
        ),
    )
    materiality_threshold: float | None = None


class Triage(BaseModel):
    account_id: str | None = Field(description="The ACC-#### borrower account printed on it, if any")
    relates_to_company: str | None = Field(
        default=None,
        description=(
            "When no account number is printed, the borrower company this document concerns, "
            "exactly as named -- e.g. a group report whose segment note says the segment is "
            "conducted through a named subsidiary. Null if it concerns no single borrower."
        ),
    )
    kind: DOC_KINDS
    superseded: bool = Field(
        description=(
            "True if stamped as a void edition (НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ) or a draft interim "
            "schedule (ПРОЕКТ — ПРОМЕЖУТОЧНАЯ ВЕДОМОСТЬ)"
        )
    )


AGREEMENT_INSTRUCTIONS = f"""You extract financial covenants from Article 6 of a Kazakh bank loan
agreement. The document is in Russian; clause headings are sometimes in English.

Formulas may use these category names, arithmetic (+ - * /), parentheses, and max()/min():
{CATEGORIES}

Plus these three, which are resolved by *who was paid* rather than by what the payment was for:
{DERIVED}
- `related_party` is the total paid to counterparties the KYC dossier identifies as related,
  whatever the payments were nominally for. A clause limiting "Ограниченные платежи"/"Restricted
  Payments"/payments to связанным сторонам or аффилированным лицам means `related_party` -- not
  `distributions`, and not `transfer_subsidiary`.
- `transfer_unrestricted` / `transfer_restricted` split transfers of assets to subsidiaries by
  whether the KYC places that subsidiary outside the security perimeter. A clause about assets
  transferred to Unrestricted subsidiaries means `transfer_unrestricted`.
A formula may also reference a quantity stated by a document rather than the ledger -- a balance,
a group total, a disclosed liability -- by a snake_case name such as group_capex,
severance_liability, ebitda_addbacks, net_debt, cash_balance. Use the plainest name for the
concept; the same name must be recoverable from the document that states it.

Rules:
- EBITDA means (revenue - opex) unless the clause defines it otherwise.
- A clause capping "the larger of" two lines is max(a, b); their sum is NOT the metric.
- "X must be at least T times Y" is metric "X / Y" with operator ">=".
- A springing/conditional test that only applies above some level goes in `trigger`.
- Clause numbers are NOT stable across borrowers. Read what each clause actually says.
- Do not invent categories. If a needed quantity has no category, use the closest listed one."""

KYC_INSTRUCTIONS = """You extract a related-party dossier (KYC) for a Kazakh bank borrower.

Copy entity names exactly as printed, including quotes and punctuation, and copy each percentage
as printed. Where the dossier says a holding is indirect and that the Group's effective stake is
what counts, also record the intermediate holder's percentage in `held_through_pct` -- but never
multiply them yourself. If a section is absent, use an empty list or null. Some tables are supplied as page images rather than text: read every
attached image before answering."""


RETRIEVE_INSTRUCTIONS = """A covenant formula for this borrower references quantities that nothing
has supplied yet. You are given every document routed to that borrower. Find each requested
quantity, or state plainly that it is not there.

The names are given to you. Return each one back **unchanged** -- do not rename, pluralise or
tidy them, because a formula elsewhere already refers to them exactly as written.

Transcribe; never compute. If a document prints the quantity, copy the figure into `value`. If it
does not print it but states the parts it follows from, put the arithmetic into `expression` and
return those parts as figures too -- the expression is evaluated outside you.

A quantity that is genuinely absent must come back with `found` false. That is a useful answer:
it tells the pipeline to report a gap instead of computing with a number nobody wrote down.
Inventing a plausible figure is the one outcome worse than not finding it.

Every figure needs a `basis` that quotes the line it came from and names its note or clause, so a
reviewer can go to the page and check."""

AUDIT_INSTRUCTIONS = f"""You extract auditor adjustments that affect covenant testing, from a
Kazakh audit note, agreed-upon-procedures report, or treasury memo.

Adjustment kinds:
- "reclassify" moves a row between categories. Categories: {CATEGORIES}
- "amount_override" supplies a figure the ledger export is missing.
- "exclude" removes a row from the covenant period (cut-off / accrual timing).

Rules:
- An item the auditor CONSIDERED and REJECTED is NOT an adjustment. Omit it entirely.
- An item whose conclusion is deferred to another report is NOT an adjustment here.
- Transcribe one-off items individually into `one_off_items` and report the stated materiality
  threshold separately. Never sum them and never filter them yourself.
- Record any exchange-rate disclosure in `fx_disclosures`. If the document quotes a rate, put it
  in `rate`; if it shows an invoice settled in USD, put both amounts in `foreign_amount` and
  `usd_amount` and leave `rate` null. Do not divide one by the other yourself.
- Some tables are supplied as page images: read every attached image before answering."""

TRIAGE_INSTRUCTIONS = """You identify a banking document that text-based routing could not place,
from its page images and/or text.

A "Досье «Знай своего клиента» (KYC)" is kind "kyc". Consolidated or standalone financial
statements are kind "financial_statements"; if such a report names the subsidiary its segment is
conducted through, put that company in `relates_to_company`, since the report itself carries no
account number."""

SPECS: dict[str, tuple[type[BaseModel], str]] = {
    "agreement": (Article6, AGREEMENT_INSTRUCTIONS),
    "kyc": (Kyc, KYC_INSTRUCTIONS),
    "audit": (AuditFindings, AUDIT_INSTRUCTIONS),
    "retrieve": (Retrieval, RETRIEVE_INSTRUCTIONS),
    "triage": (Triage, TRIAGE_INSTRUCTIONS),
}


def validate_audit(payload: AuditFindings) -> AuditFindings:
    """Reject an adjustment the pipeline could never apply, and say why.

    Extraction is not deterministic: the same note has come back once with the figure in
    `amount` and once with it only narrated inside `source`. Silently dropping the second
    shape loses a reclassification, so make the model correct itself instead.
    """
    for adj in payload.adjustments:
        if adj.txn_id is None and adj.amount is None:
            raise ModelRetry(
                f"The {adj.kind!r} adjustment has neither txn_id nor amount, so it cannot be "
                "matched to a ledger row. Put the transaction id in `txn_id` when the note "
                "gives one, and always put the figure in `amount` as a plain number -- "
                "quoting it inside `source` is not enough."
            )
        if adj.kind == "reclassify" and not adj.to_category:
            raise ModelRetry("A 'reclassify' adjustment must name its target in `to_category`.")
        if adj.kind == "amount_override" and adj.amount is None:
            raise ModelRetry("An 'amount_override' adjustment must supply `amount`.")
    for fig in payload.figures:
        if (fig.value is None) == (fig.expression is None):
            raise ModelRetry(
                f"Figure {fig.name!r} must carry exactly one of `value` (printed outright) or "
                "`expression` (arithmetic over other figures you are transcribing)."
            )
    for fx in payload.fx_disclosures:
        if fx.rate is None and (fx.foreign_amount is None or fx.usd_amount is None):
            raise ModelRetry(
                f"The {fx.currency} disclosure carries neither a quoted `rate` nor both "
                "`foreign_amount` and `usd_amount`, so no rate can be derived from it. "
                "Give whichever form the document actually states."
            )
        if fx.foreign_amount is not None and fx.foreign_amount == 0:
            raise ModelRetry(f"The {fx.currency} disclosure has a zero foreign_amount.")
    return payload


def validate_retrieval(payload: Retrieval) -> Retrieval:
    for fig in payload.figures:
        if not fig.found:
            if fig.value is not None or fig.expression is not None:
                raise ModelRetry(
                    f"{fig.name!r} is marked not found but carries a figure. Either it is in the "
                    "documents, or it is not."
                )
            continue
        if (fig.value is None) == (fig.expression is None):
            raise ModelRetry(
                f"{fig.name!r} must carry exactly one of `value` (printed outright) or "
                "`expression` (arithmetic over other figures you are returning)."
            )
    return payload


_AGENTS: dict[tuple[str, str, str], Agent] = {}


def _agent(kind: str, model: str, effort: str) -> Agent:
    key = (kind, model, effort)
    if key not in _AGENTS:
        output_type, instructions = SPECS[kind]
        _AGENTS[key] = Agent(
            model,
            output_type=output_type,
            instructions=instructions,
            capabilities=[Thinking(effort=effort)],
            retries=3,
        )
        if kind == "audit":
            _AGENTS[key].output_validator(validate_audit)
        if kind == "retrieve":
            _AGENTS[key].output_validator(validate_retrieval)
    return _AGENTS[key]


USED_CACHE: set[Path] = set()


def prune_cache() -> int:
    """Drop extractions produced by an earlier prompt or schema.

    The cache key covers the instructions and the output schema, so every edit to either
    leaves the previous entry behind, unreadable but on disk. Only ever called after a full
    successful build, when USED_CACHE holds every entry the current code would read.
    """
    if not USED_CACHE or not CACHE.exists():
        return 0
    stale = [f for f in CACHE.glob("*.json") if f not in USED_CACHE]
    for f in stale:
        f.unlink()
    return len(stale)


def extract_raw(
    body: str,
    model: str,
    image_paths: list[Path],
    kind: str,
    cache_key: str,
    effort: str = "high",
) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    # The prompt and the output schema are part of the identity of a cached extraction:
    # editing instructions must invalidate it, or the facts stop matching the code.
    output_type, instructions = SPECS[kind]
    contract = json.dumps(output_type.model_json_schema(), sort_keys=True)
    fingerprint = "|".join(p.name for p in image_paths)
    digest = hashlib.sha256(
        f"{kind}|{cache_key}|{body}|{model}|{fingerprint}|{instructions}|{contract}".encode()
    ).hexdigest()[:16]
    cached = CACHE / f"{kind}-{cache_key}-{digest}.json"
    USED_CACHE.add(cached)
    if cached.exists():
        return json.loads(cached.read_text())

    prompt: list = [body] if body else []
    prompt += [BinaryContent(data=p.read_bytes(), media_type=media_type(p)) for p in image_paths]
    result = _agent(kind, model, effort).run_sync(prompt)
    payload = result.output.model_dump()
    cached.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return payload


def extract(kind: str, body: str, model: str, image_paths: list[Path], cache_key: str) -> dict:
    payload = extract_raw(body, model, image_paths, kind, cache_key)
    if kind == "agreement":
        return {c["clause"]: c for c in payload["covenants"]}
    return payload


def retrieve_figures(names: list[str], body: str, model: str, cache_key: str) -> dict:
    """Second pass: look for exactly the quantities a formula asked for and nothing supplied."""
    asked = "Find these quantities and return each name unchanged: " + ", ".join(sorted(names))
    return extract_raw(f"{asked}\n\n{body}", model, [], "retrieve", cache_key)


def triage_document(body: str, image_paths: list[Path], model: str, cache_key: str) -> dict:
    """Type and route a document that carries no usable account number."""
    return extract_raw(body[:12000], model, image_paths, "triage", cache_key)


MIN_IMAGE_BYTES = 20_000
MAX_IMAGES_PER_DOC = 8
ZIP_MEDIA_DIRS = ("word/media/", "ppt/media/", "xl/media/")
IMAGE_SUFFIX = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}


def media_type(path: Path) -> str:
    return IMAGE_SUFFIX.get(path.suffix.lower(), "image/png")


def _pdf_images(path: Path) -> list[tuple[str, bytes, str]]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(path)
    except Exception:
        return []
    out = []
    for index, page in enumerate(reader.pages):
        try:
            images = list(page.images)
        except Exception:
            continue
        for order, image in enumerate(images):
            if len(image.data) >= MIN_IMAGE_BYTES:
                out.append((f"p{index + 1}_{order}", image.data, ".png"))
    return out


def _zip_images(path: Path) -> list[tuple[str, bytes, str]]:
    """Office formats are zips; their figures live under */media/."""
    import zipfile

    try:
        archive = zipfile.ZipFile(path)
    except Exception:
        return []
    out = []
    with archive:
        for name in archive.namelist():
            if not name.startswith(ZIP_MEDIA_DIRS):
                continue
            suffix = Path(name).suffix.lower()
            if suffix not in IMAGE_SUFFIX:
                continue
            data = archive.read(name)
            if len(data) >= MIN_IMAGE_BYTES:
                out.append((Path(name).stem, data, suffix))
    return out


def image_pages(doc: Doc) -> list[Path]:
    """Content images worth reading with vision.

    Covers both whole scanned pages and a figure or table pasted into an otherwise-textual
    page -- keying on "page has no text" alone would silently skip the latter. Images are
    deduplicated by content hash, since a PDF XObject is commonly referenced from every page.
    """
    blobs = _pdf_images(doc.path) if doc.path.suffix.lower() == ".pdf" else _zip_images(doc.path)
    out_dir = CACHE.parent / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths, seen = [], set()
    for label, data, suffix in blobs:
        digest = hashlib.sha256(data).hexdigest()[:12]
        if digest in seen:
            continue
        seen.add(digest)
        path = out_dir / f"{doc.doc_id}_{label}_{digest}{suffix}"
        if not path.exists():
            path.write_bytes(data)
        paths.append(path)
        if len(paths) >= MAX_IMAGES_PER_DOC:
            break
    return paths


def body_for(doc: Doc) -> str:
    if doc.kind == "loan_agreement":
        return article_6(doc.text) or doc.text[:12000]
    if doc.kind in ("audit_notes", "aup_report", "treasury_memo"):
        return covenant_supplement(doc.text) or doc.text[:8000]
    return doc.text[:12000]
