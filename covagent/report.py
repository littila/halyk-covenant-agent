from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path

from .build_facts import appears_in
from .dataset import Dataset, add_data_argument
from .documents import load_documents, route
from .evaluate import Covenant, FormulaError, evaluate_formula, verdict
from .ledger import apply_adjustments, categorise, load_learned, load_ledger

ROOT = Path(__file__).resolve().parent.parent

KIND_LABEL = {
    "en": {
        "loan_agreement": "loan agreement",
        "audit_notes": "audit file",
        "aup_report": "agreed-upon-procedures report",
        "kyc": "KYC dossier",
        "treasury_memo": "treasury memo",
        "financial_statements": "consolidated report",
    },
    "ru": {
        "loan_agreement": "договор банковского займа",
        "audit_notes": "аудиторское дело",
        "aup_report": "отчёт о выполнении согласованных процедур",
        "kyc": "досье KYC",
        "treasury_memo": "служебная записка казначейства",
        "financial_statements": "консолидированная отчётность",
    },
}

# The documents are already Russian; only the report's own wording needs a second language.
STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Provenance — covenant compliance agent",
        "h1": "Provenance",
        "lede": "Every verdict, and how it was reached: the clause as printed, what the model "
        "returned, the adjustments the auditor requires, the exact ledger rows behind each number, "
        "the arithmetic, and the transaction that decided it.",
        "method": "The model returns a formula and never evaluates it; a walker restricted to "
        "arithmetic, max, min, abs, sum and known category names does that, and an unrecognised "
        "name raises rather than resolving to zero. Sources are live editions — prior-year ones "
        "were filtered before retrieval, and audit joins are scoped by account because report "
        "numbers repeat across borrowers. Evidence candidates are limited to rows the documents "
        "single out, so a row that merely contributes to a total cannot qualify.",
        "meta": "{n} cells &middot; {breach} breach, {ok} compliant &middot; extraction {model} "
        "&middot; corpus {corpus}",
        "filter": "filter: borrower, clause, txn id…",
        "filter_aria": "Filter cells",
        "f_all": "all",
        "f_breach": "breaches",
        "f_ok": "compliant",
        "th_cell": "Cell",
        "th_verdict": "Verdict",
        "th_actual": "Actual",
        "th_limit": "Limit",
        "th_margin": "Margin",
        "th_evidence": "Evidence",
        "breach": "Breach",
        "ok": "Compliant",
        "clause": "clause",
        "against": "against a limit of",
        "before_round": "before rounding to two places",
        "agrees": "agrees with the key",
        "disagrees": "DISAGREES WITH THE KEY",
        "s1": "What the agreement says",
        "src_agreement": "Source: {docs}",
        "no_clause": "clause text not captured",
        "correction": "<b>Correction applied.</b> The document prints <code>{frm}</code> for "
        "<code>{field}</code>; the value used is <code>{to}</code>, on the authority of "
        "{authority}. {reason}",
        "s2": "What the model returned",
        "quarter": "Scoped to quarter {q}; rows outside it are excluded.",
        "trigger": "Conditional covenant. It applies only when <code>{cond}</code> — here that "
        "condition is <b>{state}</b>.",
        "trig_on": "met, so the test applies",
        "trig_off": "not met, so the covenant is complied with by default",
        "s3": "Adjustments the auditor requires",
        "applies": "Code applies:",
        "adj_reclassify": 'move <span class="mono">{txn}</span> into <code>{cat}</code>',
        "adj_exclude": 'exclude <span class="mono">{txn}</span> from the period',
        "adj_override": 'restate <span class="mono">{txn}</span> to {amount}',
        "audited_figure": "the audited figure",
        "elsewhere": "{n} further adjustment{s} made for {scenario} change no number this clause reads.",
        "src_audit": "Source: {docs}",
        "s4": "Where each number comes from",
        "from_doc": "stated in a document",
        "from_derived": "derived, printed nowhere",
        "th_part": "Part",
        "th_printed": "Printed in",
        "th_value": "Value",
        "rows_n": "{n} ledger row{s}",
        "not_in_ledger": "Not a ledger quantity: a balance, group total or disclosed liability. "
        "Transcribed from {where}, and checked back against that page.",
        "derived_from": "No document prints this. It follows from figures that are printed, and the "
        "arithmetic is done in code, never by the model:",
        "a_routed_doc": "a routed document",
        "no_rows": "No ledger row carries this category — it contributed 0.00.",
        "th_txn": "Transaction",
        "th_date": "Date",
        "th_cparty": "Counterparty",
        "th_desc": "Description",
        "th_amount": "Amount",
        "moved": "moved by the auditor",
        "blank_amount": "blank — stated in a document",
        "row_missing": "not found in the ledger",
        "s5": "The arithmetic",
        "reported_as": "Reported as <code>{shown}</code>, because the answer format asks for two "
        "decimal places. The comparison uses the exact value.",
        "compare": "{actual} {op} {threshold} is <b>{truth}</b>, so the covenant is",
        "is_false": "false",
        "is_true": "true",
        "s6": "Which transaction decided it",
        "cf": 'Remove it and the same formula gives <b>{actual}</b> &rarr; <b class="{cls}">{status}</b>.',
        "no_evidence": "No single transaction determines this outcome: {basis}. The answer key "
        "carries <code>null</code> for ratio and aggregate tests, and any value is accepted.",
        "docs_read": "Documents read for {scenario}:",
        "amount_in_doc": "amount stated in a document",
    },
    "ru": {
        "title": "Выкладка — агент проверки ковенантов",
        "h1": "Выкладка",
        "lede": "Каждый вердикт и то, как он получен: пункт договора как он напечатан, что вернула "
        "модель, корректировки аудитора, точные строки леджера за каждым числом, вычисление и "
        "транзакция, определившая результат.",
        "method": "Модель возвращает формулу и никогда её не вычисляет; это делает обходчик, которому "
        "доступны только арифметика, max, min, abs, sum и известные названия статей, причём "
        "неопознанное имя вызывает ошибку, а не превращается в ноль. Источники — действующие "
        "редакции: прошлогодние отброшены до поиска, а связка с аудиторскими отчётами делается в "
        "пределах счёта, потому что их номера повторяются у разных заёмщиков. Круг кандидатов в "
        "доказательства ограничен строками, на которые указывают документы.",
        "meta": "{n} ячеек &middot; {breach} нарушено, {ok} соблюдено &middot; извлечение {model} "
        "&middot; набор {corpus}",
        "filter": "фильтр: заёмщик, пункт, id транзакции…",
        "filter_aria": "Фильтр по ячейкам",
        "f_all": "все",
        "f_breach": "нарушения",
        "f_ok": "соблюдённые",
        "th_cell": "Ячейка",
        "th_verdict": "Вердикт",
        "th_actual": "Значение",
        "th_limit": "Порог",
        "th_margin": "Запас",
        "th_evidence": "Доказательство",
        "breach": "Нарушение",
        "ok": "Соблюдён",
        "clause": "пункт",
        "against": "при пороге",
        "before_round": "до округления до двух знаков",
        "agrees": "совпадает с ключом",
        "disagrees": "РАСХОДИТСЯ С КЛЮЧОМ",
        "s1": "Что сказано в договоре",
        "src_agreement": "Источник: {docs}",
        "no_clause": "текст пункта не сохранён",
        "correction": "<b>Применено исправление.</b> В документе напечатано <code>{frm}</code> "
        "для поля <code>{field}</code>; используется значение <code>{to}</code>, на основании "
        "указания: {authority}. {reason}",
        "s2": "Что вернула модель",
        "quarter": "Ограничено кварталом {q}; строки вне него исключены.",
        "trigger": "Условный ковенант. Применяется, только если <code>{cond}</code> — здесь это "
        "условие <b>{state}</b>.",
        "trig_on": "выполнено, поэтому тест применяется",
        "trig_off": "не выполнено, поэтому ковенант считается соблюдённым",
        "s3": "Корректировки, которых требует аудитор",
        "applies": "Код применяет:",
        "adj_reclassify": 'перенести <span class="mono">{txn}</span> в <code>{cat}</code>',
        "adj_exclude": 'исключить <span class="mono">{txn}</span> из периода',
        "adj_override": 'пересчитать <span class="mono">{txn}</span> до {amount}',
        "audited_figure": "аудированной суммы",
        "elsewhere": "Ещё {n} корректировк{s} для {scenario} не меняют ни одного числа, которое читает этот пункт.",
        "src_audit": "Источник: {docs}",
        "s4": "Откуда взято каждое число",
        "from_doc": "указано в документе",
        "from_derived": "выведено, нигде не напечатано",
        "th_part": "Часть",
        "th_printed": "Напечатано в",
        "th_value": "Значение",
        "rows_n": "{n} строк{s} леджера",
        "not_in_ledger": "Не величина леджера: остаток, показатель группы или раскрытое "
        "обязательство. Переписано из {where} и сверено с той же страницей.",
        "derived_from": "Этой величины нет ни в одном документе. Она следует из напечатанных, "
        "и арифметику делает код, а не модель:",
        "a_routed_doc": "размещённого документа",
        "no_rows": "Ни одна строка леджера не относится к этой статье — вклад 0.00.",
        "th_txn": "Транзакция",
        "th_date": "Дата",
        "th_cparty": "Контрагент",
        "th_desc": "Описание",
        "th_amount": "Сумма",
        "moved": "перенесена аудитором",
        "blank_amount": "пусто — указано в документе",
        "row_missing": "не найдена в леджере",
        "s5": "Вычисление",
        "reported_as": "В ответе указано <code>{shown}</code>: формат требует двух знаков после "
        "запятой. Сравнение выполняется по точному значению.",
        "compare": "{actual} {op} {threshold} — <b>{truth}</b>, поэтому ковенант",
        "is_false": "неверно",
        "is_true": "верно",
        "s6": "Какая транзакция определила результат",
        "cf": 'Уберите её — и та же формула даёт <b>{actual}</b> &rarr; <b class="{cls}">{status}</b>.',
        "no_evidence": "Результат не определяется одной транзакцией: {basis}. В ключе для "
        "коэффициентных и агрегатных тестов стоит <code>null</code>, и принимается любое значение.",
        "docs_read": "Документы, прочитанные для {scenario}:",
        "amount_in_doc": "сумма указана в документе",
    },
}

