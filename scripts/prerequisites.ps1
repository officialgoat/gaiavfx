#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Plan,
    [string[]]$SimulateMissing,
    [ValidateSet('absent', 'present', 'registers')][string]$SimulateWinget = 'absent',
    [switch]$NonInteractive,
    [switch]$InstallPrerequisites,
    [switch]$ForcePortable,
    [string]$Destination
)

$script:PrerequisiteManifestPath = Join-Path $PSScriptRoot 'prerequisites.json'
$script:PrerequisiteToolsRoot = Join-Path (Split-Path $PSScriptRoot -Parent) '.runtime/tools'
if (-not $Destination) { $Destination = $script:PrerequisiteToolsRoot }

function Add-PortableToolsPath {
    param([string]$ToolsRoot = $script:PrerequisiteToolsRoot)
    $folders = @('git/cmd', 'uv', 'node') | ForEach-Object {
        $folder = Join-Path $ToolsRoot $_
        if (Test-Path -LiteralPath $folder -PathType Container) { $folder }
    }
    $env:Path = (@($folders) + @($env:Path -split ';') | Select-Object -Unique) -join ';'
}

function Get-MissingPrerequisites {
    foreach ($tool in @('git', 'uv', 'node', 'npm')) {
        $executable = if ($tool -eq 'npm') { 'npm.cmd' } else { $tool }
        if (-not (Get-Command $executable -CommandType Application -ErrorAction SilentlyContinue)) {
            $tool
        } elseif ($tool -eq 'node') {
            try {
                $major = & node -p 'parseInt(process.versions.node)' 2>$null
                if ($LASTEXITCODE -ne 0 -or "$major" -notmatch '^\d+$' -or [int]$major -lt 24) { 'node' }
            } catch { 'node' }
        }
    }
}

function Get-PrerequisitePackages {
    param([string[]]$Missing)
    @($Missing | ForEach-Object { if ($_ -eq 'npm') { 'node' } else { $_ } } | Select-Object -Unique)
}

function Get-WingetArguments {
    param([string]$Tool)
    $ids = @{ git = 'Git.Git'; uv = 'astral-sh.uv'; node = 'OpenJS.NodeJS.LTS' }
    @('install', '--id', $ids[$Tool], '-e', '--source', 'winget', '--accept-package-agreements', '--accept-source-agreements')
}

function Get-PrerequisiteHints {
    @(
        'Install missing prerequisites manually, then rerun the installer (or pass -InstallPrerequisites to consent).',
        'winget / App Installer: https://aka.ms/getwinget',
        'Git: https://git-scm.com/download/win (winget install --id Git.Git -e)',
        'uv: https://docs.astral.sh/uv/getting-started/installation/ (winget install --id astral-sh.uv -e)',
        'Node.js 24+ with npm: https://nodejs.org/en/download (winget install --id OpenJS.NodeJS.LTS -e)'
    )
}

function Update-PrerequisitePath {
    param([string]$ToolsRoot)
    $defaults = @(
        "$env:ProgramFiles\Git\cmd", "$env:ProgramFiles\nodejs",
        "$env:LOCALAPPDATA\Programs\Git\cmd", "$env:LOCALAPPDATA\Programs\nodejs",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links", "$env:LOCALAPPDATA\Microsoft\WindowsApps",
        "$env:USERPROFILE\.cargo\bin", (Join-Path (Join-Path $env:USERPROFILE '.local') 'bin')
    )
    $paths = @([Environment]::GetEnvironmentVariable('Path', 'Machine'), [Environment]::GetEnvironmentVariable('Path', 'User'))
    $env:Path = (@($paths) + $defaults + @($env:Path)) -join ';'
    $env:Path = [Environment]::ExpandEnvironmentVariables($env:Path)
    Add-PortableToolsPath -ToolsRoot $ToolsRoot
}

