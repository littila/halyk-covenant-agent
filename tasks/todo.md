# Covenant agent — build log

## Result

**100.00% on the published data** — 36/36 statuses, 36/36 `actual` exact, 9/9 evidence ids,
with one owner-issued source correction applied (P4's agreement prints a typo'd threshold).
Rules adopted without textual support: **0**.

**The held-out corpus is answered and unscoreable.** 27 borrowers, 84 cells, three clause layouts
(`6.1–6.3`, `6.1–6.4`, `5.1–5.3`), one agreement drafted in English, no answer key. What is known
without one: 84/84 answered, no `actual` of zero, none answered around its formula, 21 naming a
determining transaction, and an independent replay from facts plus ledger reproducing the file
byte for byte. One blocking finding stands — X2 6.3, where two rows each carry the verdict alone.

That corpus found five defects the published one cannot reach, because each needs an input it does
not contain: a restated receipt signed as an outflow, debt named where no document states a
balance, add-backs unknown rather than nil, a KYC's silence read as a finding, and a borrower id
with no numeric suffix. Then a second pass found five clauses testing a quarter that were read as
testing the period, and a dossier stating a subsidiary designation in prose that the schema could
not express. Both rounds are written up in `findings.md`.

Confirmed on repeated cache-cleared runs: extraction varies in how it quotes a source, never in
what it asserts. Three clean re-runs, semantic diff zero each time, submission byte-identical.

The published data holds 36 covenant clauses in the 12 live agreements — 6.1–6.3 each — and the
answer key covers all 36. Article 25 is final provisions, not covenants. There is no untested
covenant in the corpus.

Both fact sets produce an identical submission: `cache/facts.json` (hand-verified, kept as a
regression baseline) and `cache/facts_extracted.json` (what the model reads from the documents).

## Done

**Pipeline**
- [x] Stage 0 triage: anydoc/pypdf text, doc typing, superseded hard-filter, account routing
- [x] Account ids derived from the ledger rather than assumed as `ACC-\d{4}` — also fixed 12
      misattributions where a sub-account reference matched the prefix of a real account
- [x] A document that routes to a borrower but matches no type marker goes to the model, so a
      corpus with different headings cannot lose a whole class in silence
- [x] Money recognised by symbol, currency code or digit grouping, not `$` alone
- [x] Routing assertions (1 live agreement / 1 final audit / 1 KYC per borrower)
- [x] Stage 1 extraction: Pydantic AI agents on `claude-opus-5`, Pydantic `output_type`, cached by
      content hash, vision for scanned pages
- [x] Stage 2 ledger: 23-category vocabulary, contra-credit netting, FX, auditor adjustments
- [x] Stage 3 generic formula evaluator (whitelisted AST) replacing per-archetype evaluators
- [x] Stage 4 evidence via restricted leave-one-out
- [x] Second pass: when a formula names a quantity nothing supplies, one targeted retrieval per
      borrower with that gap; a quantity genuinely absent comes back `found=false` and blocks
- [x] `submission.json` writer with template key-set validation
- [x] Scoring harness decomposed into status / actual / evidence, with a failure table

**Robustness**
- [x] Strict identifier resolution — unknown names raise instead of resolving to `0.0`
- [x] Uncategorised stems reported rather than silently contributing nothing
- [x] Model-assisted stem taxonomy for unseen ledger vocabulary (`taxonomy.py`)
- [x] `output_validator` + `ModelRetry` on audit extraction
- [x] Code Mode reconciliation for adjustments the deterministic rule cannot place (`reconcile.py`)
- [x] Structural article location (survives renumbering / rewording)
- [x] Image handling: content-hash dedup, mixed pages, Office media, true media types

**Verifiability**
- [x] Model performs no arithmetic — P4's add-back total moved into code
- [x] `verify_numerals`: every transcribed figure checked against its source documents (52/52)
- [x] `cache/derivations.json` — one full lineage record per cell
- [x] `cache/source_corrections.json` — owner-issued corrections to wrong figures in the documents,
      applied after extraction, guarded against double-patching, shown in the derivation
- [x] Cache key covers prompt + output schema, so editing a prompt re-runs its extractions
- [x] Two-channel vocabulary: flows as categories (23), document-stated figures as an open namespace
- [x] Warning when a formula references a category no ledger row populates
- [x] `verify_derived`: a computed figure is recomputed from its stored expression, every part it
      names must itself be printed, and a negative result is flagged as a probable sign error
- [x] Ownership read as printed plus `held_through_pct`; the look-through product done in code
- [x] `max_quarterly(x)` / `min_quarterly(x)` — a covenant on the extreme quarter evaluates its
      expression once per quarter and reduces, over the quarters the ledger records only
- [x] A subsidiary designation stated in prose is read as authoritative over the pledge test, and
      a KYC declaring relatedness without printing a threshold is not read as declaring nothing