# The evidence basis is generated in English by the pipeline; it is a fixed vocabulary.
BASIS_RU = {
    "sole document-implicated row whose removal flips the verdict": "единственная строка из числа упомянутых в документах, удаление которой меняет вердикт",
    "0 rows flip the verdict; no single determinant": "ни одна строка не меняет вердикт; единственного определяющего фактора нет",
}


def plural_ru(n: int, forms: tuple[str, str, str]) -> str:
    """Russian needs three endings where English needs two."""
    if 11 <= n % 100 <= 14:
        return forms[2]
    last = n % 10
    if last == 1:
        return forms[0]
    if 2 <= last <= 4:
        return forms[1]
    return forms[2]


def e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def money(value: float) -> str:
    return f"{value:,.2f}"


def figure(value: float) -> str:
    """Ratios need their decimals; dollar amounts do not need four of them."""
    return f"{value:,.2f}" if abs(value) >= 1000 else f"{value:,.4f}".rstrip("0").rstrip(".")


def ledger_index(path: Path) -> dict[str, dict]:
    with path.open() as fh:
        return {row["txn_id"]: row for row in csv.DictReader(fh)}


def counterfactual(spec: dict, raw: dict, clause: str, rows: list, txn_id: str) -> tuple[str, str] | None:
    """Re-run the test with the evidence row removed, so the reader sees both outcomes."""
    covenant = Covenant(
        clause=clause,
        metric=raw["metric"],
        operator=raw["operator"],
        threshold=float(raw["threshold"]),
        quarter=raw.get("quarter"),
        trigger=raw.get("trigger"),
        definition_verbatim=raw.get("definition_verbatim", ""),
    )
    reduced = [t for t in rows if t.txn_id != txn_id]
    try:
        trial = verdict(
            covenant,
            reduced,
            set(spec.get("related_parties", [])),
            spec.get("extra", {}),
            set(spec.get("unrestricted_subsidiaries", [])),
        )
    except (FormulaError, ZeroDivisionError):
        return None
    return figure(abs(trial.actual)), trial.status