function Install-PortablePrerequisite {
    param([string]$Tool, [string]$ToolsRoot)
    if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
        throw 'Portable prerequisites require x64 Windows.'
    }
    $spec = (Get-Content -LiteralPath $script:PrerequisiteManifestPath -Raw | ConvertFrom-Json).windows.$Tool
    $root = [IO.Path]::GetFullPath($ToolsRoot)
    $downloads = Join-Path $root 'downloads'
    New-Item -ItemType Directory -Force -Path $downloads | Out-Null
    $archive = Join-Path $downloads ([IO.Path]::GetFileName(([uri]$spec.url).AbsolutePath))
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'
    Write-Host "Downloading $Tool $($spec.version): $($spec.url)"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri $spec.url -OutFile $archive -ErrorAction Stop
    } catch {
        # Some restricted Windows sessions cannot obtain Schannel credentials.
        # An existing app Python uses its own TLS stack. It is optional, never
        # bootstrapped here; fresh checkouts can use curl if available instead.
        $existingPython = Join-Path (Split-Path $script:PrerequisiteManifestPath -Parent) '../.venv/Scripts/python.exe'
        if (Test-Path -LiteralPath $existingPython -PathType Leaf) {
            Write-Host 'Invoke-WebRequest failed; retrying the official URL with existing app Python.'
            & $existingPython -c 'import shutil, sys, urllib.request; response = urllib.request.urlopen(sys.argv[1], timeout=120); output = open(sys.argv[2], ''wb''); shutil.copyfileobj(response, output); output.close(); response.close()' $spec.url $archive
        } elseif (Get-Command curl.exe -CommandType Application -ErrorAction SilentlyContinue) {
            Write-Host 'Invoke-WebRequest failed; retrying the official URL with curl.'
            & curl.exe --fail --location --silent --show-error --proto '=https' --proto-redir '=https' --output $archive $spec.url
        } else { throw }
        if ($LASTEXITCODE -ne 0) { throw "Download failed for $Tool (transport exit $LASTEXITCODE)." }
    }
    $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($hash -ne $spec.sha256 -or (Get-Item -LiteralPath $archive).Length -ne $spec.bytes) {
        Remove-Item -LiteralPath $archive -Force
        throw "SHA-256 or size mismatch for $Tool; deleted $archive. No files extracted."
    }
    # Extract into a fresh sibling; publish only after checking the expected payload.
    $staging = Join-Path $root ('.extract-' + $Tool + '-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $staging | Out-Null
    try {
        Expand-Archive -LiteralPath $archive -DestinationPath $staging -ErrorAction Stop
        $payload = if ($spec.strip_prefix) { Join-Path $staging $spec.strip_prefix } else { $staging }
        $expected = @{ git = @('cmd/git.exe'); uv = @('uv.exe'); node = @('node.exe', 'npm.cmd') }
        foreach ($file in $expected[$Tool]) {
            if (-not (Test-Path -LiteralPath (Join-Path $payload $file) -PathType Leaf)) { throw "Archive missing $file" }
        }
        $target = Join-Path $root $Tool
        # All recursive operations stay within the explicitly selected tools directory.
        foreach ($path in @($target, $staging)) {
            if (-not ([IO.Path]::GetFullPath($path).StartsWith($root.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase))) {
                throw "Unsafe tools path: $path"
            }
        }
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        Move-Item -LiteralPath $payload -Destination $target
    } finally {
        if ([IO.Path]::GetFullPath($staging).StartsWith($root.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $staging)) {
            Remove-Item -LiteralPath $staging -Recurse -Force
        }
    }
    Write-Host "Verified and installed $Tool $($spec.version) in $target"
}

function Get-PrerequisitePlan {
    param([string[]]$Missing, [string]$Winget, [switch]$NonInteractive, [switch]$InstallPrerequisites, [switch]$ForcePortable)
    $steps = @()
    $allowed = -not $NonInteractive -or $InstallPrerequisites -or $ForcePortable
    if ($Missing.Count -and $allowed) {
        if (-not $ForcePortable -and $Winget -ne 'present') {
            $steps += @{ method = 'register'; command = @('Add-AppxPackage', '-RegisterByFamilyName', '-MainPackage', 'Microsoft.DesktopAppInstaller_8wekyb3d8bbwe') }
        }
        foreach ($tool in (Get-PrerequisitePackages $Missing)) {
            $spec = (Get-Content -LiteralPath $script:PrerequisiteManifestPath -Raw | ConvertFrom-Json).windows.$tool
            $portable = @{ method = 'portable'; tool = $tool; url = $spec.url; sha256 = $spec.sha256; bytes = $spec.bytes; version = $spec.version }
            if (-not $ForcePortable -and $Winget -in @('present', 'registers')) {
                $steps += @{ method = 'winget'; tool = $tool; command = @('winget') + (Get-WingetArguments $tool); fallback = $portable }
            } else { $steps += $portable }
        }
    }
    $code = if ($Missing.Count -and -not $allowed) { 1 } else { 0 }
    @{ missing = @($Missing); steps = @($steps); consent = $(if ($InstallPrerequisites -or $ForcePortable) { 'explicit' } elseif ($NonInteractive) { 'required' } else { 'prompt' }); hints = @(Get-PrerequisiteHints); exit_code = $code }
}

