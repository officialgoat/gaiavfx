# Install and run GAIAVFX

Start with the [README](../README.md) for the tested workstation, measured smoke runs, model sizes and licences. Windows 11 with an NVIDIA GPU is the primary target; Linux x86-64 is best effort. You need Git, uv and Node.js 24+ with npm. The app uses Python 3.12 in the repository's `.venv`; ComfyUI and engines have separate environments.

## Installer flags

Run from the repository root. PowerShell script execution may require:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

For feature arrays, use a PowerShell prompt and invoke `install.ps1` directly as in the README. Linux uses `bash install.sh`. These wrappers install dependencies before calling `scripts/install.py`; inspect flags without installing anything with `python scripts/install.py --help`.

| PowerShell | Linux / Python installer | Effect |
|---|---|---|
| `-ComfyDir DIR` | `--comfy-dir DIR` | Adopt existing ComfyUI; accepts the actual folder or portable container. |
| `-InstallComfy DIR` | `--install-comfy DIR` | Install fresh ComfyUI; mutually exclusive with the preceding flag. |
| `-Features core,image-edit,stages,recast` | `--features core image-edit stages recast` | Select weights and doctor checks; default is all four. Node installation still installs all packs. |
| `-SkipModels` | `--skip-models` | Skip ComfyUI weights, including when a feature is selected. |
| `-WithAssistant` | `--with-assistant` | Install the optional local Qwen GGUF, vision projector and llama.cpp. |
| `-WithStagesEngine` | `--with-stages-engine` | Install the isolated worldbuilder environment and probe CUDA. |
| `-NonInteractive` | `--non-interactive` | Do not prompt; download selected weights unless skipped. Required when stdin is not a terminal. |

Without non-interactive mode, the installer asks for ComfyUI location, model downloads and optional runtimes. With no ComfyUI location it can reuse the saved path; otherwise it skips ComfyUI setup. Existing local node-pack changes and commits are preserved, with a diagnostic rather than an overwrite.

The installer performs these steps in order:

1. Check Git, uv and Node/npm; sync locked Python dependencies into `.venv`; install locked npm dependencies into `node_modules`.
2. Install media tools into `.runtime/media`, then build the renderer into `dist/frontend`.
3. Install/adopt ComfyUI; install custom nodes; download selected weights unless skipped.
4. Save connection defaults; install requested worldbuilder and assistant runtimes.
5. Run doctor and print remaining setup work. Stopped ComfyUI or deferred features may yield incomplete readiness without failing the installer; failed provisioning, downloads, builds or doctor errors still fail. Standalone doctor keeps its strict exit status.

Nothing starts ComfyUI automatically during installation. Review model licences before downloading. Rerunning is supported: weights with matching sizes are reused (use `--verify` to hash existing weights), interrupted `.part` files resume, and fresh downloads are SHA-256 verified before publication. Logs emit progress at ten-percent boundaries or after 30 seconds; terminal progress updates in place.

## Start and first run

```powershell
$env:GAIAVFX_LIBRARY = 'C:\GAIAVFX-Library'
.\start.ps1 -WithComfy -ComfyArgs '--lowvram','--reserve-vram','4'
```

```bash
export GAIAVFX_LIBRARY="$HOME/GAIAVFX-Library"
bash start.sh --with-comfy --lowvram --reserve-vram 4
```

Without `-WithComfy` / `--with-comfy`, only the workspace starts. With it, the launcher starts the configured ComfyUI as a separate process (hidden on Windows), with `--disable-api-nodes` by default. Extra arguments reach ComfyUI unchanged. Stop that ComfyUI process separately when finished. If you launch ComfyUI yourself, include `--disable-api-nodes` too.

The web workspace prints its local URL (normally port 5173). Create the named library on first run, then open **Settings → Setup check**. `GAIAVFX_LIBRARY` selects its disk folder; without it, development uses `.cache/library`. Keep real libraries outside the checkout. `npm run desktop` starts the Electron shell instead.

ComfyUI uses the directory/interpreter saved in `.runtime/setup.json`; it discovers portable `python_embeded/python.exe`, `.venv` or `venv`. An explicit `comfy_python` key overrides discovery. The launcher defaults to port 8188; `comfy_port` overrides it. If changing ports, keep the saved `comfy_url` and the library's Settings URL in agreement. Launcher input/output/temp/user files live in `.cache/manual-development/comfy/`.

The optional assistant is configured through Settings. The supplied Windows llama.cpp build includes CUDA runtime files. The Linux binary is CPU-only; build llama.cpp with CUDA for GPU acceleration or use an existing OpenAI-compatible local server. Avoid keeping another large model resident alongside H3.

## Existing weights

Place `extra_model_paths.yaml` in the actual ComfyUI folder before model installation. For example:

```yaml
shared:
  base_path: C:/AI/models
  diffusion_models: diffusion_models
  text_encoders: text_encoders
  vae: vae
  loras: loras
  checkpoints: checkpoints
  model_patches: model_patches
  geometry_estimation: geometry_estimation
```

