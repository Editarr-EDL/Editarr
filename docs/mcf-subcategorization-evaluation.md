# MCF subcategorization evaluation

This record separates parser conformance from model-quality evidence. Passing the checked-in
synthetic corpus proves strict parsing, fallback, metric, and reporting behavior. It does not
establish that Qwen can classify real media accurately.

## Implemented policy

The version 1 result records a registry-valid family/subcategory pair, method, producer,
confidence or decision evidence, model identity and revision where applicable, and a fallback
reason. Invalid pairs, malformed model output, out-of-family output, missing family decisions,
and unevaluated confidence all produce deterministic family fallbacks.

The accepted exact detector mappings are:

| Producer label | Result | Rationale |
| --- | --- | --- |
| NudeNet `BUTTOCKS_EXPOSED` | `nudity/bareButtocks` | The detector and MCF token identify the same exposed body part. |
| NudeNet `FEMALE_GENITALIA_EXPOSED` | `nudity/exposedGenitalia` | The detector and MCF token identify the same exposed body part. |
| NudeNet `MALE_GENITALIA_EXPOSED` | `nudity/exposedGenitalia` | The detector and MCF token identify the same exposed body part. |

NudeNet `FEMALE_BREAST_EXPOSED` and `ANUS_EXPOSED` remain `nudity/nudity`. The first does not
prove the broader MCF `toplessness` concept and the registry has no anus-specific token. Broad
NSFW, NSFL, violence, and profanity outputs also retain their established fallback.

The CC0 producer-label policy corpus at
`tests/fixtures/mcf/detector-label-policy-corpus-v1.json` covers all active producer labels,
the three exact NudeNet mapping inputs, and ambiguous or look-alike counterexamples. Run it with:

```bash
uv run editarr-evaluate-subcategorization \
  tests/fixtures/mcf/detector-label-policy-corpus-v1.json --detector-labels
```

Its 13 cases report exact-token micro precision/recall/F1 of `1.0/1.0/1.0`, a `10/13`
(`0.7692307692`) intentional fallback rate, and zero base-family regressions. This measures the
complete deterministic mapping policy, not the underlying detectors' ability to identify media.

## Parser conformance corpus

The CC0 test-owned corpus at
`tests/fixtures/mcf/subcategorization-parser-corpus-v1.json` contains six synthetic response
cases and seven expected family decisions. It covers accepted specific tokens, low confidence,
malformed JSON, out-of-family output, and a clean mixed-family response. Run it with:

```bash
uv run editarr-evaluate-subcategorization \
  tests/fixtures/mcf/subcategorization-parser-corpus-v1.json
```

Recorded result on 2026-09-18 with threshold `0.9`:

| Metric | Result |
| --- | --- |
| Exact-token micro precision / recall / F1 | `1.0 / 1.0 / 1.0` |
| General-fallback rate | `5/7` (`0.7142857143`) |
| Invalid-output rate | `2/6` (`0.3333333333`) |
| Base-detection family regressions | `0` |
| Supplied latency mean / p50 / p95 / max | `11.5 / 11.0 / 14.0 / 14.0 ms` |

These values are parser-conformance expectations, not measured Qwen accuracy or latency.

## Real-model schema sample

The pinned local `mlx-community/Qwen3-VL-8B-Instruct-4bit` artifact at revision
`defcdea7cc7a4b0858fea563cbbce171d328e457` was replayed against two existing one-frame runtime
bundles without downloading or changing an artifact. Both responses satisfied the constrained
schema and stayed in the requested family:

| Detector family | Parsed response | Runtime result | Elapsed |
| --- | --- | --- | --- |
| `violence` | objectionable, confidence `0.95` | `violence/violence`, `insufficient_evidence` | `14.78 s` |
| `nudity` | no objectionable content | `nudity/nudity`, unselected, `insufficient_evidence` | `3.94 s` |

The samples have no reviewed expected subcategories or documented media license, so they measure
only schema compliance and latency. They do not support precision, recall, or an acceptance
threshold. Production therefore supplies no threshold and Qwen cannot specialize a finding.

## Rejected claims and next evidence

- No Qwen-derived specific subcategory is supported yet. The next viable approach is a licensed
  or test-owned corpus of reviewed visual segments with positive and negative examples for each
  candidate token.
- Transcript context is retained, but current profanity producers return only a Boolean match.
  There is no versioned contextual classifier or representative reviewed Language corpus, so
  `language/blasphemy`, `language/nameCalling`, `language/sexualDialogue`, and
  `language/vulgarity` are not claimed. Generic profanity remains `language/swearing`.
- No new model artifact, configuration, capability, notice, or release collection is required.
- Replays of pre-MCF bundles fill only the frozen general fallback in memory. Replay manifests,
  imported/manual classifications, saved reviews, and old scan evidence are not rewritten.