function Ensure-Prerequisites {
    param([switch]$NonInteractive, [switch]$InstallPrerequisites, [switch]$ForcePortable, [string]$ToolsRoot = $script:PrerequisiteToolsRoot)
    Add-PortableToolsPath -ToolsRoot $ToolsRoot
    $missing = if ($ForcePortable) { @('git', 'uv', 'node', 'npm') } else { @(Get-MissingPrerequisites) }
    if (-not $missing.Count) { return $true }
    $winget = -not $ForcePortable -and [bool](Get-Command winget -ErrorAction SilentlyContinue)
    $method = if ($ForcePortable) { "verified portable archives in $ToolsRoot" } elseif ($winget) { 'winget, with verified portable archives if a package fails' } else { 'App Installer re-registration, then winget if available; otherwise verified portable archives' }
    if (-not $InstallPrerequisites -and -not $ForcePortable) {
        if ($NonInteractive -or -not [Environment]::UserInteractive -or [Console]::IsInputRedirected) {
            Write-Host "Missing prerequisites: $($missing -join ', ')"
            Get-PrerequisiteHints | ForEach-Object { Write-Host $_ }
            return $false
        }
        Write-Host "Method: $method. Installing accepts these packages' licence terms."
        do { $answer = Read-Host "Install the missing prerequisites ($($missing -join ', ')) now? [Y/n]" } while ($answer -notmatch '^(?i:y|yes|n|no)?$')
        if ($answer -match '^(?i:n|no)$') {
            Get-PrerequisiteHints | ForEach-Object { Write-Host $_ }
            return $false
        }
    } else { Write-Host "Method: $method. Your switch accepts these packages' licence terms." }
    if (-not $ForcePortable -and -not $winget) {
        try {
            Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop | Out-Null
        } catch { Write-Host "App Installer registration unavailable: $($_.Exception.Message)" }
        Update-PrerequisitePath -ToolsRoot $ToolsRoot
        $winget = [bool](Get-Command winget -ErrorAction SilentlyContinue)
    }
    $failed = $false
    foreach ($tool in (Get-PrerequisitePackages $missing)) {
        if ($winget) {
            try {
                $arguments = Get-WingetArguments $tool
                & winget @arguments | Out-Host
                if ($LASTEXITCODE -ne 0) { Write-Host "winget failed for $tool (exit $LASTEXITCODE); checking portable fallback." }
            } catch { Write-Host "winget failed for ${tool}: $($_.Exception.Message)" }
            Update-PrerequisitePath -ToolsRoot $ToolsRoot
            $remaining = @(Get-PrerequisitePackages @(Get-MissingPrerequisites))
            if ($tool -notin $remaining) { continue }
            if ($tool -eq 'node') { Write-Host 'Node LTS did not provide Node.js >=24 with npm; falling back to pinned Node 24.' }
        }
        try {
            Install-PortablePrerequisite -Tool $tool -ToolsRoot $ToolsRoot
            Add-PortableToolsPath -ToolsRoot $ToolsRoot
        } catch {
            Write-Host "Portable $tool failed: $($_.Exception.Message)"
            $failed = $true
        }
    }
    Add-PortableToolsPath -ToolsRoot $ToolsRoot
    $remaining = @(Get-MissingPrerequisites)
    if ($failed -or $remaining.Count) {
        Write-Host "Prerequisite setup incomplete. Missing: $($remaining -join ', ')"
        Get-PrerequisiteHints | ForEach-Object { Write-Host $_ }
        return $false
    }
    Write-Host "For npm in new terminals, optionally add $ToolsRoot\node, $ToolsRoot\uv and $ToolsRoot\git\cmd to your user PATH in Windows Environment Variables; no persistent PATH change was made."
    return $true
}

if ($MyInvocation.InvocationName -ne '.') {
    $ErrorActionPreference = 'Stop'
    if ($Plan) {
        # Simulation never probes commands, refreshes PATH, registers packages or writes files.
        $missing = @($SimulateMissing | ForEach-Object { $_ -split ',' } | Where-Object { $_ })
        if (-not $PSBoundParameters.ContainsKey('SimulateMissing')) { $missing = @('git', 'uv', 'node', 'npm') }
        foreach ($tool in $missing) { if ($tool -notin @('git', 'uv', 'node', 'npm')) { throw "Unknown prerequisite: $tool" } }
        $result = Get-PrerequisitePlan -Missing $missing -Winget $SimulateWinget -NonInteractive:$NonInteractive -InstallPrerequisites:$InstallPrerequisites -ForcePortable:$ForcePortable
        $result | ConvertTo-Json -Depth 8
        exit $result.exit_code
    }
    if (Ensure-Prerequisites -NonInteractive:$NonInteractive -InstallPrerequisites:$InstallPrerequisites -ForcePortable:$ForcePortable -ToolsRoot $Destination) { exit 0 }
    exit 1
}
