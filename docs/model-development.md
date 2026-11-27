# Editarr scanner model development

This document covers development workflows for replaying Qwen prompts,
building a reviewed frame corpus, and comparing local visual detector models.
For ordinary Editarr setup and use, see the project [README](../README.md).

## Qwen prompt replay

The **Severity and Description model** (API field `qwen_model`) describes flagged
segments after detection, for every video model. Scans can use either the pinned
Qwen3-VL 8B (recommended, default) or 4B Instruct 4-bit model; 4B reduces download size
and memory use. Each scan persists its choice, and only the selected artifact is
acquired on demand.

The description prompt (`qwen_summary.PROMPT` plus the constrained JSON instructions)
also asks for a `severity` level per classification on the rubric in
[`scanner/evaluation/severity_rubric.py`](../scanner/evaluation/severity_rubric.py):
violence 0 none / 1 non-graphic / 2 graphic / 3 gore; sex 0 none / 1 kissing / 2 sex
without nudity / 3 sex with nudity or assault; nudity 0 none / 1 immodesty / 2 nudity /
3 explicit; drugs 0 none / 1 legal use / 2 illegal use. Levels 1–3 map to MCF `low`,
`medium`, and `high`. Severity is read leniently: a response whose subcategory token is
invalid still contributes its severity, out-of-scale levels clamp to the family's top
level, and unusable values are ignored. The description itself is unchanged.

Each visual finding's severity follows this precedence, recorded in its in-memory
`severity_source` (scan caches and debug logs only; not stored in the database):

1. `detector`: a detector's own severity question (d1-3B);
2. `description_model`: the description model's level, when it returns one of at least 1;
3. `confidence`: the detector-confidence mapping (≥ 0.85 high, ≥ 0.65 medium, else low).

Where the description model is unavailable (Windows and Linux), findings keep detector
or confidence severity and have no description (`details` is null); the detector label
and MCF category still identify them. The same holds wherever the description step
returns nothing usable.

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

### Qwen language prompt replay

Run the Qwen audio filter directly from this checkout with an existing Editarr
Python environment on an Apple silicon Mac. Start with a short extracted WAV
containing the speech and context you want to test.

For the local web interface, launch the replay server from the checkout.

```bash
.venv/bin/python -m scanner.commands.qwen_language_replay_web
```

Open `http://127.0.0.1:8767`. For an existing extract on this Mac, expand
**Use local path**, paste the path to the WAV, and click **Use this file**.
Replay reads the original file directly. **Browse** uploads files up to
512 MiB when you prefer to use the file picker. Run **Baseline** after
selecting your audio.
Edit and save the prompt in the checkout, then run **Candidate** with the same
clip and options. The comparison lists flagged phrases side by side in
timestamp order. A blank cell means that run has no matching finding.
The server keeps both reports for the selected clip across reloads and
restarts. Download the JSON reports to keep a copy before replacing the
clip. The server runs locally and loads the current source on each run.

Click a comparison timestamp to open **Inspect decision** and choose the
baseline or candidate. Ask Qwen about the flagged span or the missed phrase,
then review its suggested prompt edit. You can try the edit in the inspector
and rerun just that batch using the run's model and timed transcript. The test
result stays in the inspector. After a rerun, **Ask Qwen** uses the latest
tested instructions, model reply, and validation outcome for that decision.
Edit the draft and rerun again to give Qwen a new test result to review.
Later full runs read the prompt files in the checkout.

New reports record the original prompt and response for inspection. Older
reports use the cached transcript and available prompt source to reconstruct
the batch. The inspector identifies reconstructed evidence.

The replay model selector includes Qwen3.5-9B (4-bit MLX). The first run with
this model downloads about 6 GB into the Editarr model cache. To use it
from the terminal, pass `--qwen-model qwen35-9b-4bit` to `run`.

You can run the same workflow from the terminal.

```bash
.venv/bin/python -m scanner.commands.qwen_language_replay_cli run /path/to/extract.wav \
  --output /tmp/editarr-qwen-baseline.json

# Save your prompt changes in the checkout, then run the same extract again.
.venv/bin/python -m scanner.commands.qwen_language_replay_cli run /path/to/extract.wav \
  --output /tmp/editarr-qwen-candidate.json

.venv/bin/python -m scanner.commands.qwen_language_replay_cli compare \
  /tmp/editarr-qwen-baseline.json /tmp/editarr-qwen-candidate.json
```

