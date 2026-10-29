# Install and run GAIAVFX

Start with the [README](../README.md) for the tested workstation, measured smoke runs, model sizes and licences. Windows 11 with an NVIDIA GPU is the primary target; Linux x86-64 is best effort. The installer can install missing Git, uv and Node.js 24+ with npm (Linux Git requires your distro package manager). uv downloads Python 3.12 for the app and Python 3.10 for the stages engine; no separate Python installation is needed. The app uses Python 3.12 in the repository's `.venv`; ComfyUI and engines have separate environments.

## Prerequisites

Before changing anything, interactive setup asks once: `Install the missing prerequisites (...) now? [Y/n]`. It names the installation method and explains that proceeding accepts those packages' licence terms. With `-NonInteractive` / `--non-interactive`, or redirected stdin, missing tools produce manual links and exit 1 unless `-InstallPrerequisites` / `--install-prerequisites` is supplied. Existing tools are reused; Node must be at least 24 and include npm.

On Windows (PowerShell 5.1+), the installer tries:

1. **winget:** installs `Git.Git`, `astral-sh.uv` and/or `OpenJS.NodeJS.LTS` with package and source agreement flags, then refreshes this process's PATH from Machine/User values and known installation folders. Normal winget packages may request Windows elevation.
2. **App Installer registration:** if winget is absent, tries Microsoft's [documented registration command](https://learn.microsoft.com/en-us/windows/package-manager/winget/#install-winget), `Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe`, without downloading anything. If it succeeds, uses winget. Server/LTSC systems may not support registration.
3. **Portable copies:** if winget remains unavailable or a package still fails the prerequisite check, downloads official MinGit, uv and Node 24 x64 archives to `.runtime/tools/downloads`, checks exact size and SHA-256, then extracts under `.runtime/tools/git`, `uv` and `node`. This also handles Node LTS below 24 or missing npm. Portable installation needs no admin rights and changes no system or registry settings. Pins are in [prerequisites.json](../scripts/prerequisites.json): MinGit 2.56.0.4, uv 0.11.8 and Node 24.22.2 LTS. Git/uv digests come from official GitHub release assets; Node hashes come from that release's `SHASUMS256.txt`.

