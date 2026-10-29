# GAIAVFX

GAIAVFX is a local-first, chat-first agentic movie workspace powered by ComfyUI. Prepare references, direct shots with Gaia, review takes, build sets, recast characters and work with voice in one desktop workspace. Inference engines and model weights are installed separately.

Development setup (PowerShell, Python 3.12, uv and Node.js):

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$PWD/.venv"
uv sync --project backend --python 3.12
npm ci
npm run setup:media
npm run dev
```

Run `npm run desktop` for Electron. Copy `comfy_nodes/gaiavfx` into your ComfyUI `custom_nodes` folder; its README describes optional face dependencies. Configure ComfyUI and engines in the app. See `AGENTS.md` for verification commands.

Licensed under GPL-3.0-or-later; see [LICENSE](LICENSE).
