#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$ComfyDir,
    [string]$InstallComfy,
    [string[]]$Features = @('core', 'image-edit', 'stages', 'recast'),
    [switch]$SkipModels,
    [switch]$WithAssistant,
    [switch]$WithStagesEngine,
    [switch]$NonInteractive
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($ComfyDir -and $InstallComfy) { throw 'Use either -ComfyDir or -InstallComfy.' }
$hints = [ordered]@{
    git = 'winget install --id Git.Git -e'
    uv = 'winget install --id astral-sh.uv -e'
    node = 'winget install --id OpenJS.NodeJS.LTS -e'
    npm = 'Install Node.js (includes npm): winget install --id OpenJS.NodeJS.LTS -e'
}
$missing = $false
foreach ($tool in $hints.Keys) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Write-Host "Missing ${tool}. $($hints[$tool])"
        $missing = $true
    }
}
if ($missing) { exit 1 }
$nodeMajor = & node -p 'parseInt(process.versions.node)'
if ($LASTEXITCODE -ne 0 -or [int]$nodeMajor -lt 24) {
    Write-Host 'Node.js 24 or newer is required. winget install --id OpenJS.NodeJS.LTS -e'
    exit 1
}
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
if ($WithAssistant) { $installArgs += '--with-assistant' }
if ($WithStagesEngine) { $installArgs += '--with-stages-engine' }
if ($NonInteractive) { $installArgs += '--non-interactive' }
& $python @installArgs
exit $LASTEXITCODE
