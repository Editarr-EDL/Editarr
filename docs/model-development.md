# Editarr scanner model development

This document covers development workflows for replaying Qwen prompts,
building a reviewed frame corpus, and comparing local visual detector models.
For ordinary Editarr setup and use, see the project [README](../README.md).

## Qwen prompt replay

Video scans can use either the pinned Qwen3-VL 8B or 4B Instruct 4-bit model. The 8B
variant remains the default; 4B reduces download size and memory use. Each scan persists
its choice, and only the selected artifact is acquired on demand.

Each flagged visual scene preserves the exact representative frames sent to
Qwen and its detector metadata in `data/qwen-replays/<scan-id>/scene_*`. This
lets you test a changed prompt without extracting frames or rerunning any
safety model:

```bash
uv run editarr-replay-qwen data/qwen-replays/<scan-id>/scene_0001 \
  --prompt-file /path/to/prompt.txt
```

The prompt-file option is optional. Without it, the command uses the current
built-in prompt. It prints regenerated findings as JSON. Replay bundles are
development artifacts and can be deleted when no longer needed.

### MCF subcategory evaluation

Qwen prose is currently enrichment evidence only. It does not replace a detector's MCF
classification. The classifier uses a constrained, versioned response containing only category
tokens from the detector's known family before any Qwen result can select a more specific
subcategory. Invalid, out-of-family, low-confidence, or unevaluated output retains the
producer's deterministic fallback.

Existing replay bundles do not contain human-reviewed expected subcategories and are not an
evaluation corpus by themselves. A qualifying corpus must be licensed or test-owned, include
positive and negative examples for each claimed subcategory, and record expected exact MCF
tokens. Report per-subcategory precision and recall where sample size permits, fallback and
invalid-output rates, latency, and base-detection regressions. Until those measurements exist,
Qwen summaries must not make persisted classifications more specific.

Evaluate a reviewed response corpus with:

```bash
uv run editarr-evaluate-subcategorization /path/to/subcategorization-corpus.json \
  --output /path/to/report.json
```

The checked-in parser-conformance fixture and current evidence are documented in
[mcf-subcategorization-evaluation.md](mcf-subcategorization-evaluation.md). Its synthetic
responses verify the evaluator and fail-closed parser, not model quality.

Evaluate the complete deterministic producer-label policy separately:

```bash
uv run editarr-evaluate-subcategorization \
  tests/fixtures/mcf/detector-label-policy-corpus-v1.json --detector-labels
```

NudeNet has two deterministic mappings whose output semantics exactly match MCF tokens:

- `BUTTOCKS_EXPOSED` -> `nudity/bareButtocks`;
- `FEMALE_GENITALIA_EXPOSED` and `MALE_GENITALIA_EXPOSED` ->
  `nudity/exposedGenitalia`.

`FEMALE_BREAST_EXPOSED` remains `nudity/nudity`: breast exposure alone does not establish the
broader MCF `toplessness` concept. `ANUS_EXPOSED` also remains general because the registry has
no exact anus-specific token. Image Safety Gate and SigLIP2 heads expose only broad NSFW or
violence scores and therefore retain their general family fallbacks.

## Video-model evaluation

Compare visual detector models using a small corpus of human-reviewed frames.
Place a `manifest.json` next to the images, for example:

```json
{
  "frames": [
    {"path": "clean.jpg", "expected_categories": []},
    {"path": "fight.jpg", "expected_categories": ["violence"]}
  ]
}
```

Run a warmed evaluation with multiple timed passes:

```bash
uv run editarr-evaluate-video-model data/model-evals/smoke/manifest.json \
  --model siglip2_moderation_violence --warmup-frames 2 --runs 5
```

The JSON report includes per-frame predictions, overall and per-category
precision/recall/F1, warm-up duration, throughput, mean/p50/p95/max frame
latency, and peak process memory. Keep clean frames as well as reviewed
positives in the corpus; a corpus made only from prior model flags cannot
measure false positives.

## Closing-credits evaluation

The MTS credits detector has a CC0 authored observation corpus and deterministic generated
tail-frame renderer. Run both policy-vector and rendered-tail evaluation with:

```bash
uv run editarr-evaluate-credits \
  tests/fixtures/mts/credits-evaluation-v1.json \
  --output /tmp/mts-credits-evaluation-report.json
```

The report includes boundary recall within per-case tolerances, null-case false positives,
absolute boundary error, confidence-band counts, runtime, platform, backend, and per-case
results. It also includes a comparison against the already-installed
PySceneDetect scene extractor with package identity, runtime, and Python-managed peak
memory. This synthetic corpus is a regression and policy baseline, not evidence of
real-film population accuracy. The evaluation results and limitations are in
[credits-detection-evaluation.md](credits-detection-evaluation.md), and the third-party
survey and decision are in
[credits-third-party-detector-evaluation.md](credits-third-party-detector-evaluation.md).

## Adding reviewed video ranges

Add reviewed ranges directly from a source video. This extracts one frame per
second by default, copies it into the corpus, and appends the labels to the
manifest:

```bash
uv run editarr-add-video-model-corpus-range /path/to/video.mkv \
  data/model-evals/smoke --start 120 --end 135 \
  --categories violence
```

Use `--interval 0.5` for denser sampling. Omit `--categories` to add clean
frames. Supported labels are `nudity`, `violence`, `profanity`, and `other`.

## Performance controls

Visual scans extract frames at a 512px maximum height by default. Override it
with `EDITARR_EXTRACTED_FRAME_MAX_HEIGHT` when benchmarking quality against
throughput.

SigLIP2 visual scans process eight frames per batch by default. NudeNet uses
the same `EDITARR_VISUAL_BATCH_SIZE` setting. Image Safety Gate remains
frame-at-a-time to keep its three-model stack within a predictable memory
budget.

The complete scanner/model configuration is defined in
[`scanner/settings/config.py`](../scanner/settings/config.py).
