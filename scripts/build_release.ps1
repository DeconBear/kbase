# KBase Release Builder
# Builds both portable (zip) and installer (NSIS) artifacts for a release.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1 -Version "0.4.0"
#
# Prerequisites:
#   - PyInstaller (pip install pyinstaller)
#   - NSIS installed at C:\Program Files (x86)\NSIS\makensis.exe
#   - Working kbase.spec pointing to kb/desktop.py
param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot\..
$RepoRoot = (Get-Location).Path

# --- Resolve version ---
if (-not $Version) {
    $versionPy = Join-Path $RepoRoot "kb\version.py"
    $content = Get-Content $versionPy -Raw -Encoding UTF8
    if ($content -match 'VERSION\s*=\s*"([^"]+)"') {
        $Version = $Matches[1]
    } else {
        $Version = "0.3.0"
    }
}
Write-Host "=== Building KBase v$Version ==="

# --- Stage 1: PyInstaller (portable) ---
Write-Host "[1/4] Running PyInstaller..."
$pyiArgs = @("-m", "PyInstaller", "--noconfirm", "kbase.spec")
$proc = Start-Process python -ArgumentList $pyiArgs -Wait -PassThru -NoNewWindow
if ($proc.ExitCode -ne 0) {
    Write-Error "PyInstaller failed (exit $($proc.ExitCode))"
    Pop-Location; exit 1
}
if (-not (Test-Path "dist\KBase\KBase.exe")) {
    Write-Error "dist\KBase\KBase.exe not found after PyInstaller"
    Pop-Location; exit 1
}

# --- Stage 2: Portable zip ---
Write-Host "[2/4] Creating portable zip..."
$zipName = "KBase-v$Version-portable.zip"
$zipPath = "dist\$zipName"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path "dist\KBase\*" -DestinationPath $zipPath -Force
$zipSize = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host "        $zipName ($zipSize MB)"

# --- Stage 3: NSIS installer ---
Write-Host "[3/4] Building NSIS installer..."
$nsis = "C:\Program Files (x86)\NSIS\makensis.exe"
if (-not (Test-Path $nsis)) {
    $nsis = "makensis.exe"
}
$nsisArgs = @("/DVERSION=$Version", "installer.nsi")
$nsisProc = Start-Process $nsis -ArgumentList $nsisArgs -Wait -PassThru -NoNewWindow
if ($nsisProc.ExitCode -ne 0) {
    Write-Error "NSIS failed (exit $($nsisProc.ExitCode))"
    Pop-Location; exit 1
}
$setupName = "KBase-Setup-v$Version.exe"
if (Test-Path "dist\$setupName") {
    $setupSize = [math]::Round((Get-Item "dist\$setupName").Length / 1MB, 1)
    Write-Host "        $setupName ($setupSize MB)"
} else {
    Write-Error "dist\$setupName not found after NSIS"
    Pop-Location; exit 1
}

# --- Stage 4: Summary ---
Write-Host "[4/4] Done."
Write-Host ""
Write-Host "  Portable:  dist\$zipName"
Write-Host "  Installer: dist\$setupName"
Write-Host ""
Write-Host "Next: create a GitHub Release and upload both files."
Write-Host "  gh release create v$Version dist\$zipName dist\$setupName --title 'v$Version' --notes '...'"

Pop-Location