Edit `_LANGUAGE_POLICY` or `_language_prompt` in
[`qwen_language.py`](../scanner/pipeline/qwen_language.py), or edit
`_detection_prompt` in
[`qwen_language_detection.py`](../scanner/pipeline/qwen_language_detection.py).
By default, replay tests discovery across the transcript. To also test the
classification prompt for pre-flagged words, pass `--deny-word WORD` on both
runs. You can repeat the flag for multiple words.

The first run transcribes the clip and caches its timed words. Later runs reuse
that transcript for the same audio content and transcription model and run Qwen
with the instructions currently saved in the checkout. By default, transcripts
are stored in the `qwen-language-replays` subdirectory of the Editarr cache.
Use `--cache-dir` on both runs to choose a different location.

Keep the Qwen model, audio padding, and any pre-flagged words fixed between
runs to isolate prompt changes. Use `--qwen-model` and `--audio-padding-seconds`
to fix the model and padding explicitly. The comparison shows changes in time
ranges, words or phrases, MCF categories, and severity. It requires matching
transcripts and completed runs.

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
no exact anus-specific token. Image Safety Gate, SigLIP2 heads, and Shieldstral policies
expose only broad nudity, sex, or violence scores and therefore retain their general family
fallbacks.

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

## Folder-structure evaluation harness

`editarr-evaluate-folder` (also `python -m scanner.evaluation.folder_eval`) scores local
detectors, decision models and VLMs against labels taken from a folder of public
datasets and real films, and serves a static viewer for reviewing and correcting them.
It needs only the project dependencies plus `ffmpeg` and `ffprobe` on `PATH`; it never
downloads datasets or films, and downloads model weights only with `--allow-downloads`.

### Data root

All inputs and outputs live in one data root. Without `--data-root` it is
`$EDITARR_EVAL_DATA_ROOT`, else `evaluation_data/` at the repository root in a source
checkout, else `evaluation_data/` in the working directory. `/evaluation_data/` is
git-ignored: datasets, films, local configuration and results are not redistributable
and must never be committed.

```text
evaluation_data/
  video/<dataset>/...        clips and images; labels come from the folder path
  films/<Title (Year)>/      symlinks created by `films` (do not add files here)
  films.local.json           optional local film paths (see below)
  _results/                  inventory, frames, model results, summary and viewer
```

Provision datasets by unpacking them under `video/` with their published folder
layouts. [`label_rules.json`](../scanner/evaluation/folder_eval/label_rules.json) maps
paths to MCF keys for `ucf_crime`, `hockey_fight`, `movies_fight`, `xd_violence`,
`Violent Scene Dataset` and `nsfw_dataset_v1`; any folder named after an MCF family,
token or synonym (for example `video/mine/violence/`) also labels its contents.
`video/nude/` is unvetted scraped content and is never listed, decoded, scored or served,
including through symlinks or film paths that resolve into it.

### Films and ground truth

Film windows (10 s, frames at 2.5 s and 7.5 s) are labelled from a ground-truth CSV
directory containing `movies.csv` (`imdb_id`, `name`, `title_main`, `year`,
`duration_sec`) and `tag_timestamps.csv` (`imdb_id`, `category`, `tag_name`,
`start_approx`, `end_approx`), plus optional `movie_tags.csv` and `tags.csv`.
[`films.json`](../scanner/evaluation/folder_eval/films.json) lists the films by title,
year and IMDb id only; supply your own legally obtained copies and the CSV directory.
They resolve in this order, highest first:

1. `films --film IMDB_ID=PATH` (repeatable; the key may also be `Title (Year)`) and
   `films --ground-truth-dir DIR`;
2. `EDITARR_EVAL_GROUND_TRUTH_DIR` (ground truth only);
3. a local JSON config: `films --local-config PATH`, else `$EDITARR_EVAL_FILMS_LOCAL`,
   else `<data root>/films.local.json`;
