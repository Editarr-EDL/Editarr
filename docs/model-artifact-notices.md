# Model artifact and helper notices

Editarr does not bundle model weights in an installer. Downloadable artifacts are
fetched on demand at the immutable revisions recorded in
[`scanner/model-manifest-v1.json`](../scanner/model-manifest-v1.json), verified before
use, and stored below the resolved `RuntimePaths.model_cache` directory. The manifest is
the machine-readable source of truth for exact filenames, byte sizes, integrity values,
upstream sources, and license metadata.

## Downloaded model inventory

| Backend/artifact | Upstream revision | Expected bytes | License status |
| --- | --- | ---: | --- |
| Faster-Whisper base | `Systran/faster-whisper-base@ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66` | 147,882,941 | MIT declared |
| MLX Whisper base | `mlx-community/whisper-base-mlx@1e3e249fb8d01c655324bd6841b1deadffd6d04c` | 143,724,466 | Undeclared |
| WD v3 taggers: ViT, ConvNeXt, SwinV2, ViT Large, EVA02 Large | pinned separately in the manifest | 378,844,778–1,260,954,141 each | Apache-2.0 declared |
| Stable Diffusion safety checker | `CompVis/stable-diffusion-safety-checker@cb41f3a270d63d454d385fc2e4f571c487c253c5` | 1,216,072,194 | Undeclared |
| Image Safety Classifier S | `OwenElliott/image-safety-classifier-s@015042b0eab17f1b17f2986527386346fb0d94be` | 22,648,677 | MIT declared |
| SigLIP2 base encoder | `google/siglip2-base-patch16-256@3f9f96cb90da5dbc758b01813f2f6f1aee24c1ab` | 1,539,637,736 | Apache-2.0 declared |
| SigLIP2 NSFW and violence heads | `khasinski/siglip2-moderation-heads@b88fe03369e3dc6aa9887cd3b4cd3e9b7cf76ee5` | 789,337 / 789,349 | Apache-2.0 declared |
| Qwen3-VL 4B Instruct 4-bit | `mlx-community/Qwen3-VL-4B-Instruct-4bit@2fd8dacbdb8f1e54b8c005f081ec5bf79c56376b` | 3,109,729,929 | Apache-2.0 declared |
| Qwen3-VL 8B Instruct 4-bit | `mlx-community/Qwen3-VL-8B-Instruct-4bit@defcdea7cc7a4b0858fea563cbbce171d328e457` | 5,776,630,909 | Apache-2.0 declared |
| Shieldstral 1.0 3B INT8 (MLX) | `sahilchachra/Shieldstral-1.0-3B-INT8@accac83025f1164a388e0d704840809a4d46bffd` | 4,500,914,339 | Apache-2.0 declared |
| d1-3B (BF16, transformers) | `LiquidAI/d1-3B@05b80faf55536582ad5ffb2da7456f6a653acf71` | 6,265,040,453 | LFM Open License v1.0 declared (`LicenseRef-LFM-1.0`) |

The pinned MLX Whisper and CompVis safety-checker repositories have undeclared
licenses. Editarr downloads both artifacts on demand from their upstream
repositories when the corresponding backend is available.

Shieldstral uses a community MLX INT8 conversion of Mistral's Apache-2.0
`mistralai/Shieldstral-1.0-3B`. Editarr pins only the files `mlx_vlm.load` and the
processor read, including `chat_template.jinja`; the repository's `tekken.json` and
README are not downloaded. It needs about 5.3 GB of memory at the default 288 px
frame height; see [model-development.md](model-development.md#shieldstral) for the
evaluated prompts and thresholds.

The Qwen3-VL 8B and 4B video detectors reuse the pinned Qwen3-VL artifacts above; they
add no downloads.

d1-3B is distributed by Liquid AI under the
[LFM Open License v1.0](https://huggingface.co/LiquidAI/d1-3B/blob/05b80faf55536582ad5ffb2da7456f6a653acf71/LICENSE),
which is not an OSI license: it does not license commercial use by organizations with
annual revenue of US$10 million or more. Editarr pins the weights, tokenizer, configs,
`LICENSE`, and the repository's Python model code (`modeling_d1.py`, `lfm2_vl.py`,
`hybrid.py`, `runner.py`, `prompt.py`, `api.py`). transformers runs that code with
`trust_remote_code` only from the hash-verified local artifact; it is never fetched at
inference time. The README and `.gitattributes` are not downloaded.

## Package-supplied resources and helper binaries

- NudeNet supplies `320n.onnx` as package data and declares MIT.
- Better Profanity 0.7.0 supplies `profanity_wordlist.txt` under MIT.
- GLiN Profanity 3.4.0 supplies its multilingual `data/dictionaries/*.json` package
  data under the MIT expression declared in its distribution metadata.
- alt-profanity-check 1.9.0 supplies its `data/*.joblib` classifier data under MIT.
  These package/data notices are independently represented in the manifest and must be
  reproduced by the release SBOM.
- `imageio-ffmpeg` Python code declares BSD-2-Clause and has a separate manifest entry,
  while its bundled FFmpeg executable
  is governed separately by the FFmpeg build configuration and applicable LGPL/GPL
  obligations. The upstream bundled executable is marked `license-review-required`
  in the manifest.

The native macOS release replaces the `imageio-ffmpeg` binary with FFmpeg and
ffprobe built from pinned sources. It rebuilds PyAV against the same libraries
and builds OpenCV without its own FFmpeg copy.

The macOS release includes third-party notices and the corresponding source for
the bundled FFmpeg build. See [Editarr v0.1.1](https://github.com/Editarr-EDL/Editarr/releases/tag/v0.1.1).