def exact_actual(d: dict) -> float:
    """The metric before it is rounded for submission.

    `actual` is reported to two decimal places because the answer format requires it. On a ratio
    test that is destructive -- 0.0434 against a limit of 0.04 rounds to 0.04 and reads as a
    margin of zero -- so anything comparing against the threshold recomputes from the inputs.
    """
    try:
        return abs(evaluate_formula(d["metric"], d["inputs"]))
    except (FormulaError, ZeroDivisionError):
        return d["actual"]


def substituted(metric: str, inputs: dict[str, float]) -> str:
    """The formula with its names replaced by the numbers that went in."""
    out = metric
    for name in sorted(inputs, key=len, reverse=True):
        out = out.replace(name, money(inputs[name]))
    return out


def rows_label(t: dict, lang: str, n: int) -> str:
    if lang == "ru":
        return t["rows_n"].format(n=n, s=plural_ru(n, ("а", "и", "")))
    return t["rows_n"].format(n=n, s="" if n == 1 else "s")


def render_rows(ids: list[str], ledger: dict[str, dict], adjusted: set[str], t: dict) -> str:
    if not ids:
        return f'<p class="none">{t["no_rows"]}</p>'
    head = (
        f'<table class="rows"><thead><tr><th>{t["th_txn"]}</th><th>{t["th_date"]}</th>'
        f"<th>{t['th_cparty']}</th><th>{t['th_desc']}</th>"
        f'<th class="n">{t["th_amount"]}</th></tr></thead><tbody>'
    )
    out = [head]
    for txn_id in ids:
        row = ledger.get(txn_id)
        if row is None:
            out.append(f'<tr><td class="mono">{e(txn_id)}</td><td colspan="4">{t["row_missing"]}</td></tr>')
            continue
        mark = f' <span class="tag">{t["moved"]}</span>' if txn_id in adjusted else ""
        # A blank amount is the dataset's way of saying "this figure is not in the ledger";
        # the value comes from a document instead, and saying so is the point of the report.
        shown = money(abs(float(row["amount"]))) if row["amount"] else f'<span class="cur">{t["blank_amount"]}</span>'
        currency = "" if row["currency"] in ("USD", "") else f' <span class="cur">{e(row["currency"])}</span>'
        out.append(
            f'<tr><td class="mono">{e(txn_id)}{mark}</td><td class="mono">{e(row["date"])}</td>'
            f"<td>{e(row['counterparty'])}</td><td>{e(row['description'])}</td>"
            f'<td class="n">{shown}{currency}</td></tr>'
        )
    out.append("</tbody></table>")
    return "".join(out)


