param(
  [Parameter(Mandatory = $true)][string]$Media,
  [switch]$Record
)

$ErrorActionPreference = 'Stop'
$wheels = @(Get-ChildItem -LiteralPath (Join-Path $Media 'wheels') -Filter '*.whl' -File)
if ($wheels.Count -ne 2 -or
    @($wheels | Where-Object { $_.Name -like 'av-*.whl' }).Count -ne 1 -or
    @($wheels | Where-Object { $_.Name -like 'opencv_python_headless-*.whl' }).Count -ne 1) {
  throw 'Expected one PyAV wheel and one headless OpenCV wheel'
}
$helperDir = Join-Path $Media 'standalone\bin'
$files = @(
  Get-Item -LiteralPath (Join-Path $helperDir 'ffmpeg.exe')
  Get-Item -LiteralPath (Join-Path $helperDir 'ffprobe.exe')
  $wheels | Sort-Object Name
)

$hashes = [ordered]@{}
foreach ($file in $files) {
  $relative = [IO.Path]::GetRelativePath($Media, $file.FullName).Replace('\', '/')
  $hashes[$relative] = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
}
$computed = ConvertTo-Json -InputObject $hashes -Compress
$manifest = Join-Path $Media 'SHA256SUMS.json'
if ($Record) {
  Set-Content -LiteralPath $manifest -Value $computed -Encoding utf8
} elseif ((Get-Content -LiteralPath $manifest -Raw).Trim() -cne $computed) {
  throw 'Cached Windows media outputs failed their SHA-256 check'
}
