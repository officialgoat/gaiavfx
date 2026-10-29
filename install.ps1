#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$ComfyDir,
    [string]$InstallComfy,
    [string[]]$Features = @('core', 'image-edit', 'stages', 'recast'),
    [switch]$SkipModels,
    [switch]$SkipAssistant,
    [switch]$SkipStagesEngine,
    [switch]$WithAssistant,
    [switch]$WithStagesEngine,
    [switch]$NonInteractive,
    [switch]$InstallPrerequisites
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($ComfyDir -and $InstallComfy) { throw 'Use either -ComfyDir or -InstallComfy.' }
. (Join-Path $PSScriptRoot 'scripts/prerequisites.ps1') -NonInteractive:$NonInteractive -InstallPrerequisites:$InstallPrerequisites
if (-not (Ensure-Prerequisites -NonInteractive:$NonInteractive -InstallPrerequisites:$InstallPrerequisites)) { exit 1 }
$env:UV_PROJECT_ENVIRONMENT = Join-Path $PSScriptRoot '.venv'
& uv sync --project backend --python 3.12 --locked
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& npm.cmd ci
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
& $python -m gaiavfx.setup media install
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& npm.cmd run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$installArgs = @('scripts/install.py', '--features') + $Features
if ($ComfyDir) { $installArgs += @('--comfy-dir', $ComfyDir) }
if ($InstallComfy) { $installArgs += @('--install-comfy', $InstallComfy) }
if ($SkipModels) { $installArgs += '--skip-models' }
if ($SkipAssistant) { $installArgs += '--skip-assistant' }
if ($SkipStagesEngine) { $installArgs += '--skip-stages-engine' }
if ($WithAssistant) { $installArgs += '--with-assistant' }
if ($WithStagesEngine) { $installArgs += '--with-stages-engine' }
if ($NonInteractive) { $installArgs += '--non-interactive' }
if ($InstallPrerequisites) { $installArgs += '--install-prerequisites' }
& $python @installArgs
exit $LASTEXITCODE