def rounding_note(d: dict, t: dict) -> str:
    exact = exact_actual(d)
    if abs(exact - d["actual"]) < 5e-5:
        return ""
    return f' <span class="cite">({figure(exact)} {t["before_round"]})</span>'


def bears_on(adjustment: dict, d: dict, ledger: dict[str, dict]) -> bool:
    """Does this adjustment change any number this particular test reads?

    The derivation records every adjustment made for the borrower, because they are applied to
    one shared row set. Only some of them touch a given clause. An exclusion is the awkward
    case: the row it removes is gone by the time the cell is derived, so its relevance has to
    come from the category it would otherwise have landed in.
    """
    names = set(d.get("inputs") or {})
    txn_id = adjustment.get("txn_id")
    if txn_id and any(txn_id in ids for ids in (d.get("contributing_rows") or {}).values()):
        return True
    if adjustment.get("to_category") in names:
        return True
    row = ledger.get(txn_id or "")
    return bool(row) and categorise(row["description"]) in names


def render_derivation(expression: str, spec: dict, texts: dict, t: dict) -> str:
    """The parts a derived figure rests on, each with the document that prints it."""
    from .evaluate import referenced_names

    extra = spec.get("extra") or {}
    head = (
        f'<table class="rows"><thead><tr><th>{t["th_part"]}</th><th></th><th></th>'
        f'<th>{t["th_printed"]}</th><th class="n">{t["th_value"]}</th></tr></thead><tbody>'
    )
    out = [f'<p class="formula">{e(expression)}</p>', head]
    for part in sorted(referenced_names(expression)):
        val = extra.get(part)
        where = (
            ", ".join(f"{x}.pdf" for x in sorted(texts) if appears_in(float(val), texts[x])) if val is not None else ""
        )
        shown = money(float(val)) if val is not None else "—"
        out.append(
            f'<tr><td class="mono">{e(part)}</td><td></td><td></td>'
            f'<td class="mono">{e(where)}</td><td class="n">{shown}</td></tr>'
        )
    out.append("</tbody></table>")
    return "".join(out)