4. for the ground truth only, the filtered copy a previous `films` run left in
   `films/ground_truth/`.

```json
{
  "ground_truth_dir": "ground-truth-csv",
  "films": {"tt1034415": "movies/Suspiria.mkv", "Hardcore Henry (2015)": "movies/hh.mkv"}
}
```

HDR (PQ or HLG) films are tone-mapped to SDR while extracting frames: with VideoToolbox
on macOS, else with ffmpeg's `zscale` and `tonemap` filters, else with `libplacebo`. When
ffmpeg has none of these, `frames` records an error for the HDR windows rather than
extracting washed-out frames. A film window whose frame cannot be decoded is also
recorded as an error; only whole clips fall back to their first frame.

Relative paths in the local config resolve against the config's folder. A `--film`
entry overrides the local config whichever key form either uses. `films` fails without
changing the inventory when any listed film has no file or a key names no film in
`films.json`.

### Models

`run --model ID` accepts `nudenet`, `siglip2`, `siglip2-gore`, the shipped decision
detectors `scan-d1-3b`, `scan-qwen3-vl-8b` and `scan-qwen3-vl-4b`, and the evaluation-only
llama.cpp and MLX ids in
[`decision/`](../scanner/evaluation/folder_eval/decision/) (for example `d1-3b-f512`,
`qwen3vl-8b`, `qwen3vl-8b-mlx-desc`). By default `run` and `all` refuse downloads for
the whole process, from the moment the command starts: `nudenet`, `siglip2`,
`siglip2-gore` and the `scan-*` ids use Editarr artifacts already installed from model
settings and verified locally, and a missing artifact or failed
acquisition is reported as unavailable (`run` exits with status 2, `all` skips the
model). `--allow-downloads` lets those ids acquire missing Editarr artifacts. The
llama.cpp and MLX ids never download; fetch their weights as described below.

The llama.cpp ids read GGUF weights from `<models dir>/<model>/`, where the models dir is
`$EDITARR_DECISION_MODELS_DIR`, else `evaluation/llama-cpp` under Editarr's model cache
(`EDITARR_MODEL_CACHE`, or the platform cache directory). The unavailable message prints
the `hf download` command for missing weights. `llama-server` is
`$EDITARR_LLAMA_SERVER`; else, for every id except the original `d1-omni-2b`, `d1-3b`
and `clef-flash`, the newest `llama.cpp-b<build>/*/llama-server` release unpacked in the
models dir; else `llama-server` on `PATH`. `EDITARR_DM_STAGE2_THRESHOLD`, `EDITARR_DM_IMAGE_LONG_EDGE`
and `EDITARR_CASCADE_DESCRIBER` tune the decision ids, as described in their modules.

### Subcommands

| Command | Purpose |
| --- | --- |
| `inventory` | List media, probe durations and apply the label rules |
| `films` | Link films, copy their ground-truth rows and add film windows |
| `frames` | Extract frames with ffmpeg (resumable; `--only-folder`, `--limit`, `--workers`) |
| `sample` | Write the stratified `decision_sample` subset |
| `film-subset` | Write a stratified subset of decision-sample film windows |
| `run` | Score frames with one model (resumable; `--subset`, `--concurrency`, `--retry-errors`, `--allow-downloads`) |
| `summarize` | Write `summary.json` and `models/index.json` |
| `describe-eval` | Judge descriptions with d1-3B and compare severity levels |
| `viewer` | Copy the static viewer into `_results/` |
| `serve` | Serve the data root with the corrections API |
| `all` | `inventory`, `frames`, every available model, `summarize` and `viewer` (`--allow-downloads`) |

```bash
uv run editarr-evaluate-folder inventory
uv run editarr-evaluate-folder frames
uv run editarr-evaluate-folder run --model siglip2
uv run editarr-evaluate-folder summarize
uv run editarr-evaluate-folder viewer
uv run editarr-evaluate-folder serve --port 8765
```

Each run records its throughput and memory in `_results/models/<id>.meta.json`:
`server_peak_rss_mb` is the peak of a llama.cpp model server during the last run, and
`process_peak_rss_mb` is the harness process's lifetime high-water mark, which under
`all` includes the models scored before it.

