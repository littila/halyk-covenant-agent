from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import anydoc
from pypdf import PdfReader

# Fallback only. The ledger holds the account ids that actually exist, so the pattern is
# normally derived from them rather than assumed -- a corpus that numbers its accounts
# differently would otherwise fail to join a single document to a borrower.
ACC_RE = re.compile(r"ACC-\d{4}")


def account_pattern(accounts) -> re.Pattern:
    """Match the account ids the ledger contains, longest first so no id matches inside another."""
    ids = sorted({a for a in accounts if a}, key=len, reverse=True)
    if not ids:
        return ACC_RE
    body = "|".join(re.escape(a) for a in ids)
    return re.compile(rf"(?<![\w-])(?:{body})(?![\w-])")


# Agreements in a held-out set are not all in Russian; one borrower's is drafted in English.
PERIOD_RE = re.compile(
    r"(?:Ковенантный период|Covenant period)\s*(?:с|from)\s*(\d{4}-\d{2}-\d{2})\s*(?:по|to)\s*(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)

# Order matters: the first marker that matches wins. Superseded markers are checked
# before their live counterparts so a dead edition can never be classified as live.
MARKERS: list[tuple[str, str, bool]] = [
    ("НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ", "loan_agreement", True),
    ("ДОГОВОР БАНКОВСКОГО ЗАЙМА", "loan_agreement", False),
    ("ПРОМЕЖУТОЧНАЯ ВЕДОМОСТЬ", "aup_report", True),
    # Audit notes cite the AUP report by name, so they must be matched before it.
    ("АУДИТОРСКОЕ ДЕЛО", "audit_notes", False),
    ("ОТЧЁТ О ВЫПОЛНЕНИИ СОГЛАСОВАННЫХ ПРОЦЕДУР", "aup_report", False),
    ("НАДЛЕЖАЩАЯ ПРОВЕРКА КЛИЕНТА", "kyc", False),
    ("КАЗНАЧЕЙСТВО ГРУППЫ", "treasury_memo", False),
]

RELEVANT_KINDS = (
    "loan_agreement",
    "kyc",
    "audit_notes",
    "aup_report",
    "treasury_memo",
    "financial_statements",
)


@dataclass
class Doc:
    doc_id: str
    path: Path
    text: str
    account_id: str | None
    kind: str
    superseded: bool
    period: tuple[str, str] | None

    @property
    def usable(self) -> bool:
        return self.kind in RELEVANT_KINDS and not self.superseded and self.account_id is not None


def to_text(path: Path) -> str:
    """Document -> Markdown.

    anydoc covers PDF plus the Office/OpenDocument/EPUB/CSV families and renders tables as
    GFM, which keeps a name paired with its percentage instead of relying on the two landing
    on the same flattened line. A scanned file raises UnsupportedError; returning empty text
    is the correct answer there, because the image-triage path reads those pages instead.
    """
    try:
        return anydoc.to_markdown(str(path))
    except anydoc.UnsupportedError:
        # Two different things wear this error. A PDF with no text layer really is empty --
        # the image path reads it. Any other format is simply one anydoc does not recognise,
        # and must still be read, or a plain .txt silently becomes a blank document.
        if path.suffix.lower() == ".pdf":
            return ""
    except Exception:
        pass
    if path.suffix.lower() == ".pdf":
        return "\n\n".join(pg.extract_text() or "" for pg in PdfReader(path).pages)
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def extract_text(path: Path, cache_dir: Path) -> str:
    cached = cache_dir / (path.stem + ".txt")
    if cached.exists():
        return cached.read_text()
    text = to_text(path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached.write_text(text)
    return text


CLAUSE_PERIOD_RE = re.compile(r"(?:с|from)\s*(\d{4}-\d{2}-\d{2})\s*(?:по|to)\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)


def covenant_period(text: str) -> tuple[str, str] | None:
    """The period the covenants are tested over.

    The preamble wording is not always present, so fall back to the date range the clauses
    themselves state -- that is what actually governs, and it separates a live edition from a
    superseded one even when the void stamp is missing or worded differently.
    """
    match = PERIOD_RE.search(text)
    if match:
        return match.groups()
    spans = CLAUSE_PERIOD_RE.findall(article_6(text) or text)
    if not spans:
        return None
    return min(a for a, _ in spans), max(b for _, b in spans)


def classify(text: str) -> tuple[str, bool]:
    upper = text.upper()
    for marker, kind, superseded in MARKERS:
        if marker in upper:
            return kind, superseded
    return "other", False


def load_documents(doc_dir: Path, cache_dir: Path, accounts=None) -> list[Doc]:
    acc_re = account_pattern(accounts) if accounts else ACC_RE
    docs = []
    for path in sorted(doc_dir.iterdir()):
        if path.name.startswith(".") or path.name == "Thumbs.db":
            continue
        text = extract_text(path, cache_dir)
        found = sorted(set(acc_re.findall(text)))
        if len(found) > 1:
            raise ValueError(f"{path.name} references multiple accounts: {found}")
        kind, superseded = classify(text)
        period = covenant_period(text)
        docs.append(
            Doc(
                doc_id=path.stem,
                path=path,
                text=text,
                account_id=found[0] if found else None,
                kind=kind,
                superseded=superseded,
                period=period,
            )
        )
    return docs


def route(docs: list[Doc], acc_to_scenario: dict[str, str]) -> dict[str, dict[str, list[Doc]]]:
    routing: dict[str, dict[str, list[Doc]]] = {}
    for doc in docs:
        if not doc.usable:
            continue
        scenario = acc_to_scenario.get(doc.account_id)
        if scenario is None:
            continue
        routing.setdefault(scenario, {}).setdefault(doc.kind, []).append(doc)
    return routing


def routing_problems(routing: dict[str, dict[str, list[Doc]]], scenarios: list[str]) -> list[str]:
    problems = []
    for scenario in scenarios:
        kinds = routing.get(scenario, {})
        agreements = kinds.get("loan_agreement", [])
        if len(agreements) != 1:
            problems.append(f"{scenario}: expected 1 live loan agreement, found {len(agreements)}")
        audits = kinds.get("audit_notes", [])
        if len(audits) != 1:
            problems.append(f"{scenario}: expected 1 final audit, found {len(audits)}")
        # Zero is as wrong as two, and much quieter: without a KYC nothing populates the
        # related-party set, and a covenant reading it quietly computes against nobody.
        if len(kinds.get("kyc", [])) != 1:
            problems.append(f"{scenario}: expected 1 KYC dossier, found {len(kinds.get('kyc', []))}")
    return problems


# Locate the covenant article by what its heading means, not by a fixed number or wording:
# a test-set agreement may number or phrase it differently.
COVENANT_HEADING = re.compile(r"Статья\s+(\d+)\s*[—–-]\s*[^\n]*ковенант", re.IGNORECASE)
CLAUSE_RE = re.compile(r"Пункт\s+(\d+)\.\d+")


def article_6(text: str) -> str:
    """The financial-covenant article, located structurally rather than by literal heading."""
    match = COVENANT_HEADING.search(text)
    if match:
        start = match.start()
        following = re.search(rf"Статья\s+{int(match.group(1)) + 1}\s*[—–-]", text[start:])
        end = start + following.start() if following else start + 8000
        return re.sub(r"\n\d+\n", "\n", text[start:end]).strip()

    # Fallback: the densest run of "Пункт N.N" clauses is the covenant schedule.
    clauses = list(CLAUSE_RE.finditer(text))
    if not clauses:
        return ""
    start = clauses[0].start()
    following = re.search(r"Статья\s+\d+\s*[—–-]", text[clauses[-1].end() :])
    end = clauses[-1].end() + (following.start() if following else 4000)
    return re.sub(r"\n\d+\n", "\n", text[start:end]).strip()


def covenant_supplement(text: str) -> str:
    start = text.find("ДОПОЛНЕНИЕ О СОБЛЮДЕНИИ КОВЕНАНТОВ")
    if start < 0:
        start = text.find("Выводы по классификации")
    if start < 0:
        return ""
    return re.sub(r"\n\d+\n", "\n", text[start : start + 4000]).strip()