Downloads use TLS 1.2 and `Invoke-WebRequest`. If that transport fails, an existing app Python or curl can retry the same official URL; size and SHA-256 verification are still required. Python is not required for the initial bootstrap. A corrupt archive is deleted before extraction. If all methods fail (including offline), setup prints manual links and exits 1: [App Installer](https://aka.ms/getwinget), [Git](https://git-scm.com/download/win), [uv](https://docs.astral.sh/uv/getting-started/installation/), [Node.js 24](https://nodejs.org/en/download).

On Linux x86-64, the installer prints distro-specific Git hints (`apt`, `dnf`, `pacman`) and Node 24 options (distro, NodeSource or nvm). Missing Git stops setup: install it yourself with your distro package manager. With consent, uv and Node use the same manifest's official, SHA-256-verified Linux archives, without sudo. Downloads require curl or wget, sha256sum, and tar with gzip/xz support. No sudo command runs automatically.

`install.ps1`, `start.ps1`, `install.sh` and `start.sh` prepend existing portable folders for their session. Python setup finds git/uv on PATH first, then in the checkout's portable folder. For `npm` in new terminals, optionally add the absolute `<repo>/.runtime/tools/node`, `<repo>/.runtime/tools/uv`, and `<repo>/.runtime/tools/git/cmd` folders to your **user PATH** in Windows Environment Variables. On Linux, add `export PATH="<repo>/.runtime/tools/node/bin:<repo>/.runtime/tools/uv:$PATH"` to your shell profile, replacing `<repo>` with the absolute checkout path. The installer never edits PATH persistently.

The prerequisite helper also has a standalone CLI:

```powershell
# Simulation only: emits JSON, performs no installation or registration.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/prerequisites.ps1 -Plan -SimulateMissing git,node -SimulateWinget registers -NonInteractive -InstallPrerequisites
# Explicit consent to all three portable packages; bypasses winget entirely.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/prerequisites.ps1 -ForcePortable -Destination C:/gvt/h-tools
```

`-SimulateWinget` accepts `present`, `absent` or `registers`. `-SimulateMissing` accepts git, uv, node and npm (comma-separated with `-File`). `-Plan` defaults to all prerequisites missing and winget absent. `-ForcePortable` is explicit consent to download all three packages even when tools already exist; `-Destination` defaults to `<repo>/.runtime/tools`. Normal launcher installation only installs missing packages.

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
| `-SkipAssistant` | `--skip-assistant` | Skip the default local Swift GGUF, vision projector and llama.cpp installation. |
| `-SkipStagesEngine` | `--skip-stages-engine` | Skip the default isolated worldbuilder environment and CUDA probe. |
| `-WithAssistant` / `-WithStagesEngine` | `--with-assistant` / `--with-stages-engine` | Deprecated no-ops with a one-line note; skip flags take precedence. |
| `-InstallPrerequisites` | `--install-prerequisites` | Consent to missing prerequisite installation and package licence terms. Handled by the shell launcher before Python starts; accepted by Python argparse for forwarding/help. |
| `-NonInteractive` | `--non-interactive` | Do not prompt; download selected weights unless skipped. Required when stdin is not a terminal. |

With a valid saved ComfyUI folder, interactive setup offers it first and as the default, followed by fresh install, another existing folder, and skip. Otherwise it offers (1) a fresh install in `<repo parent>/ComfyUI` (default), (2) an existing folder, or (3) skip. Model downloads show the total size for selected features and default to Yes; the assistant and stages engine prompts also default to Yes. The model prompt appears only after choosing a ComfyUI folder. Non-interactive setup installs the assistant and stages engine unless skipped, and reuses a valid saved ComfyUI folder unless `-ComfyDir` / `--comfy-dir` or `-InstallComfy` / `--install-comfy` overrides it. Missing or invalid saved folders are ignored with a note. Without a usable location, ComfyUI and its models are skipped. Skip messages include a platform-specific installer command to run from the repository root. Existing local node-pack changes and commits are preserved, with a diagnostic rather than an overwrite.

The installer performs these steps in order:

1. Check Git, uv and Node/npm; install missing prerequisites with consent; sync locked Python dependencies into `.venv`; install locked npm dependencies into `node_modules`.
2. Install media tools into `.runtime/media`, then build the renderer into `dist/frontend`.
3. Install/adopt ComfyUI; install custom nodes; download selected weights unless skipped.
4. Save connection defaults; install worldbuilder and the assistant unless skipped.
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

The assistant is configured through Settings. The supplied Windows llama.cpp build includes CUDA runtime files. The Linux binary is CPU-only; build llama.cpp with CUDA for GPU acceleration or use an existing OpenAI-compatible local server. Avoid keeping another large model resident alongside H3.

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

Read-only readiness checks. Defaults to all four feature groups plus runtime checks; select `core`, `image-edit`, `recast`, `stages` and/or `runtime` with `--features`. `--comfy-dir` adds local size checks; `--verify` hashes files and requires that directory. `--extra-model-paths` also requires `--comfy-dir`. `--json` prints the unchanged machine-readable check list, including fix fields even on healthy rows. Exit 0 means every selected feature is ready; exit 1 means incomplete setup. No files are written and no GPU work is run. Engine, probe and CUDA rows appear under stages when stages is selected, otherwise under runtime. Runtime keeps the assistant, media and manual voice checks (labelled **Agent and tools** in the app). Missing assistant and stages engine installations are required gaps; explicitly skipped add-ons retain their diagnostic status and show how to install later.

### comfy install

```text
usage: python -m gaiavfx.setup comfy install [-h] [--version TAG] [--git]
                                             [--cuda CUDA]
                                             DIR
```

Targets inside the repository checkout are refused. Use `<repo parent>/ComfyUI`, for example `E:\ComfyUI` for a clone at `E:\gaiavfx`. From the repository root in PowerShell: `python -m gaiavfx.setup comfy install (Join-Path (Split-Path $PWD -Parent) 'ComfyUI')`; in Bash: `python -m gaiavfx.setup comfy install "$(dirname "$PWD")/ComfyUI"`.

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
                                                 [--model {swift-1.5,qwen3.8-base}]
                                                 [--model-only | --server-only]
```

Defaults to `.runtime/assistant`: archives under `downloads/`, extracted llama.cpp under `server/`, and the GGUF plus F16 vision projector under `models/`. Saves the selected paths as `assistant_executable`, `assistant_model` and `assistant_projector` in setup.json as applicable. Doctor accepts either model. `--model-only` and `--server-only` are mutually exclusive.

`--model swift-1.5` is the default: public UkisAI Swift 1.5 Qwen3.8-27B Q4_K_M and its projector, about 18.4 GB together, pinned to commit `14bfe4b42be4a925d98816db830155f476c605e7`. UkisAI's contributions use Swift Open License v1.0 (free for individuals and organisations under US$1M gross annual revenue; above that, a separate enterprise licence is required), on an Apache-2.0 base. See the [publisher's model repository](https://huggingface.co/ukisai/Swift-1.5-Qwen3.8-27B-GGUF) for terms.

`python -m gaiavfx.setup assistant install --model qwen3.8-base` selects the public Unsloth Qwen3.8-27B UD-Q4_K_M and projector (Apache-2.0, about 17.4 GB). Both use pinned llama.cpp b10549. Windows includes CUDA 12.4 runtime files; Linux is CPU-only. No server is started. These model choices have not been inference-qualified by this change.

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
