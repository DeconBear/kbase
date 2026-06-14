<#
.SYNOPSIS
    Build KBase release artifacts: PyInstaller onedir + NSIS installer + zip.

.DESCRIPTION
    Reproduces the steps in .github/workflows/build-and-sign.yml locally so
    a release candidate can be smoke-tested before tagging. CI calls the
    same script with -SkipInstall once deps are cached.

.PARAMETER Version
    Version string (e.g. "0.4.0"). If omitted, reads kb/version.py.

.PARAMETER SkipInstall
    Skip `pip install`. CI passes this on the cache-hit path.

.PARAMETER SkipPyInstaller
    Reuse the existing dist/KBase/. Useful when iterating on installer.nsi.

.PARAMETER SkipInstaller
    Reuse the existing dist/KBase-Setup-*.exe. Useful when iterating on zip.

.EXAMPLE
    pwsh scripts/build_release.ps1 -Version 0.4.0
#>

[CmdletBinding()]
param(
    [string]$Version = "",
    [switch]$SkipInstall,
    [switch]$SkipPyInstaller,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot\..
$RepoRoot = (Get-Location).Path

# --- Resolve version ---
if (-not $Version) {
    $versionPy = Join-Path $RepoRoot "kb\version.py"
    if (Test-Path $versionPy) {
        $content = Get-Content $versionPy -Raw -Encoding UTF8
        if ($content -match 'VERSION\s*=\s*"([^"]+)"') {
            $Version = $Matches[1]
        } else {
            $Version = "0.3.0"
        }
    } else {
        $Version = "0.3.0"
    }
}
Write-Host "=== Building KBase v$Version ===" -ForegroundColor Cyan

# --- Stage 0: pip install (optional) ---
if (-not $SkipInstall) {
    Write-Host "[0/4] Installing build dependencies..." -ForegroundColor Cyan
    python -m pip install --upgrade pip | Out-Null
    pip install `
        pyinstaller `
        pymupdf `
        pywebview `
        pythonnet `
        clr_loader `
        pillow `
        pydantic `
        pydantic-settings `
        python-dotenv `
        pyyaml `
        openai
    if ($LASTEXITCODE) { throw "pip install failed (exit $LASTEXITCODE)" }
}

# --- Stage 1: PyInstaller (portable onedir) ---
if (-not $SkipPyInstaller) {
    Write-Host "[1/4] Cleaning build/ and dist/..." -ForegroundColor Cyan
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

    Write-Host "[2/4] Running PyInstaller..." -ForegroundColor Cyan
    $pyiArgs = @("-m", "PyInstaller", "--noconfirm", "--clean", "kbase.spec")
    $proc = Start-Process python -ArgumentList $pyiArgs -Wait -PassThru -NoNewWindow
    if ($proc.ExitCode -ne 0) { throw "PyInstaller failed (exit $($proc.ExitCode))" }
    if (-not (Test-Path "dist\KBase\KBase.exe")) {
        throw "dist\KBase\KBase.exe not found after PyInstaller"
    }
    $exe = Get-Item "dist\KBase\KBase.exe"
    Write-Host ("        KBase.exe ({0:N1} MB)" -f ($exe.Length / 1MB))
} else {
    Write-Host "[2/4] Skipping PyInstaller (reusing dist/KBase/)" -ForegroundColor DarkCyan
}

# --- Stage 2: Portable zip ---
Write-Host "[3/4] Creating portable zip..." -ForegroundColor Cyan
$zipName = "KBase-v$Version-portable.zip"
$zipPath = "dist\$zipName"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path "dist\KBase\*" -DestinationPath $zipPath -Force
$zipSize = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host "        $zipName ($zipSize MB)"

# --- Stage 3: NSIS installer ---
if (-not $SkipInstaller) {
    Write-Host "[4/4] Building NSIS installer..." -ForegroundColor Cyan
    $nsis = $null
    foreach ($cand in @("C:\Program Files (x86)\NSIS\makensis.exe",
                        "C:\Program Files\NSIS\makensis.exe",
                        "makensis.exe",
                        "makensis")) {
        if ($null -ne (Get-Command $cand -ErrorAction SilentlyContinue)) {
            $nsis = $cand; break
        }
        if (Test-Path $cand) { $nsis = $cand; break }
    }
    if (-not $nsis) {
        throw "makensis not on PATH. Install NSIS 3.x (https://nsis.sourceforge.io)."
    }
    $nsisArgs = @("/DVERSION=$Version", "/V2", "installer.nsi")
    $nsisProc = Start-Process $nsis -ArgumentList $nsisArgs -Wait -PassThru -NoNewWindow
    if ($nsisProc.ExitCode -ne 0) { throw "NSIS failed (exit $($nsisProc.ExitCode))" }
} else {
    Write-Host "[4/4] Skipping NSIS (reusing dist/KBase-Setup-*.exe)" -ForegroundColor DarkCyan
}

$setupName = "KBase-Setup-v$Version.exe"
if (-not (Test-Path "dist\$setupName")) {
    throw "dist\$setupName not found after NSIS"
}
$setupSize = [math]::Round((Get-Item "dist\$setupName").Length / 1MB, 1)
Write-Host "        $setupName ($setupSize MB)"

# --- Summary ---
Write-Host ""
Write-Host "Done. Artifacts:" -ForegroundColor Green
Write-Host "  Portable : dist\$zipName"
Write-Host "  Installer: dist\$setupName"
Write-Host ""
Write-Host "Next: create a GitHub Release and upload both files."
Write-Host "  gh release create v$Version dist\$zipName dist\$setupName --title 'v$Version' --notes '...'"
Write-Host "Or: push tag v$Version and let .github/workflows/build-and-sign.yml handle it."

Pop-Location
