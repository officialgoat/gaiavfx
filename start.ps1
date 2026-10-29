#requires -Version 5.1
[CmdletBinding()]
param([switch]$WithComfy, [string[]]$ComfyArgs = @())
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($WithComfy) {
    & (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') scripts/start_comfy.py @ComfyArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
& npm.cmd run dev
exit $LASTEXITCODE
