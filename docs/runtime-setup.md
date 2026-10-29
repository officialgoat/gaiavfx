# Install and start GAIAVFX

Windows 11 with an NVIDIA GPU is the primary target. Linux x86-64 uses the same Python installers. Install Git, uv, and Node.js 24 or newer (which includes npm), then open a terminal in this checkout.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

```bash
bash install.sh
bash start.sh
```

The installer checks prerequisites, creates the root `.venv` from the lockfile, installs npm dependencies, downloads verified FFmpeg tools, builds the renderer, and offers ComfyUI and optional runtime setup. It finishes with the doctor summary. It never starts inference services. A checkout missing the ComfyUI setup commands prints which steps were skipped.

Doctor may report incomplete setup while ComfyUI is stopped, or when you deferred optional models/runtimes. The installer prints those findings without treating them as a failed download or build. Start the configured services and run `npm run doctor` again to check readiness; the standalone doctor retains its strict exit status.

For an unattended Windows installation:

```powershell
.\install.ps1 -InstallComfy .runtime\ComfyUI -Features core,image-edit,stages,recast -WithStagesEngine -WithAssistant -NonInteractive
```

Use `-ComfyDir <path>` for an existing ComfyUI instead of `-InstallComfy`. `-SkipModels` defers the large ComfyUI model downloads. Omit `-WithAssistant` or `-WithStagesEngine` to defer those optional runtimes. The feature defaults are `core`, `image-edit`, `stages`, and `recast`.

The equivalent bash flags are `--install-comfy DIR`, `--comfy-dir DIR`, `--features core image-edit stages recast`, `--skip-models`, `--with-assistant`, `--with-stages-engine`, and `--non-interactive`.

`start.ps1 -WithComfy` or `bash start.sh --with-comfy` launches the configured ComfyUI in a separate console/process before starting the workspace. Its output is inherited, never pipe-captured. Stop that ComfyUI console/process separately when finished. The default start command launches only the app.

## Individual runtimes

The npm wrapper selects `.venv/Scripts/python.exe` on Windows and `.venv/bin/python` on Linux. These commands also work as `python -m gaiavfx.setup ...` from the activated environment.

```text
npm run setup -- media install
npm run setup -- engines install worldbuilder [--root DIR]
npm run setup -- assistant install [--dir DIR] [--model-only | --server-only]
npm run doctor
```

Media installs two binaries, their provenance, and the archive's licence notice in `.runtime/media`. `GAIAVFX_MEDIA_DIR` overrides the installation directory. Runtime discovery checks that override, the repo runtime, then PATH. Windows uses Gyan's pinned 9.0.1 archive; Linux uses the pinned BtbN 9.0-family archive and needs its documented glibc baseline.

The stages engine lives under `.runtime/engines/worldbuilder` by default. It uses its own Python 3.10.19 environment, torch 2.4.1 with CUDA 12.4, and gsplat 1.5.3. Installation records the root and writes the package manifest and probe evidence used by Settings. Installation probes CUDA once; doctor only reads that evidence. Voice engines remain experimental with manual setup. `scripts/engine_setup.py` is retained for the existing voice adoption, verification and unlink workflows.

The optional assistant installs llama.cpp b10549 and the public [Unsloth Qwen3.8-27B GGUF](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF/tree/4ca720788d1e01f1bff70c033e0d0028fd02e502), using its UD-Q4_K_M quantization and F16 vision projector. Downloads are revision-pinned, resumable and SHA-256 verified. The model and projector total about 17.4 GB before server files. Windows includes the CUDA 12.4 server and matching CUDA runtime. The Linux prebuilt server is CPU-only; build llama.cpp with CUDA for GPU acceleration, or add any OpenAI-compatible local endpoint (LM Studio, Ollama or vLLM) in Settings instead.

The previous `orcarouter/Qwen3.8-27B-Uncensored-GGUF` is an alternative requiring accepted gated access and `HF_TOKEN`. Its model/projector hashes remain unresolved, so those alternative entries have empty download URLs and cannot be installed automatically. No token is stored by these installers.

Paths are merged into `.runtime/setup.json` (`GAIAVFX_SETUP_FILE` overrides it). Existing connection keys are preserved. Runtime/model files and caches remain outside version control.
