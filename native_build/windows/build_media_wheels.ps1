param(
  [Parameter(Mandatory = $true)][string]$Sources,
  [Parameter(Mandatory = $true)][string]$Media
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$pythonVersion = '3.11.16'
$prefix = Join-Path $Media 'prefix'
$sharedDlls = Join-Path $prefix 'bin'
$imports = Join-Path $Media 'msvc-imports'
$rawWheels = Join-Path $Media 'raw-wheels'
$wheels = Join-Path $Media 'wheels'
New-Item -ItemType Directory -Path $imports, $rawWheels, $wheels | Out-Null

# FFmpeg's .def lists supply the exports. Generate MSVC import libraries with
# the actual installed DLL names, then vendor those DLLs into the PyAV wheel.
foreach ($library in @('avcodec', 'avdevice', 'avfilter', 'avformat', 'avutil', 'swresample', 'swscale')) {
  $exports = @(Get-ChildItem -LiteralPath (Join-Path $prefix 'lib') -Filter "$library-*.def" -File)
  if ($exports.Count -ne 1) { throw "Missing or ambiguous FFmpeg exports for $library" }
  $dllName = $exports[0].BaseName + '.dll'
  if (-not (Test-Path -LiteralPath (Join-Path $sharedDlls $dllName) -PathType Leaf)) {
    throw "Missing FFmpeg library $dllName"
  }
  $definition = Join-Path $imports "$library.def"
  ('LIBRARY "' + $dllName + '"'), (Get-Content -LiteralPath $exports[0].FullName -Raw) |
    Set-Content -LiteralPath $definition -Encoding ascii
  lib.exe /nologo /machine:x64 "/def:$definition" "/out:$imports\$library.lib"
}

$oldInclude = $env:INCLUDE
$oldLib = $env:LIB
$oldSdkSetting = $env:DISTUTILS_USE_SDK
try {
  $env:DISTUTILS_USE_SDK = '1'
  $env:INCLUDE = "$prefix\include;$oldInclude"
  $env:LIB = "$imports;$oldLib"
  uv build --wheel --managed-python --python $pythonVersion --out-dir $rawWheels (Join-Path $Sources 'av-18.1.0.tar.gz')
} finally {
  $env:INCLUDE = $oldInclude
  $env:LIB = $oldLib
  $env:DISTUTILS_USE_SDK = $oldSdkSetting
}
$av = @(Get-ChildItem -LiteralPath $rawWheels -Filter 'av-*.whl' -File)
if ($av.Count -ne 1) { throw 'Expected one source-built PyAV wheel' }
uv tool run --python $pythonVersion --from delvewheel==1.13.2 delvewheel repair --add-path $sharedDlls --wheel-dir $wheels $av[0].FullName

tar.exe -xzf (Join-Path $Sources 'opencv_python_headless-5.0.0.93.tar.gz') -C $Media
$opencv = Join-Path $Media 'opencv_python_headless-5.0.0.93'
git -C $opencv apply --no-index (Join-Path $Sources 'opencv-optional-typing.patch')
git -C $opencv apply --no-index (Join-Path $PSScriptRoot 'opencv-no-windows-ffmpeg.patch')
$numpyHeaders = (uv run --no-sync python -c 'import numpy; print(numpy.get_include())').Replace('\', '/')
$env:ENABLE_HEADLESS = '1'
$env:CMAKE_BUILD_PARALLEL_LEVEL = $env:EDITARR_BUILD_JOBS
$env:CMAKE_ARGS = @(
  "-DPYTHON3_NUMPY_INCLUDE_DIRS=$numpyHeaders"
  '-DBUILD_LIST=core,imgproc,imgcodecs,videoio,dnn,python3'
  '-DWITH_FFMPEG=OFF', '-DWITH_MSMF=OFF', '-DWITH_DSHOW=OFF', '-DWITH_IPP=OFF'
  '-DWITH_OPENCL=OFF', '-DWITH_OPENEXR=OFF', '-DWITH_UNIFONT=OFF', '-DWITH_KLEIDICV=OFF'
  '-DWITH_ITT=OFF', '-DBUILD_TESTS=OFF', '-DBUILD_PERF_TESTS=OFF', '-DBUILD_EXAMPLES=OFF'
  '-DCMAKE_ASM_COMPILER='
) -join ' '
# OpenCV's packaging sets the VS generator platform on Windows. Use the
# matching Visual Studio generator and SDK already selected by the runner.
$env:CMAKE_GENERATOR = 'Visual Studio 17 2022'
uv build --wheel --managed-python --python $pythonVersion --out-dir $wheels $opencv
