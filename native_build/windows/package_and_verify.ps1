$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $env:EDITARR_WINDOWS_SOURCE
function Invoke-Native13 {
  param(
    [Parameter(Mandatory = $true)][string]$Description,
    [Parameter(Mandatory = $true)][scriptblock]$Command
  )
  & $Command
  if ($LASTEXITCODE -ne 0) {
    throw "$Description failed with exit code $LASTEXITCODE"
  }
}

function Get-GuiExitCode {
  param([string]$Program, [string[]]$Arguments)
  # Wait on the GUI stub only. A launcher may intentionally leave the detached
  # controller running after the stub exits.
  $start = [Diagnostics.ProcessStartInfo]::new($Program)
  $start.UseShellExecute = $false
  foreach ($argument in $Arguments) { $start.ArgumentList.Add($argument) }
  $process = [Diagnostics.Process]::Start($start)
  try {
    if (-not $process.WaitForExit(900000)) {
      $process.Kill($true)
      throw "$Program did not exit"
    }
    return $process.ExitCode
  } finally {
    $process.Dispose()
  }
}

function Invoke-CheckedGui {
  param([string]$Description, [string]$Program, [string[]]$Arguments)
  $result = Get-GuiExitCode $Program $Arguments
  if ($result -ne 0) { throw "$Description failed with exit code $result" }
}

$packageOutput = Join-Path $env:EDITARR_NATIVE13_TASK_ROOT 'package output'
$env:LOCALAPPDATA = Join-Path $env:EDITARR_NATIVE13_TASK_ROOT 'localappdata'
$installRoot = Join-Path $env:EDITARR_NATIVE13_TASK_ROOT 'installed Editarr ✓'
$installedEvidence = Join-Path $env:RUNNER_TEMP 'native13-installed-verifier.json'
$installLog = Join-Path $env:RUNNER_TEMP 'native13-inno-install.log'
$uninstallLog = Join-Path $env:RUNNER_TEMP 'native13-inno-uninstall.log'
$release = [bool]$env:RELEASE_VERSION
$packageArguments = @('--unsigned-development')
if ($release) {
  $notices = Join-Path $env:EDITARR_WINDOWS_BOOTSTRAP 'notices'
  $packageArguments = @('--unsigned-alpha', '--notices', $notices)
}
Invoke-Native13 'package unsigned installer' {
  uv run python -m scripts.package_windows --runtime $env:EDITARR_NATIVE13_RUNTIME --out-dir $packageOutput --work-dir $env:TEMP @packageArguments
}
$installers = @(Get-ChildItem -LiteralPath $packageOutput -Filter '*Setup*.exe' -File)
if ($installers.Count -ne 1) { throw 'Expected exactly one generated Inno Setup installer' }
$installer = $installers[0]
$installArguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=$installRoot", '/TASKS=autostart', "/LOG=$installLog")
Invoke-CheckedGui 'per-user install' $installer.FullName $installArguments
if (-not (Test-Path -LiteralPath (Join-Path $installRoot 'Editarr.exe'))) {
  throw 'Installed tray launcher is missing'
}
if (-not (Test-Path -LiteralPath (Join-Path $installRoot 'Runtime\Editarr.exe'))) {
  throw 'Installed frozen runtime is missing'
}
if ($release) {
  $noticePath = 'THIRD-PARTY-NOTICES.md'
  $upstreamNotice = Get-FileHash (Join-Path $notices $noticePath)
  $installedNotice = Get-FileHash (Join-Path $installRoot "Licenses\$noticePath")
  if ($installedNotice.Hash -ne $upstreamNotice.Hash) { throw 'Installed release notices differ from the source notices' }
}
$runValue = Get-ItemPropertyValue -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name Editarr
if ($runValue -ne ('"' + (Join-Path $installRoot 'Editarr.exe') + '" --background')) {
  throw "Unexpected launch-at-login command: $runValue"
}

