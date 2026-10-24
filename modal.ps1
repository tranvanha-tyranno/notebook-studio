param(
    [ValidateSet('setup', 'studio', 'status', 'login', 'test')]
    [string]$Action = 'studio'
)
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location -LiteralPath $PSScriptRoot
$StudioPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($Action -eq 'setup') {
    if (-not (Test-Path -LiteralPath $StudioPython)) {
        & py -3.11 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.11 and retry setup.' }
    }
    & $StudioPython -m pip install -r requirements-modal.txt
} else {
    if (-not (Test-Path -LiteralPath $StudioPython)) { throw 'Run .\modal.ps1 setup first.' }
    switch ($Action) {
        'studio' { & $StudioPython -X utf8 studio.py }
        'status' { & $StudioPython -m modal profile list }
        'login' { & $StudioPython -m modal setup }
        'test' { & $StudioPython -m unittest discover -s tests -v }
    }
}
if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE)." }
