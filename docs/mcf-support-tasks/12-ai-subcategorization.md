# MCF-12 - Improve AI subcategorization

Status: **DONE**

Dependencies: MCF-11. Deliverable: evaluated, provenance-aware assignment of more specific
MCF subcategories for AI-generated findings, with deterministic general fallbacks.

This task begins only after the core MCF import, review, manual-entry, UI, export, defaults,
and badge work is complete. It must not delay or destabilize that core release.

## Scope

1. Inventory every audio and visual producer, its raw labels, confidence, transcript or
   summary context, and current general pair:
   - ordinary profanity: `language/swearing`;
   - explicit blasphemy producer: `language/blasphemy`;
   - nudity: `nudity/nudity`;
   - violence: `violence/violence`.
2. Define a versioned subcategorization result containing the registry-valid family and
   subcategory, method/model provenance, confidence or decision evidence, and fallback
   reason. It must never produce a token outside the canonical registry or a mismatched
   family/subcategory pair.
3. Use deterministic model labels directly only where their semantics unambiguously match
   an MCF subcategory. Document every mapping and counterexample; do not map by name
   similarity alone.
4. At minimum, evaluate Qwen summaries as classification evidence for visual findings and
   segments. Use a constrained prompt/schema containing only valid subcategories for the
   known family, reject malformed/out-of-family output, and retain the general fallback
   when evidence or confidence is insufficient.
5. Evaluate whether transcript context can safely distinguish Language subcategories.
   Generic profanity remains `language/swearing`; only an explicit reliable result becomes
   `language/blasphemy`, `nameCalling`, `sexualDialogue`, or `vulgarity`. Do not infer
   blasphemy from an unversioned hard-coded word heuristic.
6. Decide through measured evidence whether subcategorization is stored during scanning or
   in a deterministic post-processing stage. Preserve scan reproducibility, cache keys,
   cancellation/progress behavior, model acquisition, and immutable source-evidence rules.
7. Build a representative, licensed/test-owned evaluation corpus for supported families.
   Record per-subcategory precision/recall where sample size permits, general-fallback rate,
   invalid-output rate, latency, and regressions against current finding detection.
8. Add configuration/capability reporting only if users need to select or download a new
   model artifact. Update model notices and release collection only for artifacts actually
   introduced.
9. Keep manual and imported MCF classifications unchanged. Reprocessing old scans requires
   a separately approved workflow; this task does not silently rewrite immutable evidence.
10. Update authoritative model-development and feature documentation with the evaluated
    approach, supported subcategories, known gaps, provenance, fallback behavior, and exact
    verification evidence.

## Guardrails

- A more specific category is accepted only when it belongs to the producer's valid family
  and meets the task's measured reliability threshold.
- General category fallbacks are correct successful outcomes, not errors.
- Qwen output is untrusted structured model output and receives strict parsing/validation.
- No classifier changes EDL action, evidence source, timing, channel, or user-owned review
  edits.
- Do not claim support for a subcategory without representative positive and negative tests.

## Producer inventory and initial policy

| Producer | Raw evidence | Context | Policy at this checkpoint |
| --- | --- | --- | --- |
| Better Profanity | matched word or segment, fixed `0.9` finding confidence | full Whisper segment text; word timestamps where available | `language/swearing`; transcript context is retained but has not been shown reliable for another token |
| GLiN Profanity | Boolean/coerced profanity result, fixed `0.9` finding confidence | same Whisper segment context | `language/swearing` |
| alt-profanity-check | Boolean/coerced result, fixed `0.9` finding confidence | same Whisper segment context | `language/swearing` |
| Explicit blasphemy | no active producer exists | none | no claim; never infer from a word heuristic |
| NudeNet | exact object label and model score | frame; Qwen segment summary may be added later | exact buttocks/genitalia mappings below; otherwise `nudity/nudity` |
| Image Safety Gate NSFW | WD/CLIP unsafe booleans and combined score | frame; Qwen segment summary may be added later | `nudity/nudity` |
| Image Safety Gate NSFL | NSFL unsafe Boolean and score | frame; Qwen segment summary may be added later | `violence/violence` |
| SigLIP2 NSFW head | broad NSFW probability | frame; Qwen segment summary may be added later | `nudity/nudity` |
| SigLIP2 violence head | broad violence probability | frame; Qwen segment summary may be added later | `violence/violence` |
| Qwen3-VL segment summary | free-form categories and prose; deterministic temperature | representative frames and all detector findings in the segment | enrichment only pending constrained schema and measured corpus evidence |

Accepted deterministic mappings are intentionally exact:

- NudeNet `BUTTOCKS_EXPOSED` -> `nudity/bareButtocks`;
- NudeNet `FEMALE_GENITALIA_EXPOSED` and `MALE_GENITALIA_EXPOSED` ->
   `nudity/exposedGenitalia`.

Counterexamples are part of the policy. `FEMALE_BREAST_EXPOSED` does not prove the broader MCF
meaning of `toplessness`; `ANUS_EXPOSED` has no exact canonical token; broad NSFW, unsafe,
violence, profanity, and free-form prose labels do not become specific merely because words
look similar. These all retain their established fallback.

## Initial implementation decision

Subcategorization is a deterministic post-detection operation. Detector evidence first becomes
a registry-valid fallback, then an exact label mapping or later constrained summary result may
specialize it. This keeps base detection, frame/transcript caches, cancellation, progress, and
model acquisition unchanged. Only newly generated findings use the result; imported/manual
classifications, saved-review edits, and old immutable scans are untouched.