$sentinelRoot = Join-Path $env:LOCALAPPDATA 'Editarr\Data'
New-Item -ItemType Directory -Force $sentinelRoot | Out-Null
$sentinel = Join-Path $sentinelRoot 'native13-preserve.txt'
Set-Content -LiteralPath $sentinel -Value 'preserve' -NoNewline
$launcher = Join-Path $installRoot 'Editarr.exe'
Invoke-CheckedGui 'launcher start' $launcher @('--start')
$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
  if ((Get-GuiExitCode $launcher @('--status')) -eq 0) { $ready = $true; break }
  Start-Sleep -Seconds 1
}
if (-not $ready) { throw 'Installed runtime did not reach launcher status Running' }
Invoke-CheckedGui 'second launcher start' $launcher @('--start')
Invoke-Native13 'diagnostic command' { & (Join-Path $installRoot 'Runtime\Editarr.exe') --foreground --help }
Invoke-CheckedGui 'launcher stop' $launcher @('--stop')

Invoke-Native13 'installed NATIVE-11 verifier' {
  uv run python -m scripts.verify_installed_release --runtime (Join-Path $installRoot 'Runtime') --target windows-x64 --json-out $installedEvidence
}

Invoke-CheckedGui 'same-version upgrade' $installer.FullName $installArguments
if (-not (Test-Path -LiteralPath $sentinel)) { throw 'Upgrade removed durable data' }

$uninstaller = Join-Path $installRoot 'unins000.exe'
Invoke-CheckedGui 'ordinary uninstall' $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$uninstallLog")
if (Test-Path -LiteralPath $sentinel) {
  Write-Host 'NATIVE13_DURABLE_DATA_PRESERVED=true'
} else {
  throw 'Ordinary uninstall removed durable data'
}
if (Get-ItemProperty -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name Editarr -ErrorAction SilentlyContinue) {
  throw 'Uninstall left the Editarr launch-at-login value behind'
}
$packageEvidence = Get-Content -LiteralPath (Join-Path $packageOutput 'editarr-windows-package.json') -Raw | ConvertFrom-Json
Write-Host "NATIVE13_INSTALLER_SHA256=$($packageEvidence.installer_sha256)"
Write-Host "NATIVE13_INSTALLER_SIZE_BYTES=$($packageEvidence.installer_size_bytes)"
Write-Host "NATIVE13_INSTALLER_SIGNED=$($packageEvidence.signed)"
Write-Host "NATIVE13_INSTALLER_DEVELOPMENT_ONLY=$($packageEvidence.development_only)"
if ($packageEvidence.signed -or $packageEvidence.development_only -eq $release) {
  throw 'Unsigned installer evidence was not labeled correctly'
}
Invoke-Native13 'git diff --check after installer lifecycle' { git diff --check }

if ($release) {
  $expectedName = "Editarr-v${env:RELEASE_VERSION}-Windows-x64-Setup.exe"
  $sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $installer.FullName).Hash.ToLowerInvariant()
  $verified = Get-Content -LiteralPath $installedEvidence -Raw | ConvertFrom-Json
  if ($installer.Name -ne $expectedName -or $sha256 -ne $packageEvidence.installer_sha256 -or
      (Get-AuthenticodeSignature -LiteralPath $installer.FullName).Status -ne 'NotSigned' -or
      $packageEvidence.publisher_identity_approved -ne $false -or
      $packageEvidence.version -ne $env:RELEASE_VERSION -or
      $packageEvidence.source_revision -ne $verified.source_revision -or
      $verified.version -ne $env:RELEASE_VERSION -or $verified.target -ne 'windows-x64' -or
      $verified.failures.Count -ne 0 -or $verified.checks.scratch_cleanup.removed -ne $true) {
    throw 'Installer checksum or verification does not match the Windows release'
  }
  $releaseAssets = Join-Path $env:RUNNER_TEMP 'windows-release-assets'
  Invoke-Native13 'stage Windows release distribution' {
    uv run --no-sync python -m scripts.prepare_alpha_distribution package windows-x64 $env:EDITARR_WINDOWS_BOOTSTRAP $installer.FullName $releaseAssets
  }
}
