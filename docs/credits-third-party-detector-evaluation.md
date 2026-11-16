# Third-party closing-credits detector evaluation

Evaluation date: **September 20, 2026**

No surveyed candidate demonstrated a
licensed, reproducible improvement on the legal corpus. PySceneDetect remains useful in
its existing supporting scene-boundary role, but replacing the generated-tail scene
evidence with it reduced candidate recall. No package or model was added.

## Eligibility and protocol

The survey used upstream repositories, release metadata, package metadata, and model
registries. A candidate was eligible to execute only when all of the following were true
before installation or model acquisition:

- commercial redistribution terms and model provenance were documented;
- a package and every model artifact had an immutable version plus integrity value;
- local offline execution supported macOS, Windows, and Linux without uploading media;
- analysis could be bounded to the configured media tail;
- output could contribute a reviewable boundary and confidence through the existing
  `FrameObservation` and detector contracts;
- cancellation could be checked between sampled frames; and
- the candidate did not require a user database, media service, or remote API.

Eligible candidates use the unchanged eight-case CC0 corpus and generated tails from
`tests/fixtures/mts/credits-evaluation-v1.json`. The comparison records recall within each
case tolerance, null-case false positives, emitted-candidate boundary error, confidence
bands, runtime, peak Python-managed memory, package/model size, and failure behavior. No
threshold is tuned against this small corpus. Native OpenCV allocations are not included
in the portable peak-memory measurement, so the values are comparative diagnostics rather
than complete process RSS.

## Survey

### PySceneDetect

