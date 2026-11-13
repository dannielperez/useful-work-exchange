$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
if (-not (Test-Path .venv/Scripts/python.exe)) {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { $runtime = $python.Source; & $runtime -c 'import sys; sys.exit(sys.version_info < (3,12))' }
    if (-not $python -or $LASTEXITCODE -ne 0) {
        $runtime = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    }
    & $runtime -c 'import sys; sys.exit(sys.version_info < (3,12))'
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 or newer is required' }
    & $runtime -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed' }
}
& .venv/Scripts/python.exe -c 'import sys; sys.exit(sys.version_info < (3,12))'
if ($LASTEXITCODE -ne 0) { throw 'Existing .venv needs Python 3.12 or newer' }
& .venv/Scripts/python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& .venv/Scripts/python.exe scripts/ai/shift.py --setup-only
if ($LASTEXITCODE -ne 0) { throw 'Shift preflight failed' }
