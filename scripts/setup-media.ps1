$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$version = '9.0.1'
$expectedHash = 'fec81ae03971d9dd4be3ebe02e263bd2ec1d789483f931bdba5f5715e65da2e9'
$cache = Join-Path $workspace '.cache/media'
$destination = Join-Path $workspace '.runtime/media'
New-Item -ItemType Directory -Path $cache, $destination -Force | Out-Null
$archive = Join-Path $cache "ffmpeg-$version-essentials_build.zip"
if (!(Test-Path -LiteralPath $archive)) {
  Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-$version-essentials_build.zip" -OutFile $archive
}
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
  throw 'FFmpeg archive checksum does not match the pinned release.'
}
$expanded = Join-Path $cache "ffmpeg-$version"
if (!(Test-Path -LiteralPath $expanded)) { Expand-Archive -LiteralPath $archive -DestinationPath $expanded }
$release = Join-Path $expanded "ffmpeg-$version-essentials_build"
Copy-Item -LiteralPath (Join-Path $release 'bin/ffmpeg.exe'), (Join-Path $release 'bin/ffprobe.exe'), (Join-Path $release 'LICENSE') -Destination $destination -Force
@{version=$version; sha256=$expectedHash; source="https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-$version-essentials_build.zip"} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destination 'provenance.json') -Encoding utf8
& (Join-Path $destination 'ffmpeg.exe') -version | Select-Object -First 1
& (Join-Path $destination 'ffprobe.exe') -version | Select-Object -First 1