- Source: [Breakthrough/PySceneDetect](https://github.com/Breakthrough/PySceneDetect)
- Evaluated release: `0.7.1`, tag commit
  `6ebb72392de8acfb6c539bf15d0aa912ce7ab6b2`
- License: BSD-3-Clause. The Python wheel is 146,194 bytes with SHA-256
  `91b67902275be0d29a12f6d70435f04e0bd7852a6dc8a69c901c6069328dffa`.
- Maintenance/platforms: active; Python 3.10+; OS-independent OpenCV/PyAV backends. It
  executes locally and has no model artifact.
- Fit: emits scene boundaries, not credits semantics or calibrated credits confidence.
  Editarr already locks this release and uses it for general scene detection, so the
  comparison required no installation or acquisition.
- Disposition: executed as supporting sequence evidence. Keep only its existing role; do
  not replace Editarr’s visual fallback with it.

### RapidOCR and PaddleOCR

- Sources: [RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR) and
  [PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)
- Surveyed releases: RapidOCR `3.9.2`; PaddleOCR `3.7.0`.
- License/platforms: both repositories declare Apache-2.0 and local Windows, macOS, and
  Linux operation. RapidOCR offers ONNX Runtime, OpenVINO, MNN, Paddle, TensorRT, and
  PyTorch backends and returns text boxes and scores suitable for text-density evidence.
- Integrity/size: the RapidOCR wheel is 27,275,208 bytes with SHA-256
  `04d6b8d151f823d930bd91910555f57bea87c0c44fa6794267b94cf9c1ef9a0`.
  Its registry pins `PP-OCRv6_det_tiny.onnx` to ModelScope revision
  `bbd748c6a4c6c19da672d72f491e355ca35f2d8a`, size 1,829,618 bytes, SHA-256
  `f42c0fbd294d95eac1a550e131b277dac97462c8025fa4b6c3cec1b7894bd3d5`.
- Dependencies: RapidOCR adds `pyclipper`, Shapely, PyYAML, tqdm, OmegaConf, requests, and
  colorlog, and declares `opencv-python` while Editarr uses `opencv-python-headless`.
  RapidOCR's release build prepares model assets for its wheel; its runtime downloader can
  also fetch registry models and verify declared SHA-256 values.
- Provenance gate: the `3.9.2` source tree does not contain a model-specific license and
  provenance record. The current README refers to `MODEL_LICENSES.md`, but that path was
  absent when surveyed. The general PaddleOCR Apache-2.0 repository license and papers do
  not identify the complete training-data rights for the proposed weight.
- Disposition: rejected before execution. The model lacks documented provenance for the pinned weight.

### Recurring Content Detector

- Source: [nielstenboom/recurring-content-detector](https://github.com/nielstenboom/recurring-content-detector)
- Release/maintenance: MIT `1.1.1`; release and default-branch commit dated January 6,
  2023 (`b248e17fa9f8d384ae8a146abc7a5d47190aaaa6`).
- Fit: local Python/FFmpeg processing, but the unsupervised method compares recurring
  material across multiple episodes in a season. A standalone movie or one authored tail
  cannot provide its required comparison set, and it does not supply MTS credits evidence
  confidence.
- Disposition: rejected as inapplicable to the single-file movie and manual-file contract.

### plex-credits-detect

- Source: [cjmanca/plex-credits-detect](https://github.com/cjmanca/plex-credits-detect)
- Release/maintenance: MIT; latest commit February 12, 2023
  (`519b12470729d114ad9db834efd241b240763e99`). The README explicitly marks the project
  end-of-life.
- Fit: cross-episode audio/video fingerprinting plus optional black-frame/silence rules. It
  is coupled to, and writes directly into, a Plex database and does not expose the required
  credits confidence/evidence contract.
- Disposition: rejected for maintenance, service coupling, side effects, and incomplete
  evidence semantics.

### end-credits-detection / credictor

- Source: [thiago-franco/end-credits-detection](https://github.com/thiago-franco/end-credits-detection)
- Maintenance/license: latest commit July 29, 2021
  (`cef2e34aaa28a8796dddcae4892805a8c68da4cb`); no repository license.
- Artifacts/provenance: includes an unlicensed 59,210,175-byte random-forest pickle and a
  47,083,939-byte packaged model, unidentified numeric training sources, and copyrighted
  test imagery. No model or data provenance permits commercial redistribution.
- Disposition: rejected without installation or execution.

### credit-detect

- Source: [authrequest/credit-detect](https://github.com/authrequest/credit-detect)
- Maintenance/license: active in June 2026 at
  `e0dcd5e7de569053455142670387e728e8d7072d`; AGPL-3.0-or-later.
- Fit/provenance: its heuristic is reverse-engineered from Plex Media Scanner. Direct video
  mode requires a `model_v1.pb` extracted from Plex, whose license the project explicitly
  calls unclear. CSV mode requires the corresponding pre-extracted proprietary feature
  shape and is not a complete media adapter.
- Disposition: rejected for model provenance, redistribution, and input-contract failures.

### Existing Qwen choices

Editarr's pinned Qwen3-VL 4B and 8B choices remain optional ambiguity evidence under the
credits detection contract. No Qwen artifact was available
for deterministic evaluation. The comparison used neither a model cache nor a download;
the existing strict mocked-response tests remain the only Qwen evidence for this corpus.

## Executed comparison

The evaluator used the installed PySceneDetect `ContentDetector` with its upstream default
threshold `27.0` and a one-sample minimum spacing over the same ordered generated frames.
Its boundaries replaced only the authored scene vector; OpenCV text/motion observations,
chapters, ranking policy, thresholds, and DTOs were unchanged.

Recorded platform: `macOS-27.0-arm64-arm-64bit`.

| Track | Recall | Null false positives | Mean/max emitted error | Analysis runtime | Peak Python memory |
| --- | ---: | ---: | ---: | ---: | ---: |
| Editarr rendered baseline | 7/7 | 0/1 | 0.0s / 0.0s | 0.023s | 582,208 bytes |
| PySceneDetect scene evidence | 6/7 | 0/1 | 0.0s / 0.0s | 0.264s | 4,626,142 bytes |

PySceneDetect emitted four medium- and two high-confidence candidates. It missed
`intermittent-black-frames`: the generated frames yielded no PySceneDetect cut, leaving
only one extracted positive evidence kind, while MTS policy requires two independent
non-black kinds without a credible chapter. The miss is preferable to weakening that
guard or tuning a third-party threshold to one synthetic case.

Failure behavior is deterministic: cancellation is checked before every sampled frame,
an unreadable frame raises a bounded non-path-bearing error, no media is uploaded, and no
model or service is contacted.

## Decision

Keep `credits-detector-v1` and its OpenCV visual/text fallback. Continue accepting cached
PySceneDetect boundaries as supporting evidence where already available, but do not make
them the sole scene source and do not add a new adapter, model, package, or acquisition
flow. The current extractor is the only evaluated option with 7/7 synthetic recall, 0/1
null false positives, low-confidence visibility, and first-credible-boundary behavior.

This decision does not claim real-film population accuracy. A future OCR hybrid can be
reconsidered only after a model-specific provenance record is available and explicit
approval names the exact package and artifact. Such work must rerun this protocol, add
licensed cases that distinguish OCR from edge density, and version the detector cache.

Reproduce the machine-readable result with:

```bash
uv run editarr-evaluate-credits \
  tests/fixtures/mts/credits-evaluation-v1.json \
  --output /tmp/editarr-credits-comparison.json
```
