# Closing-credits detector evaluation

Evaluation date: **September 20, 2026**

This report records the MTS-04 deterministic detector baseline. It evaluates policy and
cross-platform visual feature extraction on authored CC0 fixtures. It does not claim
real-film accuracy or treat confidence as a probability.

The MTS-04A third-party survey, common-corpus comparison, and keep decision are recorded in
[Third-party closing-credits detector evaluation](credits-third-party-detector-evaluation.md).

## Corpus and method

The source manifest is `tests/fixtures/mts/credits-evaluation-v1.json`, version
`mts-credits-evaluation-v1`. It contains eight authored cases covering scrolling credits,
static title cards, credits over moving imagery, a post-credits scene, credible, absent, and
misleading chapters, no credits, non-English credits, intermittent black frames, and
ambiguous material.

The evaluator runs two tracks:

1. Policy vectors exercise exact evidence ranking, confidence, and boundary behavior.
2. `mts-credits-generated-tail-v1` renders temporary CC0 PNG tail sequences and passes them
   through the OpenCV text-pattern, motion, and black-frame extractor before ranking.

No copyrighted media, user media, model artifact, database, queue, API, or sidecar is used.

## Recorded result

The recorded platform was `macOS-27.0-arm64-arm-64bit`. Detector version
`credits-detector-v1` completed the full evaluator in 0.286 seconds; policy-vector scoring
took 0.001 seconds and rendered-tail generation and analysis took 0.072 seconds.

| Measure | Policy vectors | Rendered tails |
| --- | ---: | ---: |
| Candidate recall within case tolerance | 7/7 | 7/7 |
| False positives on null-boundary cases | 0/1 | 0/1 |
| Mean absolute boundary error | 0.0 seconds | 0.0 seconds |
| Maximum absolute boundary error | 0.0 seconds | 0.0 seconds |

Policy-vector confidence bands contained one low, four medium, and two high candidates. All
seven were within their case tolerance. The ambiguous case remained visible as a low-
confidence candidate. The no-credits case returned `no_candidate`; intermittent black
frames did not establish a candidate by themselves; and the post-credits case selected the
first credible closing-credits boundary.

## Frozen v1 policy

- Analyze the final 45 seconds at a two-second sample interval by default.
- Admit a candidate from one credible tail chapter marker or two independent positive,
  non-black evidence kinds within eight seconds.
- Treat text-pattern and credit-motion scores of 0.50 as positive, black-frame scores of
  0.85 as conflicting evidence, and scene transitions within two seconds as supporting
  evidence.
- Keep the MTS-00 confidence labels: low below 0.60, medium from 0.60 through below 0.80,
  and high from 0.80. They are presentation labels, not calibrated probabilities.
- Give a credible credits chapter priority over a later visual-only post-credits candidate.
  Black-frame evidence lowers confidence. Optional Qwen evidence may annotate or raise a
  deterministic score but may not lower it or create a candidate by itself.

Every behavior-affecting detector, evidence, threshold, prompt, scene-cache, frame-cache,
Qwen model, and pinned Qwen revision value participates in the SHA-256 cache identity.

## Limitations

The corpus is deliberately small and synthetic. It justifies deterministic v1 behavior and
guards regressions; it cannot establish precision, recall, or confidence calibration for a
population of commercial films. Real-media acceptance requires a separately licensed,
human-reviewed corpus. Qwen behavior is covered by strict mocked-response tests only; no
model was acquired or run for MTS-04.

Reproduce the report with:

```bash
uv run editarr-evaluate-credits \
  tests/fixtures/mts/credits-evaluation-v1.json \
  --output /tmp/mts-credits-evaluation-report.json
```