Open `http://127.0.0.1:8765/_results/index.html`. `serve` binds only to 127.0.0.1
(`--port 0` picks a free port) and rejects other `Host` headers, cross-site `Origin` or
`Sec-Fetch-Site` values, non-JSON or oversized correction bodies, and excluded paths,
including symlinks that resolve into them. Reviewer corrections are stored in
`_results/corrections.json` and applied when the summary is rebuilt. Any other static
file server shows the viewer read-only.

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

### Qwen3-VL and d1-3B video detectors

`qwen3-vl-8b` (recommended and the default on Apple silicon), `qwen3-vl-4b`, and `d1-3b`
ask one yes/no question per family about each frame, using the evaluation harness's
wording: `Does this video frame show <subject>? Answer yes or no.` with subjects
`drugs, alcohol, smoking, or gambling`, `nudity`, `sexual content`, and `violence`.
Positive families become general-family findings (`drugs`, `nudity`, `sex`,
`violence`) that the description model can refine. Thresholds are module constants in
[`qwen_detector.py`](../scanner/pipeline/qwen_detector.py) and
[`d1_detector.py`](../scanner/pipeline/d1_detector.py).

The Qwen3-VL detectors (macOS Apple silicon only) run on MLX with the same pinned
artifacts and loader as the description model. The questions are chained in one
conversation per frame: the image is encoded once, each later question feeds only its
new tokens into the KV cache, and the greedy answer token stays in the conversation.
A family's score is P(yes) / (P(yes) + P(no)) from the first answer token's top 20
log-probabilities at temperature 0, flagged at 0.5. When the detector and the
description model are the same artifact, the scan worker loads it once.

d1-3B is a decision model that answers typed questions in one forward pass without
generating text. It runs through transformers (≥ 5.14) and torch, on MPS where the
runtime verified Metal and otherwise on CPU in float32 (the Windows and Linux runtimes
ship CPU-only torch; its 3.1B float32 weights need about 12.5 GB there and run much
more slowly than on MPS), with `trust_remote_code` loading the model's own code from the pinned,
hash-verified artifact only. A malformed answer never fails a scan: an unusable family
decision leaves that family unflagged for the frame, and an unusable severity
distribution gives no detector severity; each is logged once per scan. Each frame gets the four family `noul` questions
plus four `score` severity questions whose levels are the rubric above. The argmax
level sets the finding's severity (level 0 on a positive decision is reported as
`low`). Families are flagged at 0.5 except violence at 0.25: with only the four family
questions, 0.25 reproduces the decisions of the original question set (family plus
specific tokens such as weapons, each at 0.5) on 1,592 of 1,603 evaluation frames and
raises violence F1 from 0.57 to 0.65; the other families scored best at 0.5.

Fine passes score one frame per `EDITARR_DECISION_FINE_SAMPLE_INTERVAL_SECONDS`
(default 0.5) and bisect changes in the set of positive families, as Shieldstral does.

A real-inference check on 12 film frames (8 positive d1-3B decisions, covering all four
families) on an M4 Pro (24 GB):

| Detector | Agreement with the evaluation | Warm time per frame | Memory |
| --- | --- | ---: | ---: |
| `qwen3-vl-8b` (MLX) | 48/48 decisions; scores identical to `qwen3vl-8b-mlx` | 1.37 s | 6.5 GB MLX peak |
| `d1-3b` (transformers, BF16, MPS) | 48/48 decisions vs `d1-3b-full4` and `d1-3b-sev` (llama.cpp Q8_0); 47/48 severity levels; mean \|ΔP\| 0.009 | 0.71 s | 7.0 GB MPS |

A resident detector (d1-3B, a Qwen3-VL detector, or Shieldstral) whose artifact differs
from the description model is never loaded alongside it: the scan detects every scene
first, spooling each scene's detections to its own file in the attempt's temporary
`visual-results/pending/` directory, then releases the detector and describes one scene
at a time, deleting each spooled file once its scene is described. On a 20 s clip with d1-3B and the 8B description model this cut the scan
worker's peak footprint from 17.7 GB to 10.5 GB and wall time from 324 s to 235 s with
identical findings. A Qwen3-VL detector that is the description model's artifact keeps
the single shared instance and describes each scene as it is detected.