def render_cell(scenario, clause, d, spec, ledger, rows, docs, key, t, lang, texts) -> str:
    breach = d["status"] == "BREACH"
    adjusted = {a["txn_id"] for a in d.get("adjustments_applied") or [] if a.get("txn_id")}
    files = lambda ids: ", ".join(e(x) + ".pdf" for x in ids)
    out = [f'<article class="cell" id="{e(scenario)}-{e(clause)}" data-status="{e(d["status"])}">']

    out.append(
        f'<header class="cellhead"><h2>{e(scenario)} &middot; {t["clause"]} {e(clause)}</h2>'
        f'<span class="verdict {"breach" if breach else "ok"}">{t["breach"] if breach else t["ok"]}</span>'
        f'<span class="headline"><b>{figure(d["actual"])}</b> {t["against"]} '
        f"{figure(d['threshold'])}{rounding_note(d, t)}</span>"
    )
    if key:
        agree = d["status"] == key["status"] and abs(d["actual"] - (key["actual"] or 0)) < 0.005
        out.append(
            f'<span class="key {"match" if agree else "mismatch"}">{t["agrees"] if agree else t["disagrees"]}</span>'
        )
    out.append("</header>")

    # 1 — the clause
    out.append(f'<section class="step"><h3><span class="n">1</span> {t["s1"]}</h3>')
    agreements = docs.get("loan_agreement", [])
    cite = f'<p class="cite">{t["src_agreement"].format(docs=files(agreements))}</p>' if agreements else ""
    body = e(d["clause_text"]) or f"<em>{t['no_clause']}</em>"
    out.append(f"<blockquote>{body}</blockquote>{cite}")
    if d.get("source_correction"):
        c = d["source_correction"]
        out.append(
            '<p class="correction">'
            + t["correction"].format(
                frm=e(c["from"]),
                field=e(c["field"]),
                to=e(c["to"]),
                authority=e(c["authority"]),
                reason=e(c["reason"]),
            )
            + "</p>"
        )
    out.append("</section>")

    # 2 — the model's reading
    out.append(f'<section class="step"><h3><span class="n">2</span> {t["s2"]}</h3>')
    out.append(
        f'<p class="formula">{e(d["metric"])} &nbsp;<span class="op">{e(d["operator"])}</span>&nbsp; '
        f"{figure(d['threshold'])}</p>"
    )
    if d.get("quarter"):
        out.append(f'<p class="cite">{t["quarter"].format(q=e(d["quarter"]))}</p>')
    if d.get("trigger"):
        trig = d["trigger"]
        cond = f"{e(trig['metric'])} {e(trig['operator'])} {figure(float(trig['value']))}"
        state = t["trig_on"] if d.get("trigger_active") else t["trig_off"]
        out.append(f'<p class="cite">{t["trigger"].format(cond=cond, state=state)}</p>')
    out.append("</section>")

    # 3 — adjustments. Only those that change a number this clause reads; the rest belong to
    # the borrower's other covenants and would be noise here.
    every = d.get("adjustments_applied") or []
    adjustments = [a for a in every if bears_on(a, d, ledger)]
    elsewhere = len(every) - len(adjustments)
    if adjustments:
        out.append(f'<section class="step"><h3><span class="n">3</span> {t["s3"]}</h3>')
        aup = docs.get("aup_report", []) + docs.get("audit_notes", [])
        for a in adjustments:
            amount = money(float(a["amount"])) if a.get("amount") else t["audited_figure"]
            what = {
                "reclassify": t["adj_reclassify"].format(txn=e(a.get("txn_id")), cat=e(a.get("to_category"))),
                "exclude": t["adj_exclude"].format(txn=e(a.get("txn_id"))),
                "amount_override": t["adj_override"].format(txn=e(a.get("txn_id")), amount=amount),
            }.get(a["kind"], e(a["kind"]))
            out.append(f'<p class="adj"><b>{t["applies"]}</b> {what}.</p>')
            if a.get("source"):
                out.append(f'<blockquote class="small">{e(a["source"])}</blockquote>')
        if elsewhere:
            suffix = plural_ru(elsewhere, ("а", "и", "")) if lang == "ru" else ("s" if elsewhere > 1 else "")
            out.append('<p class="cite">' + t["elsewhere"].format(n=elsewhere, s=suffix, scenario=e(scenario)) + "</p>")
        if aup:
            out.append(f'<p class="cite">{t["src_audit"].format(docs=files(aup))}</p>')
        out.append("</section>")

    # 4 — inputs
    step = 4 if adjustments else 3
    out.append(f'<section class="step"><h3><span class="n">{step}</span> {t["s4"]}</h3>')
    for name in sorted(d["inputs"]):
        value = d["inputs"][name]
        ids = (d.get("contributing_rows") or {}).get(name) or []
        from_doc = name in (d.get("scalars_from_documents") or {})
        derived = from_doc and name in (spec.get("derived_figures") or {})
        src = (t["from_derived"] if derived else t["from_doc"]) if from_doc else rows_label(t, lang, len(ids))
        out.append(
            f'<div class="input"><div class="inputhead"><code>{e(name)}</code>'
            f'<span class="val">{money(value)}</span>'
            f'<span class="src">{src}</span></div>'
        )
        if from_doc:
            expression = (spec.get("derived_figures") or {}).get(name)
            if expression:
                out.append(f'<p class="none">{t["derived_from"]}</p>')
                out.append(render_derivation(expression, spec, texts, t))
            else:
                held = [x for x in sorted({i for v in docs.values() for i in v}) if appears_in(value, texts.get(x, ""))]
                where = ", ".join(e(x) + ".pdf" for x in held) or t["a_routed_doc"]
                out.append(f'<p class="none">{t["not_in_ledger"].format(where=where)}</p>')
        else:
            out.append(render_rows(ids, ledger, adjusted, t))
        out.append("</div>")
    out.append("</section>")

    # 5 — the arithmetic
    exact = exact_actual(d)
    step += 1
    out.append(f'<section class="step"><h3><span class="n">{step}</span> {t["s5"]}</h3>')
    rounded = (
        f'<p class="cite">{t["reported_as"].format(shown=figure(d["actual"]))}</p>'
        if abs(exact - d["actual"]) >= 5e-5
        else ""
    )
    compare = t["compare"].format(
        actual=figure(exact),
        op=e(d["operator"]),
        threshold=figure(d["threshold"]),
        truth=t["is_false"] if breach else t["is_true"],
    )
    out.append(
        f'<p class="formula sub">{e(substituted(d["metric"], d["inputs"]))}</p>'
        f'<p class="formula">= <b>{figure(exact)}</b></p>'
        + rounded
        + f'<p>{compare} <b class="{"breach" if breach else "ok"}">'
        f"{t['breach'] if breach else t['ok']}</b>.</p>"
    )
    out.append("</section>")

    # 6 — evidence
    step += 1
    basis = d["evidence_basis"]
    if lang == "ru":
        basis = BASIS_RU.get(basis, basis)
    out.append(f'<section class="step"><h3><span class="n">{step}</span> {t["s6"]}</h3>')
    if d.get("evidence_txn_id"):
        row = ledger.get(d["evidence_txn_id"], {})
        cf = counterfactual(
            spec,
            {
                "metric": d["metric"],
                "operator": d["operator"],
                "threshold": d["threshold"],
                "quarter": d.get("quarter"),
                "trigger": d.get("trigger"),
            },
            clause,
            rows,
            d["evidence_txn_id"],
        )
        amount = money(abs(float(row["amount"]))) if row.get("amount") else t["amount_in_doc"]
        out.append(
            f'<p><span class="mono big">{e(d["evidence_txn_id"])}</span> &mdash; '
            f"{e(row.get('description', ''))}, {e(row.get('counterparty', ''))}, {amount}.</p>"
        )
        if cf:
            status = t["breach"] if cf[1] == "BREACH" else t["ok"]
            out.append(
                '<p class="cf">'
                + t["cf"].format(actual=cf[0], cls="breach" if cf[1] == "BREACH" else "ok", status=status)
                + "</p>"
            )
        out.append(f'<p class="cite">{e(basis)}.</p>')
    else:
        out.append(f'<p class="none">{t["no_evidence"].format(basis=e(basis))}</p>')
    out.append("</section>")

    # sources
    if docs:
        labels = KIND_LABEL[lang]
        listed = " &nbsp;·&nbsp; ".join(
            f"{labels.get(k, k)}: " + ", ".join(f'<span class="mono">{e(x)}.pdf</span>' for x in v)
            for k, v in sorted(docs.items())
        )
        out.append(f'<footer class="sources"><b>{t["docs_read"].format(scenario=e(scenario))}</b> {listed}</footer>')

    out.append("</article>")
    return "".join(out)


