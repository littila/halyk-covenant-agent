# Verified findings

Everything here was confirmed by hand-recomputation against `ground_truth.json`. The pipeline
reproduces **36/36 statuses and 36/36 `actual` values exactly** on the published set.

Sections marked SUPERSEDES correct the initial working brief — a pre-build document, written from
a manual read of the ledger and six sample PDFs, that is not kept in the repository. Sections
marked SELF-CORRECTION correct an earlier conclusion reached during this build.

## Corrections to the initial brief

1. **SUPERSEDES — clause numbers are not archetype-stable.** `6.1` is a ratio for P1 (0.46) and a
   dollar amount for B4 (3,084,375.68) / P8 (4,221,314.95). `6.3` is a ratio for P2/P4/P8/P10 and
   dollars for B1/B4/P1. Archetype must come from clause text, never from the number.
2. **SUPERSEDES — the related-party threshold varies per borrower.** It is not a fixed 20%. Observed:
   20.0 (B1, P1), 25.0 (P2, P3), 30.0 (B4, P4), 32.0 (P7), 34.0 (P9), 35.0 (P5), 36.0 (P10),
   38.0 (P8), 40.0 (P6). Each KYC states its own. Hard-coding 20% breaks 10 of 12 borrowers.
3. **SUPERSEDES — every borrower does have exactly one KYC.** P6's is invisible to text extraction
   because it is a fully scanned document (see "Image-only pages").
4. **SUPERSEDES — no B2/B3 documents exist.** Every document carries at most one `ACC-####`; none
   mentions two. Routing on the account number is unambiguous.
5. **SUPERSEDES — there is a document layer the handoff missed** (the AUP chain, below).
6. Two non-PDF files in `documents/`: `4a5315740e89.csv` (server access log) and `904dea48b34b.txt`
   (archive README, which usefully states the "only the current edition applies" rule). Both decoys.
   `Thumbs.db` is also present.

## Image-only pages — the trap that silently loses data

Four documents carry content as **images with no text layer**. A text-only pipeline reads them as
blank and never knows it lost anything.

| Document | Borrower | What is in the image |
|---|---|---|
| `f3fa6d20c8a1` | P6 | The entire KYC dossier (3 scanned pages), incl. 40.0% threshold |
| `63e162bd710b` p2 | P2 | Ownership table + 25.0% threshold |
| `aaf665cbc612` p2 | P9 | Subsidiary pledge table + 50.0% Unrestricted rule |
| `2ed0b2ee4b57` p4 | P4 | EBITDA one-off add-back table + $300,000 materiality floor |

Detection rule: a page with `len(text.strip()) < 20` and an embedded image over ~20KB. These must be
routed to a vision-capable read. Three of the four feed a covenant directly.

## The audit chain (three hops, not one)

```
loan agreement (live 2025)  -> covenant definitions + thresholds
KYC dossier                 -> ownership %, related-party threshold, subsidiary pledge status
audit notes (АУДИТОРСКОЕ ДЕЛО №ACC-xxxx/2025)
                            -> flags an amount as selected for classification testing,
                               then DEFERS: "вывод изложен в отчёте ... № AR-2025-XXXX"
AUP report (Отчёт о выполнении согласованных процедур, № AR-2025-XXXX)
                            -> the binding reclassification conclusion
```

Two superseded editions must be hard-filtered **before** retrieval:

- `НЕДЕЙСТВУЮЩАЯ РЕДАКЦИЯ (2024 г.)` — dead loan agreement. One per borrower, 12 total.
- `ПРОЕКТ — ПРОМЕЖУТОЧНАЯ ВЕДОМОСТЬ` — dead interim audit schedule, 5 total (B1, P3, P6, P8, P9).
  The AUP states it "заменяет любые промежуточные ведомости".

Rules stated in the documents that generalise:

- Where evidence is inconclusive, the **original classification stands**.
- An adjustment **considered and rejected** is not an adjustment (P10 note 7.2 — and P10 6.1's own
  text says rejected reclassifications are not counted).