### Shieldstral

Shieldstral (macOS Apple silicon only) answers yes/no policy questions about each frame.
Its prompts, routing, and labels are in
[`scanner/pipeline/shieldstral_policies.py`](../scanner/pipeline/shieldstral_policies.py).
Every prompt uses the model card's fixed system prompt and the instruct
`You moderate still frames from movies for a family content filter. Apply a strict
standard.` The evaluated configuration is:

| Step | Query | Threshold setting (default) | Emits |
| --- | --- | --- | --- |
| Sexual screen | `Does this image show sexual activity or sexually suggestive content?` | `EDITARR_SHIELDSTRAL_SEXUAL_THRESHOLD` (0.40) | routed families below |
| Violence screen | `Does this image contain violence or blood?` | `EDITARR_SHIELDSTRAL_VIOLENCE_THRESHOLD` (0.40) | `shieldstral_violence` (violence) |
| Nudity route | `Does this image contain nudity?` | `EDITARR_SHIELDSTRAL_NUDITY_ROUTE_THRESHOLD` (0.25) | `shieldstral_nudity` (nudity) |
| Sex-act route | `Does this image show sexual intercourse or intimate sexual contact?` | `EDITARR_SHIELDSTRAL_SEX_ROUTE_THRESHOLD` (0.5, provisional) | `shieldstral_sexual` (sex) |

Routes are asked only when the sexual screen fires. Both may fire; when neither does,
the frame is reported as `shieldstral_sexual` (suggestive). Every sexual detection uses
the sexual screen score as its confidence; route scores only choose families. All
detections keep their general MCF family fallback, so Qwen can refine the subcategory.

The nudity route uses 0.25 because the question is under-confident on backlit,
sheer-fabric, distant, or partly hidden nudity; on the routing corpus it routed 78/83
nude frames (26/26 shots) to nudity. The sex-act route is **provisional**: neither
evaluation trailer contains real sexual activity, so its recall is unmeasured. Broader
wordings such as `Does this image show people engaged in sexual activity?` fired on
72–76% of plain artistic nude frames, while the chosen wording fired on 5/88 (posed
nudes resembling sexual positions). Check it on footage with actual sex scenes before
relying on it; a missed nude sex scene is reported as nudity only.

On a hand-labeled 320-frame trailer and replay corpus, the two screens together caught
27/27 positive scenes at 288 px (frame recall 0.89, precision 0.78). Frames taller than
`EDITARR_SHIELDSTRAL_MAX_IMAGE_HEIGHT` (default 288) are downscaled with an even width;
288 px showed no measurable loss against 512 px, while 256 px was worse.

Each frame's image is encoded once and reused by every question. The KV cache of the
system + instruct prefix shared by all prompts is computed once per process, each
question pre-fills only its remaining tokens, and the answer logits are computed in
float32 from the final hidden state. Both screens take about 0.66 s per frame at 288 px
on an M4 Pro; routing adds two questions (about 0.5 s) only on sexual-positive frames. Peak MLX
memory is about 5.3 GB at 288 px. Shieldstral is released before the Qwen3-VL
description model loads (see above), so the two are not held together.

A fine scene pass scores one frame per `EDITARR_SHIELDSTRAL_FINE_SAMPLE_INTERVAL_SECONDS`
(default 0.5), plus the first and last frames. When two consecutive samples emit
different labels, including a change of routed family, the gap is bisected until the
change is located to adjacent frames. Other frames reuse the nearest scored result in
the same uniform run, so an event shorter than the interval that falls between two
matching samples can be missed.

In the fine pass, a sexual-positive frame where neither route fires has an unknown
family rather than a confirmed suggestive one. Within each contiguous run of
sexual-positive frames, such frames are reported as nudity when any frame in the run
routed to nudity, so a nude shot whose nudity score dips under 0.25 does not also
produce a sex finding for Qwen to reconcile. Frames that fired the sex-act route keep
the sex family, and runs without a nudity-routed frame keep the suggestive sex
fallback. A coarse sample is a single frame and always uses the fallback.
