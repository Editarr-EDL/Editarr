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
| MLX Whisper base | `mlx-community/whisper-base-mlx@1e3e249fb8d01c655324bd6841b1deadffd6d04c` | 143,724,466 | **License review required** |
| WD v3 taggers: ViT, ConvNeXt, SwinV2, ViT Large, EVA02 Large | pinned separately in the manifest | 378,844,778–1,260,954,141 each | Apache-2.0 declared |
| Stable Diffusion safety checker | `CompVis/stable-diffusion-safety-checker@cb41f3a270d63d454d385fc2e4f571c487c253c5` | 1,216,072,194 | **License review required** |
| Image Safety Classifier S | `OwenElliott/image-safety-classifier-s@015042b0eab17f1b17f2986527386346fb0d94be` | 22,648,677 | MIT declared |
| SigLIP2 base encoder | `google/siglip2-base-patch16-256@3f9f96cb90da5dbc758b01813f2f6f1aee24c1ab` | 1,539,637,736 | Apache-2.0 declared |
| SigLIP2 NSFW and violence heads | `khasinski/siglip2-moderation-heads@b88fe03369e3dc6aa9887cd3b4cd3e9b7cf76ee5` | 789,337 / 789,349 | Apache-2.0 declared |
| Qwen3-VL 4B Instruct 4-bit | `mlx-community/Qwen3-VL-4B-Instruct-4bit@2fd8dacbdb8f1e54b8c005f081ec5bf79c56376b` | 3,109,729,929 | Apache-2.0 declared |
| Qwen3-VL 8B Instruct 4-bit | `mlx-community/Qwen3-VL-8B-Instruct-4bit@defcdea7cc7a4b0858fea563cbbce171d328e457` | 5,776,630,909 | Apache-2.0 declared |

The MLX Whisper repository does not currently declare a repository license even though
the original Whisper project is MIT-licensed. The CompVis safety-checker repository also
does not publish model-license metadata. Both remain available for development and
on-demand acquisition, but they are `license-review-required`. NATIVE-15 and NATIVE-17
must not approve a release until both have written clearance or are removed from the
release contract.

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
  obligations. It is `license-review-required`; NATIVE-15 and NATIVE-17 must not approve
  a release until the executable's exact license, corresponding-source/source-offer, and
  notice obligations are verified.

NATIVE-10 owns collection of package data and helper binaries into frozen artifacts.
NATIVE-16 owns the final SBOM and complete third-party notices. A declared license in
this inventory is evidence about the pinned upstream component, not legal advice or a
substitute for those release checks.