- Report numbers **collide across borrowers**: P3's draft `a81217cea0a1` cites the same
  `AR-2025-0634` as B1's real report. Join by account, never by report number alone.

## Ledger semantics

- `counterparty` is noise for categorisation but is the join key for related-party identity.
- 673 of 1473 rows belong to the 12 template borrowers; the rest sit in decoy `ACC-9xxx` accounts.
- **Name normalisation matters.** KYC "Aktau Holdings LLP" vs ledger "Aktau Holdings L.L.P.";
  "Atyrau Holding Group L.L.P." must fold to the same key as "Atyrau Holding Group LLP". Delete
  periods (so `L.L.P.` -> `llp`) and strip legal-form tokens **only from the tail** — "Holding" and
  "Group" are part of the name here, not the legal form.
- **SELF-CORRECTION: "Social tax remittance" is a tax, not payroll.** Its counterparty is often
  payroll-named ("Juniper Payroll Associates") — that is the trap. Typing it as payroll breaks
  B1 6.2 (1,284,663.42 -> 2,748,491.27) and P6 6.2 (3.64 -> 2.58). It becomes payroll only if an
  auditor reclassifies it.
- Missing amounts (`TXN-P7-0033`, `TXN-P8-0031`) are supplied by documents, not coerced to zero:
  486,204.19 from P7's treasury memo, 884,204.16 from P8's audit note 8.1.

### FX: EUR/USD = 1.16, disclosed per borrower and derived in code

No rate table exists anywhere in the corpus. P3's audit discloses a rate implicitly: an invoice of
**72,146.75 EUR settled by a USD payment of $83,690.23** -> exactly 1.160000. Confirmed by P3 6.1
landing on 1.71. The 15 EUR rows are ~10x USD scale, so getting this wrong is destructive.

Three things about how that is now handled, each fixing a real defect:

- **The rate belongs to one borrower, not the corpus.** P3's clause 6.1 requires the rate
  *"раскрытому аудитором"* and its audit note says amounts translate *"по курсу фактического
  расчёта по операциям соответствующего периода"* -- P3's auditor, P3's transactions. Rates are
  therefore keyed per borrower (`{"P3": {"EUR": 1.16}}`); applying P3's auditor's rate to B1's
  EUR insurance row had no basis.
- **Parity is never assumed.** `fx_rates.get(currency, 1.0)` used to treat an undisclosed
  currency as 1:1, silently. A non-USD row with no disclosed rate is now left unconverted and
  flagged, and if a covenant actually reads its category the run says so and names the direction
  of the error. Deleting P3's disclosure produces exactly that warning and moves 6.1 from 1.71 to
  a visibly wrong 1.66, instead of quietly answering 1.66.
- **The model transcribes, code divides.** The rate used to be recovered by regexing a sentence
  the model had already read -- which only matched one wording. The audit schema now carries
  `fx_disclosures`, each with a currency, either a quoted `rate` or a `foreign_amount`/`usd_amount`
  pair, and the verbatim basis; `rates_from_disclosures` does the division. That covers quoted
  rates, settlement pairs and multi-row rate tables without another regex, keeps the arithmetic
  out of the model, and reports conflicting disclosures rather than silently taking one. The raw
  text scan survives only as a last resort, and says so when it fires.

**Only 1 of the 15 non-USD rows affects an answer.** `TXN-P3-0024` (P3's catalyst servicing
contract) is the sole non-USD row whose category any covenant of its borrower reads; the other
fourteen sit in categories their borrowers' covenants ignore. The global-constant design was
therefore never exercised outside the borrower it was disclosed for -- correct by luck, not by
construction.

## SELF-CORRECTION: category aggregates do matter

Early in this build I concluded from B1 and P6 that "операционные расходы" is always a single
designated ledger line and the marketing/insurance/rent/tax families are pure decoys. **That is
wrong as a general rule.** It holds for B1 and P6 only because their covenants happen to reference
single lines. Three covenants aggregate the "noise" families directly:

- P1 6.1 `capex / (opex + lease)`
- P7 6.1 `(taxes + utilities) / EBITDA`
- P10 6.1 `insurance / (lease + utilities)`

So the stem taxonomy and contra-credit netting are load-bearing after all, as the handoff's Trap 2
argued. Contra-credits ("rebate", "returned", "refund", "credit received", "reversal", …) net
against their own expense family; they are not revenue. Revenue is only "… sales settlement".

## Figures that exist in no ledger row

| Value | Source | Feeds |
|---|---|---|
| `severance_liability` 918,447.52 | P8 audit note 7.1 | P8 6.1 |
| `ebitda_addbacks` 824,152.91 | P4 audit image, sum of items >= $300,000 materiality (342,905.28 + 481,247.63; 251,338.94 excluded) | P4 6.1 |
| `group_capex` 21,847,362.55 | Derived, see below | P5 6.1 |

**P5's group capex is a four-hop derivation.** The agreement says Group capex comes from the
*ultimate parent's consolidated statements*. That document (`a5cc1400b640`) is **in English, carries
no `ACC-####`**, and is titled *Sarybel Energy Holding JSC* — linked to P5 only because its segment
note names "Ekibastuz Power Services JSC" (and Sarybel Capital LLP appears in P5's KYC). It gives no
capex line; Note 7 states *"There were no disposals of property, plant and equipment during the
year"*, so additions are recoverable from the roll-forward:

```
154,050,122.81 (closing NBV) - 148,028,989.69 (opening NBV) + 15,826,229.43 (depreciation)
= 21,847,362.55   ->  / EBITDA 2,312,216.15 = 9.45   (GT 9.45, limit 9.00 -> BREACH)
```

## Evidence rule

Plain leave-one-out over all rows is wrong: removing a borrower's only revenue or opex row flips
almost every ratio, so 4-6 rows "qualify" and the determinant is lost. CASE.ru defines evidence as
the row whose *reclassification, inclusion, exclusion or correction* causes the breach, and states
that a row merely contributing to an aggregate is **not** evidence.

Restricting leave-one-out candidates to **rows the documents single out** — auditor-adjusted rows,
plus rows inside an identity-restricted set (related party, unrestricted-subsidiary transfer) —
yields 9/9 correct evidence ids and correctly returns null on all 27 null-key cells.

## RESOLVED: P4 6.3 was a corrupted threshold, not a modelling gap

For most of the build this was the one cell the pipeline got wrong, and it looked like a
contradiction in the data. P4 6.3 and P8 6.3 have textually identical clauses and the same printed
`0.04x` limit:

| | related-party payment | revenue | ratio | GT |
|---|---|---|---|---|
| P4 | 288,417.52 (Aral Capital Partners, 33.4% vs 30.0% threshold) | 7,004,318.47 | 0.041177 | **COMPLIANT** |
| P8 | 342,118.65 (Syrdarya Capital Holding, 44.6% vs 38.0% threshold) | 7,884,663.19 | 0.043391 | **BREACH** |

Both exceed 0.04; both round to 0.04. GT called one compliant and one breached.

**The organizers confirmed the cause: P4's agreement (`3a4aa2dbd6c4.pdf`, ACC-7804) loaded
incorrectly and prints `0.04x` where the covenant is `0.045x`. The answer key held the correct
value.** At 0.045 the cell is compliant, and the training set scores **36/36 on every field**.

Two things this settles.

**The strict comparison was right.** Five candidate rules, scored against all 36 cells with the
document's (wrong) 0.04 in place:

| rule | statuses right | cells wrong |
|---|---|---|
| strict | 35/36 | P4 6.3 |
| compare rounded to 2dp | 35/36 | P8 6.3 |
| tolerance 3% | 34/36 | P2 6.1, P3 6.1 |
| tolerance 5% | 32/36 | + P1 6.2, P5 6.1 |
| tolerance 8% | 28/36 | + four more |

Rescuing P4 needed >=2.95% slack, which flips **P3 6.1 — a genuine breach 0.80% over its limit** —
and P2 6.1 at 1.44% over. Every rule that "fixed" the corrupted cell broke sound ones. Fitting the
outlier would have cost more than it saved, on data that never needed fitting.

**The generalisation rule paid for itself.** Two readings would have fixed P4 without breaking P8 —
adding the EBITDA add-backs to the 6.3 denominator, or applying P4's $300,000 materiality floor to
the payment. Neither had textual support, so neither was adopted. Both would have been wrong, and
both would have shipped to the test set.

One correction to my own earlier write-up: I concluded "the labels are internally contradictory."
The attribution was wrong — the labels were consistent throughout; the *document* was corrupt. The
verdict on which side to trust was right for a slightly wrong reason.

**How the fix is carried.** `cache/source_corrections.json` records the correction with the printed
value, the corrected value, and the issuing authority. `build_facts.apply_corrections` applies it
after extraction and only while the extraction still reads the original figure, so a repaired
document re-reads cleanly rather than being double-patched; the record appears in that cell's
derivation. The extractor is left alone deliberately — its job is to report what the page says, and
here it did that correctly.

## Rules adopted without textual support

**Zero.** The one place a GT-only rule would have paid was P4 6.3 — and it was a corrupted
threshold, so every candidate rule would have been fitted to a typo.

---

# Robustness findings

Everything above is about *this* dataset. This half is about what survives a different one, and
was established by ablation and by breaking things on purpose.

## Extraction is not deterministic — design for it

The same audit note, same prompt, same model, produced two different shapes across runs: once with
the figure in `amount`, once with it only narrated inside `source` prose. The second shape was
unresolvable, the reclassification was silently dropped, and the score fell from 97.22% to 94.44%.

With 12 borrowers this shows up occasionally. On a larger test set it is a recurring tax. Two
defences, in order:

1. **`output_validator` + `ModelRetry`** (`extract.validate_audit`) — an adjustment carrying
   neither `txn_id` nor `amount` is rejected with a specific correction, so the model fixes its
   own output rather than the pipeline losing data.
2. **Code Mode reconciliation** (`reconcile.py`) — whatever still cannot be matched by the
   deterministic rule goes to an agent with ledger-query tools. Verified on the exact failure
   shape: amount buried in Russian prose, no id, no amount field -> resolved `TXN-P2-0040`,
   *"Unique row matching amount 1,104,663.28, counterparty Tien Shan Advisory Bureau, and
   advisory category."*

Note the ordering: deterministic first, model only on the residue. The cheap path stays primary.

## Silent-failure audit

Probing for confident-wrong-answer modes found four. All are now loud:

| Failure | Was | Now |
|---|---|---|
| Unknown name in a formula | `(revenue - opex + rd_expense) / revenue` -> `0.6` | `FormulaError` naming the identifier; cell left null |
| Unseen expense family | Row contributes to nothing, silently | Reported; taxonomy step categorises it first |
| Unusable adjustment | Dropped with a warning nobody reads | `ModelRetry`, then Code Mode reconciliation |
| Converter changes a figure's rendering | Would have scored 0 | Caught by the strict identifier check |

The last one is worth dwelling on: adopting anydoc broke `group_capex_from` because the PP&E
roll-forward became a Markdown table, and the loud check surfaced it as
`unknown identifier 'group_capex'` instead of a plausible wrong ratio. A silent-zero design would
have shipped a wrong P5 6.1 and nobody would have known.

## Generalisation evidence

- **Held-out vocabulary.** 800 decoy `ACC-9xxx` rows, 190 distinct stems, never inspected while
  writing the category rules: **0 uncategorised**. Caveat — the decoys contain no revenue, opex or
  capex rows, so the *signal* categories are untested by this.
- **Signal-vocabulary ablation.** Rewriting B1's four signal descriptions to synonyms no regex
  matches ("Plant upkeep and running expenditure", "Generation capacity turnover recognised",
  "Remuneration disbursement for operational crew", "Grid energy consumption billing") drops all
  four to `uncategorised`. The taxonomy step recovers every one, and B1's three cells then
  reproduce exactly. This is the test the decoy hold-out could not give.
- **Unseen category vocabulary.** The stem classifier maps pension contributions -> `payroll`,
  pilotage and towage -> `freight`, security guarding -> `opex`, and returns `other_expense` when
  genuinely unsure rather than guessing wrong.

## Document conversion

anydoc (Rust, local) replaced pypdf as the primary reader. Three things it bought, and one it did
not:

- **Office formats.** A realistically-compressed `.docx` was previously read as raw ZIP bytes and
  was entirely unrecoverable. If the test set contains any Word/Excel/PowerPoint, that was a total
  blind spot.
- **Structural tables.** `|Ertis Capital, LLP|31.4%|` binds the name to its percentage. pypdf's
  pairing was incidental — it depended on both landing on the same flattened line. Related-party
  thresholds are the densest trap in this dataset, so making that structural is worth real money.
- **Typed scanned detection.** `UnsupportedError: PDF has no extractable text (Scanned, 3 pages)`
  instead of two characters of whitespace.
- **Not OCR.** Image-only PDFs are explicitly unsupported, so it never touches the four hardest
  documents. Vision does that work.

Compatibility was checked before adoption: all 12 agreements still classify, resolve their account,
and locate Article 6; every audit-family document types identically. Zero mismatches.

## Images and mixed-format pages

The original rule extracted images only from pages with under 20 characters of text. On this corpus
it misses nothing — but only by luck: every content image also happens to appear on a near-textless
page. A chart or scanned table pasted into a text-heavy page would have been skipped silently.

`image_pages` now takes any image over 20KB regardless of page text, dedupes by content hash (a PDF
XObject is referenced from every page — P6's dossier was sending 9 images for 3 distinct ones),
caps at 8 per document, and reads `word/media/`, `ppt/media/`, `xl/media/` for Office files. Vision
inputs went 12 -> 6 for identical coverage.

## Verifiability

After Kepler's three pillars (`claude.com/blog/how-kepler-built-verifiable-ai-for-financial-services-with-claude`):

- **Scope determinism** was already the design — the model writes a formula, code evaluates it.
- **Atomic provenance** had exactly one violation. An audit of every numeral in the extracted facts
  found 52 transcribed and 2 arithmetic; one of the two was the model summing P4's add-back items
  to 824,152.91. Now the model transcribes rows and code filters against the materiality floor and
  sums. `verify_numerals` checks every remaining transcription back against the borrower's own
  documents — all 52 verify. (First version of that check matched a bare `"2"` inside `72,146.75`;
  boundary matching fixed it.)
- **Derivation chains** did not exist. `cache/derivations.json` now records the full lineage per
  cell: clause text -> formula -> inputs -> contributing rows -> adjustments with quoted sources ->
  verdict -> evidence basis.

Their sharper point, *citations are post-hoc audit trails that show alleged sources without
verifying correctness*, is why the derivation records the arithmetic and the rows behind it rather
than just a document reference.

## A cache key that omits the prompt makes every prompt change an unverified claim

The extraction cache was keyed on `document text | model | images`. It did **not** include the
instructions or the output schema. Consequences, discovered in that order:

1. Categories were widened from 15 to 20 and the run still scored 97.22% — but the agreement
   extractions were served from cache and **never saw the widened vocabulary**. The result was
   real; it was not evidence for the claim it was cited for.
2. Adding the prompt and the JSON schema to the key forced a genuine re-run. Score: **73.61%**.

The cause was a refactor, not the model. Single-sourcing the vocabulary to `ledger.CATEGORIES`
silently dropped `related_party`, `transfer_unrestricted` and `transfer_restricted` from the
extractor's prompt — those live in `DERIVED`, because they are resolved by *who was paid* rather
than assigned per row. Offered a list without the name it needed, the model reached for the
nearest available: `B4 6.3: transfer_subsidiary + distributions` where the answer is
`related_party`. Twelve of thirty-six cells depend on those three names.

Two lessons, the second more general than the first:

- **`distributions` was a latent trap from the moment it was added.** "Ограниченные платежи" is
  *Restricted Payments*, which in credit-agreement usage means distributions — so the name collides
  semantically with the related-party concept. This violated the third criterion for adding a
  category (distinguishable from its neighbours) that the same commit had just written down. The
  cache hid it for two rounds.
- **Prompt and schema are part of a cached extraction's identity.** Without them in the key, the
  committed facts can silently stop corresponding to the committed prompts, and every "still
  97.22% after that change" is unfalsifiable.

Fixed by restoring the derived names to the extractor's vocabulary with an explicit note that they
resolve by payee identity, including that Ограниченные платежи / Restricted Payments to связанным
сторонам means `related_party`, not `distributions`.

## Extending the vocabulary — two channels

| Concept | Channel | Why |
|---|---|---|
| Flow transacted during the period | `ledger.CATEGORIES` | Aggregated from ledger rows |
| Balance, group total, disclosed liability | `figures` -> `extra` | A transaction ledger cannot produce a balance |

`Net Debt / EBITDA` and minimum-liquidity covenants are unanswerable from categories at any width,
because net debt and cash are positions rather than flows. They need the figures channel, which is
now open: `{name, value, basis}` per figure, verified against the document that states it.

Criteria for a new category — all three, or it is noise: it is a flow appearing as ledger rows; a
covenant could constrain it by name; and it is distinguishable from existing names from the
description alone.

Widening trades a loud failure for a quiet one — an unknown name raises, a known-but-unpopulated
name contributes `0.00` — which is why a covenant referencing an empty category is now reported.

## Effective (look-through) ownership — a trap I missed and the model caught

B4's KYC prints a clean table, and under it a sentence that changes the answer:

> «Доля в Shymkent Fuel Distributors LLP удерживается косвенно через Syr Darya Investment
> Partners LLP; Группе принадлежит 27.3% голосующих прав в Syr Darya Investment Partners LLP.
> Для целей настоящего раздела учитывается **эффективная доля** Группы.»

The table says 48.0%, comfortably over B4's 30.0% threshold. The effective stake is
48.0% x 27.3% = **13.104%**, which is under it. I recorded 48.0% in the hand-written reference and
listed the entity as a related party; the model read the sentence and did not. Only the fact that
this counterparty has no outgoing payments (its four ledger rows are revenue receipts, and
`related_party` counts outflows) kept the reviewed set at 36/36.

Two lessons, in opposite directions:

- **A hand-verified baseline is not ground truth.** It agreed with the key by luck here. Diffing
  the two fact sets is what surfaced it -- the score never moved.
- **The model was right by doing arithmetic it should not have done.** It returned 13.104, a
  number printed nowhere. `Entity` now carries `ownership_pct` (as printed) and an optional
  `held_through_pct`; the look-through multiplication happens in code and is reported. Ownership
  percentages and the related-party threshold are now covered by `verify_numerals`, which they
  were not -- 13.104 would have passed unchecked.

## Two namespaces, one collision

Routing the consolidated report through the model made it transcribe the whole statement,
including group **Revenue $92,418,000** as a figure named `revenue`. `aggregates()` was *adding*
`extra` into category totals, so P5's revenue became 8,214,663.28 + 92,418,000 = 100,632,663.28 and
two cells broke.

A document figure names something the ledger cannot supply; a category names a ledger total. They
are different quantities even when they share a word. A figure colliding with a category name is
now refused and reported, and `extra` sets rather than adds.

## `UnsupportedError` wears two meanings

anydoc raises it both for a scanned PDF with no text layer and for a format it does not recognise.
Collapsing them meant a perfectly readable `.txt` became an empty document, silently. Only the PDF
case is genuinely empty; anything else falls back to a plain read.

## Verification tells absent from unverifiable

A figure read off a scanned page has no text to check against. `verify_numerals` now says
"could not be verified: this borrower has a scanned source with no text layer" rather than
"is not printed", so P6's image-sourced percentages do not read as hallucinations.

## Every regex, checked for how it fails rather than whether it works

Auditing the hardcoded patterns turned up one with no fallback, and it was the load-bearing one.

| pattern | if it fails |
|---|---|
| `COVENANT_HEADING` | falls back to a clause cluster, then to `text[:12000]` |
| `PERIOD_RE` | falls back to clause dates, then warns |
| `CATEGORY_RULES` | falls back to the model classifier, and warns |
| `MONEY_FIGURE` | only gates the density signal |
| `CONTRA_RE` | netting only |
| **`ACC_RE`** | **nothing — and it is the document↔ledger join key** |

The ledger already holds the 561 account ids that exist, so the pattern is built from them,
longest-first with boundaries. Two things came out of that beyond the intended robustness.

**It fixed twelve live misattributions.** The noise documents cite sub-accounts like
`ACC-7801-05`, which is not a ledger account; `ACC-\d{4}` matched the prefix and attributed each
to a borrower. They were typed `other` and dropped, so no answer moved, but the routing was wrong
— and it explains the malformed `ACC-7204-06` the triage agent had been reporting.

**It made the previous fix free.** Those same twelve were what the widened triage was paying to
examine; once they stopped looking routable, extra triage calls fell from 12 to 0.

The shape assumption mattered in its own right: `ACC-\d{4}` reads `ACC-78011` as `ACC-7801`, and
finds nothing at all in `KZ-2025-0417`.

## Document typing is seven Russian substrings, and two failures of it are silent

`classify()` is an uppercase substring test, first match wins. Rewriting one marker per kind to a
plausible alternative another bank's template might use:

| kind | reworded → | reached the model? | noticed? |
|---|---|---|---|
| `loan_agreement` | `other` | no | **loud** — routing asserts exactly 1 |
| `audit_notes` | `other` | no | **loud** — routing asserts exactly 1 |
| `kyc` | `other` | no | **silent** |
| `aup_report` | `other` | no | **silent** |
| `treasury_memo` | `other` | no | **silent** |

None reached the model, because `needs_triage` excluded documents carrying an account number. The
silent ones are the expensive ones: losing every KYC dossier costs 10.00 of 36 by ablation, the
largest single component. Downstream eventually notices — the empty-aggregate warning fires 13
times — but it misdiagnoses, blaming the ledger for a document nobody read.

Routing also asserted `kyc > 1` but not `kyc == 0`, so a duplicate was flagged and an absence was
not.

## Money is not always in dollars

The density gate that finds an unrouted financial statement counted `$` figures only. Rewriting
the consolidated report into forms a Kazakh corpus would plausibly use:

| variant | `$`-only | loosened |
|---|---|---|
| as published (`$`) | 82 | 84 |
| tenge symbol (`₸`) | **0** | 84 |
| tenge code (`тенге`) | **0** | 83 |
| bare grouping | **0** | 79 |

Every non-dollar form scored zero. The looser pattern drops separation from the noisiest unrouted
document from 82x to 8.4x — and inspecting the matches shows that is honest: the documents now
scoring up to 10 are internal policy manuals quoting about ten tenge amounts each, 53 of them,
which the dollar-only pattern could not see at all. The gate sits at 20, so the ceiling is half the
threshold. The asymmetry decides it: a false positive costs one triage call in which the model says
`other`, a false negative loses a document class in silence.

## Batched extraction, with one exception

Extract everything then compute, rather than answering cells on demand. A borrower's documents
serve all three of its clauses, so batching costs 39 extraction calls where per-cell costs 117;
cross-cell facts stay consistent by construction; and the facts file is what makes the
hand-written reference set, the level ladder and the ablation harness possible at all.

The one thing batching cannot do is fetch a quantity the fixed schema did not anticipate. That
case is handled on demand, and the trigger is not a heuristic — the evaluator names the identifier
it cannot resolve. Tested by deleting known figures and re-running:

- `P8 severance_liability`, printed in an audit note → recovered 918,447.52 exact
- `P5 group_capex`, printed nowhere → recovered 21,847,362.55 exact, via an expression the agent
  composed itself with its own part names
- a covenant pointed at `minimum_liquidity_balance`, which no document states → nothing invented,
  gap reported, confidence goes to 1 blocking

## Derived figures were the one class of number nothing checked

`verify_numerals` can only check transcriptions: a derived figure appears in no document by
definition, so it was skipped — leaving `group_capex`, which decides P5 6.1, with no detector.
`verify_derived` now recomputes from the stored expression, requires every part it names to be
printed, and flags a negative result.

That third check earned its place. A sign error is self-consistent — the value follows from the
expression and every part is printed — so neither of the others can see it. The model returning
`ppe_closing - ppe_opening - ppe_depreciation` gives −9,805,096.31 and was caught by nothing until
the sign check went in.

Still uncaught: an expression that drops a term but stays positive.

## An audit trail that was wrong about provenance

Auditing the provenance report itself, not the pipeline, found two errors in it. It labelled
`group_capex` "stated in a document" when no document states it, and named a source file that did
not hold the figure. Both because `report.py` and `explain.py` never ran `resolve_unrouted`, so the
consolidated report — the document supplying the number that decides the cell — was absent from
their view of the corpus entirely; P5's source footer listed three documents instead of four.

The report now distinguishes transcribed from derived, shows the expression with each part and the
file that prints it, and finds that file by searching the routed texts for the value rather than
naming a plausible one.

## What a corpus the code had never seen exposed

Five defects survived a corpus scoring 36.00/36 and a level ladder, a leakage scan, four
perturbations and a from-zero rerun. None is subtle. Each needed an input the published set
does not contain, which is the whole point: a passing score on one corpus measures that corpus.

**A restated amount was always an outflow.** `amount_override` wrote `-abs(amount)`, and an
inflow category only aggregates positive rows, so a restated receipt landed in no aggregate.
Both overrides in the held-out set are receipts. One is a borrower's entire revenue, and its
covenant read BREACH against a floor of zero. Signed by the row's category now.

**A stem the classifier missed cost a borrower 172.9M of revenue.** The classifier records what
it learns and reports what it could not place; one stem came back unmatched and the warning was
demoted to a footnote. Its absence drove a covenant's denominator negative and produced
`actual = 44.09`. The number was absurd on its face and nothing treated absurdity as a signal.

**`total_debt` resolved to nothing.** A ledger records flows, so the debt it can state is
financing drawn less principal repaid — which is how one agreement in the set writes its own
leverage trigger out, in ledger categories, and how another defines it in prose. Four springing
covenants had been answered on `best_effort`, which assumes the covenant sprang. Three now
compute. Assuming a trigger fired is not a conservative default; it is a coin flip that reads
as an answer.

**Silence was read as a finding.** A KYC that prints no ownership threshold still states
relatedness outright, and one that classifies no subsidiary has not placed anything inside the
security perimeter. Both were treated as "nothing qualifies" and answered 0.00 — a claim that no
such payment exists, against a ledger recording several. Four cells.

**Two regexes and a sort key assumed the published set's shape**: a borrower id with a numeric
suffix (`int(name[1:])` raises on `KC`), and a covenant period written in Russian (one agreement
in the set is drafted in English). Neither degrades; both fail outright.

The pattern across all five is the same. Every one is a place where the code answered instead of
refusing, and the answer was indistinguishable from a real one. `confidence` caught four of the
five as blocking findings before any key existed — the zero-valued cells and the lenient
fallbacks — which is what that report is for.