- [x] A restated amount is signed by its category, so a restated receipt is not booked as an outflow
- [x] `total_debt` / `net_debt` resolved from the ledger as financing drawn less principal repaid,
      which is how the agreements that use the term define it

**Portability**
- [x] `--data` on all seven commands; the ledger is found by pattern, `ground_truth.json` is
      optional, and a missing corpus fails by name rather than writing an empty submission
- [x] Dry-run against a simulated held-out corpus: renamed ledger, no answer key
- [x] `team` / `contact_email` taken from the template, which is where the organisers ask for them
- [x] One directory per corpus under `cache/`, none privileged, and nothing written outside it —
      the published corpus had been the exception, and it is the one every command touches
- [x] No run writes the submitted answer by default; the root path is reached only by `--out`
- [x] `report` and `explain` scope the extraction cache to the corpus before reading documents
      with the model, instead of recreating the shared directory
- [x] Source corrections live beside the corpus they correct and name the document they are about
- [x] Borrower ids sorted without assuming a numeric suffix (`KC` raised on `int(name[1:])`)
- [x] Covenant periods parsed in English as well as Russian
- [x] A borrower the ledger never mentions is reported, with the identifiers the ledger does carry
- [x] Real held-out run: 307 documents, 27 borrowers, 84 cells, keyless

**Tools for a corpus with no answer key**
- [x] `covagent.confidence` — the checks that stand in for a score, sorted blocking / look-at /
      information, including two shape checks per-cell tests cannot see
- [x] `covagent.ablate` — prices each component by removing it; runs the L1–L6 ladder
- [x] `covagent.levels` + `--level` — degrades the facts, never the code, so the ladder measures
      evidence rather than implementation
- [x] `covagent.perturb` — synonym-swapped descriptions, undisclosed currency, shuffled rows, 6x
      decoy volume; no re-extraction, so no API calls
- [x] `covagent.report` — the HTML provenance page, `--lang ru|en`

**Docs**
- [x] `docs/solution.html` — architecture only, in Russian: the seam, six stages, who decides
      what, two namespaces, behaviour under uncertainty, limits. No results in it.
- [x] `cache/<corpus>/provenance.ru.html` / `.html` — every verdict traced from the printed clause
      to the transaction that decided it, with the per-quarter split where a covenant reads one
- [x] `tasks/findings.md` — dataset findings and robustness findings
- [x] `README.md` — Russian; the operational entry point, with every command verified against
      `--help`

## Open

- [ ] **A wrong expression that yields a positive number.** `verify_derived` catches a changed
      value, an unprinted part and a sign error. A derivation that drops a term but stays positive
      — `ppe_closing - ppe_opening` without the depreciation charge — is self-consistent and has no
      mechanical detector. Only the `basis` the model quotes, shown in the provenance report, stands
      between it and a wrong answer. A second opinion would share the first one's failure mode.
- [x] ~~The trigger-not-met branch has never executed.~~ Closed by the held-out corpus: of its 12
      springing covenants, four have dormant triggers, so the "condition not met, complied with by
      default" path now runs end to end on real data.
- [ ] **Figure names are an open namespace.** A formula may say `net_debt` while the extractor
      reported `total_net_debt`. The second pass now recovers this by going to look for the name the
      formula used, but the orphan stays in `extra` and nothing reconciles the two.
- [ ] **Arbitrary date-range periods** — `Covenant` supports quarter filtering only.
- [ ] **Company matching is `JSC`-only.** `link_by_company` builds its name index from a regex ending
      in ` JSC`, so a borrower incorporated as LLP or TOO would never enter it. The failure is loud
      (*names X, which matches no borrower*) but the fix is a one-line suffix list.
- [ ] **A consolidated report naming two borrowers** attaches to whichever name resolves first, and
      warns about neither.
- [ ] **The agreement prompt is one shared surface.** Editing it invalidates all 27 agreement
      readings, so any wording change can move any of the 84 cells, and two attempts did — one
      inverting a metric and its trigger, one flipping a trigger's operator. There is no way to
      re-read a single borrower. The diff-every-cell-and-adjudicate discipline is the control, and
      it is manual.
- [ ] **The prompt is narrower than the evaluator.** It tells the model `max_quarterly` takes a
      bare category name; the evaluator accepts any expression, which is what J1's clause needs.
      Conservative in the safe direction, but the two should agree, and fixing the wording costs
      a full re-extraction.
- [ ] **X2 6.3 has no single determining row.** Two transactions each flip the verdict alone. The
      larger is named and the basis line says so, which is the best available answer rather than a
      correct one.
- [ ] **`min_quarterly` over a category with no rows in some quarter** reads that quarter as zero,
      because a category absent from a quarter's aggregate is genuinely zero spend. For a floor
      covenant on a category that is merely unbilled that quarter, zero and unknown are different
      claims and the code cannot tell them apart.

## Review