CSS = """
:root{--paper:#fffdf7;--raised:#faf7ee;--ink:#16150f;--soft:#4a473d;--faint:#8b877a;
--rule:#ddd8c8;--hair:#ece7d9;--accent:#8c2f18;--ok:#1d6b3f;--okbg:#eef5f0;--brbg:#fbf0ea;
--serif:Georgia,"Iowan Old Style","Palatino Linotype",Palatino,serif;
--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--paper:#15140f;--raised:#1d1c16;
--ink:#ebe7da;--soft:#a9a496;--faint:#7d7a6d;--rule:#37342b;--hair:#282620;--accent:#e5825c;
--ok:#6cc48d;--okbg:#18251d;--brbg:#2a1a13}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--serif);font-size:16.5px;
line-height:1.6;-webkit-font-smoothing:antialiased}
.sheet{max-width:1080px;margin:0 auto;padding:0 26px 120px}
header.top{padding:64px 0 26px;border-bottom:1px solid var(--ink);margin-bottom:30px}
h1{font-size:36px;font-weight:400;letter-spacing:-.02em;margin:0 0 12px}
.lede{color:var(--soft);font-size:18px;margin:0 0 18px;max-width:62ch}
.method{font-size:14.5px;color:var(--faint);margin:0 0 20px;max-width:78ch;line-height:1.55}
.meta{font-family:var(--mono);font-size:12.5px;color:var(--faint);line-height:1.7}
.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:26px 0 8px}
.controls input,.controls button{font-family:var(--mono);font-size:13px;padding:7px 12px;
border:1px solid var(--rule);border-radius:3px;background:var(--raised);color:var(--ink)}
.controls button{cursor:pointer}
.controls button[aria-pressed=true]{background:var(--ink);color:var(--paper);border-color:var(--ink)}
table.index{border-collapse:collapse;width:100%;font-size:14.5px;margin-bottom:14px;
font-variant-numeric:tabular-nums lining-nums}
table.index th{text-align:left;font-size:12px;color:var(--soft);padding:0 14px 6px 0;
border-bottom:1px solid var(--ink);white-space:nowrap}
table.index td{padding:6px 14px 6px 0;border-bottom:1px solid var(--hair)}
table.index td.n,table.index th.n{text-align:right;padding-right:18px;font-family:var(--mono);font-size:13px}
table.index td:last-child,table.index th:last-child{padding-left:4px}
table.index a{color:var(--ink);text-decoration:none;font-family:var(--mono);font-size:13px;white-space:nowrap}
table.index td:first-child{white-space:nowrap}
table.index a:hover{color:var(--accent);text-decoration:underline}
tr.breach td.st{color:var(--accent)}
tr.ok td.st{color:var(--ok)}
article.cell{border-top:1px solid var(--ink);padding:30px 0 10px;margin-top:34px;scroll-margin-top:14px}
.cellhead{display:flex;flex-wrap:wrap;gap:12px;align-items:baseline;margin-bottom:22px}
.cellhead h2{font-size:24px;font-weight:400;margin:0;letter-spacing:-.01em}
.verdict{font-family:var(--mono);font-size:11.5px;letter-spacing:.08em;padding:3px 9px;border-radius:3px}
.verdict.breach{background:var(--brbg);color:var(--accent);border:1px solid var(--accent)}
.verdict.ok{background:var(--okbg);color:var(--ok);border:1px solid var(--ok)}
.headline{color:var(--soft);font-size:15.5px}
.headline b{color:var(--ink);font-family:var(--mono);font-size:15px}
.key{font-family:var(--mono);font-size:11.5px;color:var(--faint)}
.key.mismatch{color:var(--accent);font-weight:700}
.step{margin:0 0 24px;padding-left:40px;position:relative}
.step h3{font-size:16.5px;font-weight:700;margin:0 0 10px}
.step h3 .n{position:absolute;left:0;font-family:var(--mono);font-size:12px;color:var(--faint);
border:1px solid var(--rule);border-radius:50%;width:24px;height:24px;line-height:22px;
text-align:center;display:inline-block}
blockquote{margin:0 0 10px;padding:12px 16px;background:var(--raised);border-left:1px solid var(--rule);
font-size:15.5px;line-height:1.55}
blockquote.small{font-size:14.5px;color:var(--soft)}
.cite{font-size:13.5px;color:var(--faint);margin:0 0 12px}
.correction{font-size:14.5px;background:var(--brbg);padding:10px 14px;border-radius:3px;margin:10px 0}
.formula{font-family:var(--mono);font-size:15px;margin:6px 0;overflow-x:auto}
.formula.sub{color:var(--soft);font-size:13.5px}
.formula .op{color:var(--accent)}
.adj{margin:0 0 6px;font-size:15.5px}
.input{margin:0 0 16px}
.inputhead{display:flex;gap:12px;align-items:baseline;padding-bottom:5px;border-bottom:1px solid var(--hair)}
.inputhead code{font-size:14px}
.inputhead .val{font-family:var(--mono);font-size:14px;margin-left:auto}
.inputhead .src{font-size:12.5px;color:var(--faint);min-width:9em;text-align:right}
table.rows{border-collapse:collapse;width:100%;font-size:13.5px;margin:6px 0 0;
font-variant-numeric:tabular-nums lining-nums}
table.rows th{text-align:left;font-size:11.5px;color:var(--faint);font-weight:400;padding:4px 12px 4px 0}
table.rows td{padding:4px 12px 4px 0;border-top:1px solid var(--hair);vertical-align:top}
table.rows td.n,table.rows th.n{text-align:right;padding-right:0;font-family:var(--mono)}
.tag{font-family:var(--mono);font-size:10.5px;color:var(--accent);border:1px solid var(--accent);
border-radius:2px;padding:0 4px;white-space:nowrap}
.cur{color:var(--accent);font-size:11px}
.none{font-size:14px;color:var(--faint);margin:6px 0 0}
.cf{background:var(--raised);padding:11px 15px;border-radius:3px;font-size:15.5px;margin:8px 0}
.big{font-size:15px}
b.breach{color:var(--accent)}b.ok{color:var(--ok)}
.mono,code{font-family:var(--mono);font-size:.86em}
.sources{font-size:13px;color:var(--faint);border-top:1px solid var(--hair);padding-top:12px;margin-top:20px}
.sources b{color:var(--soft);font-weight:400}
@media(max-width:720px){.step{padding-left:0}.step h3 .n{position:static;margin-right:8px}
.inputhead{flex-wrap:wrap}.inputhead .src{text-align:left;min-width:0}}
"""

