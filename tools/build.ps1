$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $root '.packaging\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $basePython = (Get-Command python -ErrorAction Stop).Source
    & $basePython -m venv (Join-Path $root '.packaging')
    if ($LASTEXITCODE -ne 0) { throw 'Could not create packaging environment' }
    & $python -m pip install --disable-pip-version-check --no-input pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'Could not install PyInstaller' }
}
$dist = [IO.Path]::GetFullPath((Join-Path $root 'dist'))
$build = [IO.Path]::GetFullPath((Join-Path $root 'build'))
$env:PYINSTALLER_CONFIG_DIR = Join-Path $root '.pyinstaller-cache'
foreach ($path in @($dist,$build)) {
    if (-not $path.StartsWith($root + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {
        throw "Build output escaped the project: $path"
    }
}
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --name CodexUsageWidget `
    --icon (Join-Path $root 'assets\icon.ico') `
    --add-data ((Join-Path $root 'assets') + ';assets') `
    --distpath $dist --workpath $build --specpath $root (Join-Path $root 'main.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
Get-Item -LiteralPath (Join-Path $dist 'CodexUsageWidget.exe') | Select-Object FullName,Length
