# Covenant agent — build log

## Result

**100.00% on the published data** — 36/36 statuses, 36/36 `actual` exact, 9/9 evidence ids,
with one owner-issued source correction applied (P4's agreement prints a typo'd threshold).
Rules adopted without textual support: **0**.

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

**Portability**
- [x] `--data` on all seven commands; the ledger is found by pattern, `ground_truth.json` is
      optional, and a missing corpus fails by name rather than writing an empty submission
- [x] Dry-run against a simulated held-out corpus: renamed ledger, no answer key
- [x] `team` / `contact_email` taken from the template, which is where the organisers ask for them

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
- [x] `cache/provenance.ru.html` / `.html` — every verdict traced from the printed clause to the
      transaction that decided it
- [x] `tasks/findings.md` — dataset findings and robustness findings
- [x] `README.md` — Russian; the operational entry point, with every command verified against
      `--help`

## Open

- [ ] **A wrong expression that yields a positive number.** `verify_derived` catches a changed
      value, an unprinted part and a sign error. A derivation that drops a term but stays positive
      — `ppe_closing - ppe_opening` without the depreciation charge — is self-consistent and has no
      mechanical detector. Only the `basis` the model quotes, shown in the provenance report, stands
      between it and a wrong answer. A second opinion would share the first one's failure mode.
- [ ] **The trigger-not-met branch has never executed.** The corpus's one springing covenant has an
      active trigger, so ablating triggers costs 0.00 and the "condition not met, complied with by
      default" path is untested end to end. Synthetic covenants over real aggregates would cover it
      without an answer key, since the expected value is known by construction.
- [ ] **Figure names are an open namespace.** A formula may say `net_debt` while the extractor
      reported `total_net_debt`. The second pass now recovers this by going to look for the name the
      formula used, but the orphan stays in `extra` and nothing reconciles the two.
- [ ] **Arbitrary date-range periods** — `Covenant` supports quarter filtering only.
- [ ] **Company matching is `JSC`-only.** `link_by_company` builds its name index from a regex ending
      in ` JSC`, so a borrower incorporated as LLP or TOO would never enter it. The failure is loud
      (*names X, which matches no borrower*) but the fix is a one-line suffix list.
- [ ] **A consolidated report naming two borrowers** attaches to whichever name resolves first, and
      warns about neither.

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

**Why the pipeline stayed batched.** Extracting everything and then computing beats answering cells
on demand here: a borrower's documents serve all three of its clauses, so batching costs 39
extraction calls where per-cell costs 117, and cross-cell facts like related parties stay consistent
by construction. What batching cannot do is fetch a quantity the fixed schema did not anticipate,
so that one case — and only that case — is handled on demand, triggered by the evaluator naming
the identifier it cannot resolve.