`scanner/subcategorization.py` defines result version 1 with category/subcategory, method,
producer and model provenance, confidence or decision evidence, and fallback reason. Its
constructor rejects mismatched registry pairs, non-finite/out-of-range confidence, incomplete
Qwen provenance, and incorrectly marked fallbacks. Qwen segment merging now preserves every
distinct detector classification instead of replacing a mixed segment with its first finding.

No Qwen-specific subcategory is claimed yet. The constrained version 1 JSON parser rejects
malformed, incomplete, duplicate, unknown, and out-of-family output. Production deliberately
supplies no acceptance threshold, so even a valid specific proposal remains the family fallback.
Local replay directories are useful inputs for experiments but lack reviewed expected
subcategories and licensing metadata, so they cannot support precision/recall.

The CC0 test-owned parser-conformance corpus contains six synthetic responses covering seven
family decisions at a `0.9` threshold. It reports exact-token micro precision/recall/F1 of
`1.0/1.0/1.0`, fallback rate `5/7`, invalid-output rate `2/6`, and zero family regressions. These
are fixture expectations, not model-quality claims. Two unlabeled one-frame replays with the
pinned Qwen revision both produced schema-valid in-family output; measured elapsed times were
`14.78 s` and `3.94 s`. The evidence and limitations are recorded in
`docs/mcf-subcategorization-evaluation.md`.

A second CC0 policy corpus exhaustively covers the active producer labels, exact NudeNet
mappings, and negative counterexamples. Its 13 cases report exact-token micro
precision/recall/F1 of `1.0/1.0/1.0`, an intentional general-fallback rate of `10/13`, and zero
base-family regressions. This supports every newly claimed mapping without claiming a change in
the underlying detectors' media-recognition accuracy. Qwen and transcript-specific tokens are
rejected rather than claimed because no representative labeled media corpus supports them.

## Verification

- Ruff changed Python files and focused unit/integration tests.
- Deterministic replay tests for constrained Qwen outputs, malformed output, cancellation,
  cache identity, and fallback.
- Corpus evaluation with a checked-in summary of metrics and thresholds.
- Existing scan, Qwen replay, saved-review, EDL, and MCF regression suites.
- Runtime/model-acquisition smoke checks if an artifact or capability changes.
- `git diff --check`.

## Acceptance criteria

- [x] Every AI producer has an explicit specific-or-general mapping policy.
- [x] Qwen summaries were evaluated and used where reliable, or rejection evidence and the
  next viable approach are documented.
- [x] All outputs are registry-valid pairs with provenance and deterministic fallback.
- [x] Generic profanity stays swearing unless an explicit classifier marks another
  Language subcategory; blasphemy is never inferred by an accidental text heuristic.
- [x] Metrics support every claimed specific subcategory without regressing base detection.
- [x] Imported/manual/review-owned classifications and immutable old scans are not rewritten.
- [x] Documentation, tests, packaging, and model notices match the implemented approach.

## Handoff

Status: DONE
Last completed substep: Added strict constrained Qwen parsing and prompts, a reproducible corpus
evaluator/CLI, pre-MCF replay compatibility, fail-closed integration coverage, and an explicit
evaluation record. Exact NudeNet mappings remain the only new specific classifications.
Files changed: `scanner/subcategorization.py`, `scanner/pipeline/audio.py`,
`scanner/pipeline/visual.py`, `scanner/pipeline/qwen_summary.py`, `scanner/subcategorization_eval.py`,
`scanner/subcategorization_eval_cli.py`, focused tests and fixture, `pyproject.toml`,
`docs/model-development.md`, `docs/feature-design-spec.md`,
`docs/mcf-subcategorization-evaluation.md`, and this task file.
Schema/API/contract changes: Added internal result/corpus version 1 contracts and a developer CLI;
no database or public API schema changed.
Verification commands and results: Full backend suite passed with 1,091 tests; Ruff passed for
`scanner` and `tests`; the parser corpus reported 7 TP, 0 FP, 0 FN, and zero family regressions;
the producer-label corpus reported 13 TP, 0 FP, 0 FN, and zero family regressions; both F1 scores
were `1.0`; `git diff --check` passed.
Manual or artifact evidence: The pinned 5.78 GB Qwen artifact was already ready. Two unlabeled
one-frame local replays produced schema-valid in-family output in `14.78 s` and `3.94 s` without
a download. They were not treated as accuracy ground truth.
Known failures or risks: No representative reviewed visual or Language media corpus exists, so
Qwen and transcript-specific classification remain intentionally disabled. No explicit
blasphemy producer exists.
Acceptance criteria still unmet: None for the conservative implemented scope. Future Qwen or
transcript-specific support requires new representative evidence and a separately reviewed
threshold change.
Next exact action: None. Do not enable Qwen or transcript-specific categories without a new
licensed evaluation corpus and reviewed metrics.
Next eligible task: None; this is post-core model-quality work.
Decisions or deviations from the parent plan: Use deterministic post-detection processing. Claim
only exact NudeNet buttocks/genitalia mappings at this checkpoint; all Qwen classifications and
ambiguous labels remain fallbacks.
User approval required: Ask only if evaluation requires a new model/artifact or old-scan rewrite.