**What the harness bought.** Building the scorer before the agent was the highest-leverage
decision. The first end-to-end run scored 85.56% and the decomposed failure table pointed straight
at four distinct bugs — a bad payroll rule, broken name normalisation, a missing denominator term,
and an over-permissive evidence rule. An aggregate score could not have separated those.

**Where the handoff was wrong.** It asserted a fixed 20% related-party threshold (actually varies
20–40% per borrower, would have broken 10 of 12), that P6 had no KYC (it is a scanned image), and
that clause numbers map to archetypes (they do not). It also missed the agreed-upon-procedures
layer entirely, which is where B1's binding reclassification lives.

**Where I was wrong.** From B1 and P6 I concluded "операционные расходы" is always one designated
ledger line and the marketing/insurance/rent/tax families are pure decoys. That generalised badly —
P1 6.1, P7 6.1 and P10 6.1 aggregate those families directly. Checking a hypothesis against a third
and fourth borrower before encoding it would have caught this sooner.

**What changed my mind about robustness.** Two probes did more than any amount of reasoning: the
vocabulary ablation (rewrite B1's signal descriptions to synonyms and see whether the system
recovers) and the numeral audit (count where the model touches numbers). Both turned "I think this
generalises" into a measurement, and the second found a real architectural violation.

**The one that cost the most to learn.** The extraction cache was keyed on document text, model
and images — not on the prompt or the output schema. So two rounds of "widened the vocabulary,
still 97.22%" were unfalsifiable: the agreement extractions were served from cache and never saw
the change. Putting the instructions and JSON schema into the key forced a real re-run and the
score fell to 73.61%, exposing a refactor that had silently dropped `related_party` from the
extractor's prompt. A cache key that omits the prompt turns every prompt change into an unverified
claim, and it hid a category collision (`distributions` vs Restricted Payments) that violated a
rule written in the same commit.

**The discipline that decided it.** 36 labelled cells will fit almost any convention. Every rule
here traces to document text, and the one place a ground-truth-only rule would have paid — P4 6.3 —
was declined and documented instead. The organizers then confirmed that cell was a corrupted
threshold in the source PDF, not a modelling gap: `0.045x` printed as `0.04x`. Every tolerance that
would have rescued it flipped P3 6.1, a genuine breach 0.80% over its limit. Fitting the outlier
would have shipped a worse system to the test set. That number — rules adopted without textual
support — is the one to keep at zero.

**What auditing the regexes was worth.** Every hardcoded pattern was checked for how it fails, not
whether it works. All of them degraded gracefully except one: `ACC-\d{4}`, the join key between
documents and the ledger, had no fallback at all. Deriving it from the 561 account ids the ledger
actually contains fixed twelve live misattributions on top of the intended robustness — noise
documents cite sub-accounts like `ACC-7801-05`, and the prefix matched. The same exercise found that
document typing dropped a routable-but-untypable document without a word, which would have lost every
KYC dossier — worth 10.00 of 36 — on a corpus that words its headings differently.

**Where an artefact was lying.** Auditing the provenance report itself, rather than the pipeline,
found it labelling `group_capex` "stated in a document" when no document states it, and naming a
source file that did not hold the figure. Both because `report.py` never ran `resolve_unrouted`, so
the document supplying the number that decides P5 6.1 was missing from its view entirely. An audit
trail that is wrong about provenance is worse than no audit trail, and nothing but reading it would
have caught that.

**What the held-out corpus was actually worth.** It scored nothing measurable and found more than
every self-check combined. The published corpus had passed 36/36, a level ladder, a leakage scan,
four perturbations and three from-zero reruns while the code carried a restated receipt signed as
an outflow, five clauses tested over the wrong period, and a schema that could not express a
subsidiary designation stated in prose. Every one of those needed an input the published corpus
does not contain. A passing score on one corpus measures that corpus, and the interesting question
is never "did it pass" but "what would have to be true for it to pass while being wrong".

**The two habits that did the work.** Reading the source clause, and diffing every cell after every
change. Between them they caught eight wrong cells and, twice, told me my first impression of a
change was backwards — J1 6.1 looked like a regression and was a correction, J3 6.1 looked like a
fix and was a loss. Neither would have been visible from a score, and on the held-out corpus there
is no score to look at.

**Where isolation failed.** The per-corpus layout exempted exactly one corpus: the published one,
which every command touches by default. The submitted answer was worse — a single shared path that
a regression check overwrote once and a forgotten background job overwrote again, after the first
had been repaired. A guarantee that holds everywhere except the busiest path is not a guarantee,
and `report` and `explain` were quietly violating it too, because they also read documents with the
model and nobody thinks of them as writers.

**Why the pipeline stayed batched.** Extracting everything and then computing beats answering cells
on demand here: a borrower's documents serve all three of its clauses, so batching costs 39
extraction calls where per-cell costs 117, and cross-cell facts like related parties stay consistent
by construction. What batching cannot do is fetch a quantity the fixed schema did not anticipate,
so that one case — and only that case — is handled on demand, triggered by the evaluator naming
the identifier it cannot resolve.