JS = """
const q=document.getElementById('q'),cells=[...document.querySelectorAll('article.cell')],
rows=[...document.querySelectorAll('tr.idx')],btns=[...document.querySelectorAll('button[data-f]')];
let filter='all';
function apply(){const t=q.value.trim().toUpperCase();
 for(const el of [...cells,...rows]){
  const k=(el.dataset.key||'')+' '+(el.textContent||'').toUpperCase();
  const okF=filter==='all'||el.dataset.status===filter;
  el.hidden=!(okF&&(!t||k.includes(t)));}}
q.addEventListener('input',apply);
for(const b of btns)b.addEventListener('click',()=>{filter=b.dataset.f;
 for(const o of btns)o.setAttribute('aria-pressed',String(o===b));apply();});
apply();
"""


def build_page(derivations, facts, ledger, routing, gt, txns_by_scenario, model, corpus, lang, texts) -> str:
    t = STRINGS[lang]
    order = sorted(derivations, key=lambda x: (x[0], int(x[1:])))
    index, body = [], []
    breaches = 0
    for scenario in order:
        for clause in sorted(derivations[scenario]):
            d = derivations[scenario][clause]
            key = (gt.get(scenario, {}).get("covenants", {}) or {}).get(clause) if gt else None
            spec = facts["scenarios"].get(scenario, {})
            docs = {k: [x.doc_id for x in v] for k, v in sorted(routing.get(scenario, {}).items())}
            breach = d["status"] == "BREACH"
            breaches += breach
            exact = exact_actual(d)
            slack = (exact - d["threshold"]) / d["threshold"] if d["threshold"] else 0
            if d["operator"].startswith("<"):
                slack = -slack
            index.append(
                f'<tr class="idx {"breach" if breach else "ok"}" data-status="{d["status"]}" '
                f'data-key="{e(scenario)} {e(clause)}">'
                f'<td><a href="#{e(scenario)}-{e(clause)}">{e(scenario)} {e(clause)}</a></td>'
                f'<td class="st">{t["breach"] if breach else t["ok"]}</td>'
                f'<td class="n">{figure(d["actual"])}</td><td class="n">{figure(d["threshold"])}</td>'
                f'<td class="n">{slack * 100:+.2f}%</td>'
                f'<td class="mono">{e(d["evidence_txn_id"] or "—")}</td></tr>'
            )
            body.append(
                render_cell(
                    scenario,
                    clause,
                    d,
                    spec,
                    ledger,
                    txns_by_scenario.get(scenario, []),
                    docs,
                    key,
                    t,
                    lang,
                    texts,
                )
            )

    total = len(index)
    meta = t["meta"].format(n=total, breach=breaches, ok=total - breaches, model=e(model) or "n/a", corpus=e(corpus))
    return f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t["title"]}</title>