On Linux, use an absolute Linux path for `base_path`. The setup parser supports plain scalar paths and multiline path lists, relative to `base_path` (itself relative to the YAML file when not absolute). It also recognizes the legacy `unet` and `clip` aliases. It rejects YAML anchors, inline lists and other unsupported structures rather than guessing.

The downloader looks through mapped folders, the local model folder and legacy aliases. It writes missing files to the first mapped folder. Filenames must match the manifest exactly, not just a basename inside a nested directory. Buffalo_l always belongs in `ComfyUI/models/insightface/models/buffalo_l` because the face nodes ignore extra paths. `--extra-model-paths` selects a different YAML for the CLI only; configure ComfyUI to load the same paths separately. Restart ComfyUI after changes.

## CLI reference

Activate `.venv` first, or substitute `.venv/Scripts/python.exe` (Windows) / `.venv/bin/python` (Linux) for `python`. `npm run setup -- ...` is another wrapper; `npm run doctor` invokes `python -m gaiavfx.setup doctor` in the app environment. Usage blocks below are captured from the real subcommand `--help` output.

### doctor

```text
usage: python -m gaiavfx.setup doctor [-h] [--comfy-url COMFY_URL]
                                      [--comfy-dir COMFY_DIR]
                                      [--features FEATURES [FEATURES ...]]
                                      [--verify]
                                      [--extra-model-paths EXTRA_MODEL_PATHS]
                                      [--json]
```

Read-only readiness checks. Defaults to all four feature groups plus runtime checks; select `core`, `image-edit`, `recast`, `stages` and/or `runtime` with `--features`. `--comfy-dir` adds local size checks; `--verify` hashes files and requires that directory. `--extra-model-paths` also requires `--comfy-dir`. `--json` prints the unchanged machine-readable check list, including fix fields even on healthy rows. Exit 0 means every selected feature is ready; exit 1 means incomplete setup. No files are written and no GPU work is run.

### comfy install

```text
usage: python -m gaiavfx.setup comfy install [-h] [--version TAG] [--git]
                                             [--cuda CUDA]
                                             DIR
```

On Windows, installs the SHA-verified official portable build in `DIR/ComfyUI_windows_portable/`; caches its archive at `DIR/../.cache/gaiavfx/ComfyUI_windows_portable_nvidia.7z`. Windows uses built-in `tar.exe` for extraction. Linux and `--git` use a pinned Git checkout at `DIR`, create `DIR/.venv`, then install pinned PyTorch and ComfyUI requirements. `--version TAG` and `--cuda CUDA` override the Git route (CUDA accepts an index URL or a name such as `cu130`, `cu128` or `cpu`); portable uses the verified pinned version/CUDA only. Saves `comfy_dir` and `comfy_url` in setup.json and prints a start command with `--disable-api-nodes`. Does not start ComfyUI.

### nodes install

```text
usage: python -m gaiavfx.setup nodes install [-h] --comfy-dir COMFY_DIR
                                             [--copy] [--python PYTHON]
```

Installs the local pack and four pinned third-party packs in `COMFY_DIR/custom_nodes`. The local pack is linked (Windows junction / Unix symlink), with copy fallback; `--copy` requests copies for local and subdirectory packs. NativeAudioLock is checked out under `COMFY_DIR/.cache/gaiavfx/node-packs` and its subdirectory linked or copied. Other third-party packs are pinned Git checkouts. Installs requirements into ComfyUI's Python with `--no-warn-script-location`; `--python` overrides interpreter discovery. Saves `comfy_dir` in setup.json. Never replaces changed packs silently. Restart ComfyUI afterward.

### models download

```text
usage: python -m gaiavfx.setup models download [-h] --comfy-dir COMFY_DIR
                                               [--features FEATURES [FEATURES ...]]
                                               [--all] [--dry-run] [--verify]
                                               [--yes]
                                               [--extra-model-paths EXTRA_MODEL_PATHS]
```

Defaults to `--features core`; feature names accept spaces or commas. `--all` selects every feature and includes optional entries. `--dry-run` prints the plan, sizes, paths and licences without writing files or making downloads. Without `--yes`, asks before downloading missing weights. `--verify` hashes existing files instead of relying on size. Writes model files and resumable `.part` files in mapped/local folders, never to the library. `--extra-model-paths` overrides the YAML used for path lookup. See the [manifest-generated table](../README.md) for all filenames and licences.

### configure

```text
usage: python -m gaiavfx.setup configure [-h] --comfy-url COMFY_URL
                                         [--engines-root ENGINES_ROOT]
```

Validates the ComfyUI HTTP(S) URL and merges `comfy_url` plus optional `engines_root` into `.runtime/setup.json`. Does not contact the endpoint or modify existing libraries.

### media install

