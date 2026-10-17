param(
    [string]$PythonExecutable = (Join-Path (Split-Path $PSScriptRoot -Parent) '.venv\Scripts\python.exe'),
    [string]$PackagesDirectory = (Join-Path (Split-Path $PSScriptRoot -Parent) '.venv\Lib\site-packages')
)
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path $PSScriptRoot -Parent
$env:PYTHONPATH = $PackagesDirectory
$env:PDF2ZH_BUILD_PACKAGES = $PackagesDirectory
& $PythonExecutable -m PyInstaller --noconfirm --distpath (Join-Path $projectDirectory 'release\程序') --workpath (Join-Path $PSScriptRoot 'build') (Join-Path $PSScriptRoot 'PDF2ZH.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
