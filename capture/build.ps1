# Builds capture\dist\OWScoreboardCapture.exe (one file, no console window).
# Run from any directory:  powershell -ExecutionPolicy Bypass -File capture\build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
python -m pip install --quiet --upgrade -r requirements-build.txt
python -m PyInstaller --noconfirm --clean --onefile --windowed --name OWScoreboardCapture ow_capture.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Host "Built $PSScriptRoot\dist\OWScoreboardCapture.exe"