```text
usage: python -m gaiavfx.setup media install [-h]
```

Caches the pinned archive in `.cache/downloads`, verifies it, then installs `ffmpeg`, `ffprobe`, `provenance.json` and an archive licence notice (when present) in `.runtime/media` or `GAIAVFX_MEDIA_DIR`. No setup.json entry is needed. Windows uses the pinned Gyan archive; Linux uses BtbN. It does not run an inference engine.

### assistant install

```text
usage: python -m gaiavfx.setup assistant install [-h] [--dir DIR]
                                                 [--model-only | --server-only]
```

Defaults to `.runtime/assistant`: archives under `downloads/`, extracted llama.cpp under `server/`, and the Qwen GGUF plus F16 vision projector under `models/`. Saves `assistant_executable`, `assistant_model` and `assistant_projector` in setup.json as applicable. `--model-only` and `--server-only` are mutually exclusive. Uses the public Unsloth Qwen3.8-27B UD-Q4_K_M model and pinned llama.cpp b10549; the model/projector total about 17.4 GB. Windows includes CUDA 12.4 runtime files; Linux is CPU-only. No server is started. The alternative gated orcarouter entries in runtime.toml have unresolved hashes/URLs and are not automatic installer choices.

### engines install

```text
usage: python -m gaiavfx.setup engines install [-h] [--root ROOT]
                                               {worldbuilder}
```

Only `worldbuilder` is automatically installable. Defaults to `.runtime/engines/worldbuilder`, creates an isolated Python 3.10.19 environment, installs torch 2.4.1 with CUDA 12.4 and gsplat 1.5.3 plus pinned dependencies, writes `manifest.json` and `probe.json`, and saves `engines_root` in setup.json. This command probes CUDA once and fails readiness on missing CUDA or version drift. It does not fit a stage. Voice engines remain manual.

### smoke

```text
usage: python -m gaiavfx.setup smoke [-h] [--comfy-url COMFY_URL]
                                     [--feature {core,image-edit,stages}]
                                     [--out OUT] [--timeout MIN] [--json]
```

**Runs real inference** against an already running ComfyUI. Features are `core` (default), `image-edit` and `stages`; there is no Recast smoke option. Defaults to `http://127.0.0.1:8188`, independently of saved setup settings. Creates tiny synthetic inputs, uses the app's actual graph builders, and writes `inputs/`, `graph.json`, downloaded `output-*` media and `report.json` under `.runtime/smoke/<timestamp>/` or `--out`. `--timeout` is positive minutes (default 20); `--json` emits one result. Exit 0 is PASS, exit 1 is FAIL. Stages checks DA3/SAM3 geometry extraction, not gsplat fitting or full stage quality. It never downloads missing weights or installs nodes.

## Configuration and troubleshooting

`.runtime/setup.json` is merged atomically; installers preserve unrelated keys. `GAIAVFX_SETUP_FILE` overrides its location. `configure` saves defaults for **new** libraries; existing libraries keep their settings. `GAIAVFX_COMFY_URL` overrides the CLI/default connection URL, while smoke takes its own `--comfy-url` (default `http://127.0.0.1:8188`).

`GAIAVFX_MEDIA_DIR` selects the media installation/discovery directory. Media discovery then tries `.runtime/media`, then PATH. `GAIAVFX_ENGINES_ROOT` overrides the engine root. Runtime and dependency pins live in [runtime.toml](../backend/src/gaiavfx/setup/manifest/runtime.toml) and [comfy.toml](../backend/src/gaiavfx/setup/manifest/comfy.toml).

- **Connection refused:** start ComfyUI and check the URL/port. Doctor does not start it.
- **Missing node:** restart ComfyUI after installation; inspect its startup errors. KJNodes' `PatchTritonVAE requires triton` warning on Windows is harmless for these graphs.
- **Gated weights:** accept the publisher's terms, then set `HF_TOKEN` in the shell. The downloader sends it only to Hugging Face and does not save it.
- **Memory pressure:** the tested flags are `--lowvram --reserve-vram 4`; run nothing else large beside H3. The tested machine had 24 GB VRAM and 64 GB RAM.
- **Interrupted or corrupt download:** rerun the command. Partial downloads resume; a size/SHA mismatch discards the partial file and preserves any previous target. `models download --verify` checks existing files too.
- **Portable extraction failure:** use Windows tar with 7z support, or extract the downloaded archive with 7-Zip as instructed by the error. `comfy install DIR --git` is the alternative Git/uv route.
- **Stages incomplete:** install worldbuilder and inspect its `probe.json`; the doctor reads saved CUDA evidence and does not run a fresh GPU probe. The stages smoke only checks the ComfyUI geometry graph.

Voice conversion remains experimental and manual. [scripts/engine_setup.py](../scripts/engine_setup.py) provides the existing adoption, verification and unlink tools; its registries define the separately provisioned voice engines. No voice engine is installed by the public one-command installer.