<style>{CSS}</style></head><body><div class="sheet">
<header class="top">
<h1>{t["h1"]}</h1>
<p class="lede">{t["lede"]}</p>
<p class="method">{t["method"]}</p>
<p class="meta">{meta}</p>
</header>
<div class="controls">
<input id="q" type="search" placeholder="{t["filter"]}" aria-label="{t["filter_aria"]}">
<button data-f="all" aria-pressed="true">{t["f_all"]}</button>
<button data-f="BREACH" aria-pressed="false">{t["f_breach"]}</button>
<button data-f="COMPLIANT" aria-pressed="false">{t["f_ok"]}</button>
</div>
<table class="index"><thead><tr><th>{t["th_cell"]}</th><th>{t["th_verdict"]}</th>
<th class="n">{t["th_actual"]}</th><th class="n">{t["th_limit"]}</th>
<th class="n">{t["th_margin"]}</th><th>{t["th_evidence"]}</th></tr></thead>
<tbody>{"".join(index)}</tbody></table>
{"".join(body)}
</div><script>{JS}</script></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Write an HTML provenance report for every cell")
    parser.add_argument("--derivations", default=None, help="defaults to <corpus workdir>/derivations.json")
    parser.add_argument("--facts", default=None, help="defaults to <corpus workdir>/facts_extracted.json")
    parser.add_argument("--lang", default="en", choices=sorted(STRINGS))
    parser.add_argument("--out", default=None, help="defaults to <corpus workdir>/provenance[.<lang>].html")
    add_data_argument(parser)
    args = parser.parse_args()

    data = Dataset(Path(args.data))
    data.check()
    derivations = json.loads((Path(args.derivations) if args.derivations else data.artefact("derivations.json")).read_text())
    facts = json.loads((Path(args.facts) if args.facts else data.artefact("facts_extracted.json")).read_text())
    gt = json.loads(data.ground_truth.read_text())["scenarios"] if data.ground_truth.exists() else {}

    from .build_facts import account_map, resolve_unrouted

    load_learned(data.artefact("stem_categories.json"))
    ledger = ledger_index(data.ledger)
    accounts = account_map(data)
    docs = load_documents(data.documents, data.artefact("text"), accounts)
    # Same second look the build does, so a document attached by reading it -- a scan, or a
    # group report that prints no account -- is listed as a source rather than silently absent.
    resolve_unrouted(docs, facts.get("extraction_model") or "anthropic:claude-opus-5")
    routing = route(docs, accounts)
    txns = load_ledger(data.ledger, set(derivations), facts["fx_rates"])
    by_scenario = {}
    for scenario in derivations:
        rows = [x for x in txns if x.scenario == scenario]
        spec = facts["scenarios"].get(scenario, {})
        by_scenario[scenario] = apply_adjustments(rows, spec.get("adjustments", []))

    page = build_page(
        derivations,
        facts,
        ledger,
        routing,
        gt,
        by_scenario,
        facts.get("extraction_model", ""),
        data.root.name,
        args.lang,
        {x.doc_id: x.text for v in routing.values() for ds in v.values() for x in ds},
    )
    suffix = "" if args.lang == "en" else f".{args.lang}"
    out = Path(args.out) if args.out else data.artefact(f"provenance{suffix}.html")
    out.write_text(page)
    print(f"wrote {out}  ({len(page) // 1024} KB, {sum(len(v) for v in derivations.values())} cells, {args.lang})")


if __name__ == "__main__":
    main()
