"""GitHub-release update checker and applier for the installed (NSIS) build.

Portable builds do not use this module — they show a "请下载绿色版压缩包
手动覆盖" message instead.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from .version import VERSION
except ImportError:
    from version import VERSION  # noqa: E402

GITHUB_API = "https://api.github.com/repos/DeconBear/kbase/releases/latest"
_UPDATE_CHECK_INTERVAL = 3600  # cache check result for 1 hour

_last_check: dict | None = None
_last_check_time: float = 0.0


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_installed_build() -> bool:
    """True when running from an NSIS-installed copy."""
    flag = _exe_dir() / "installed.flag"
    if flag.exists():
        return True
    # Also detect via uninstaller presence (NSIS creates uninst.exe)
    if (_exe_dir() / "uninst.exe").exists():
        return True
    return False


def check_for_update(force: bool = False) -> dict:
    """Query GitHub for the latest release.

    Returns a dict suitable for the frontend:
        {current, latest, hasUpdate, releaseUrl, releaseNotes, assetUrl, installedBuild}
    Caches the result for _UPDATE_CHECK_INTERVAL seconds unless *force* is True.
    """
    global _last_check, _last_check_time

    now = time.time()
    if not force and _last_check is not None and (now - _last_check_time) < _UPDATE_CHECK_INTERVAL:
        return _last_check

    result: dict = {
        "current": VERSION,
        "latest": VERSION,
        "hasUpdate": False,
        "releaseUrl": "",
        "releaseNotes": "",
        "assetUrl": "",
        "installedBuild": is_installed_build(),
    }

    try:
        req = urllib.request.Request(
            GITHUB_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "KBase-Updater"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 403:
            try:
                body_preview = e.read()[:200].decode(errors="replace").lower()
            except Exception:
                body_preview = ""
            if "rate limit" in body_preview:
                result["error"] = "GitHub API rate limited — try again later"
            else:
                result["error"] = f"GitHub returned {e.code}"
        else:
            result["error"] = f"GitHub returned {e.code}"
        _last_check = result
        _last_check_time = now
        return result
    except Exception as e:
        result["error"] = f"无法连接到 GitHub: {e}"
        _last_check = result
        _last_check_time = now
        return result

    tag = data.get("tag_name", "")
    # Strip leading 'v' for comparison
    latest_version = tag.lstrip("v")
    if not latest_version:
        result["error"] = "GitHub release has no valid tag"
        _last_check = result
        _last_check_time = now
        return result

    result["latest"] = latest_version
    result["releaseUrl"] = data.get("html_url", "")
    result["releaseNotes"] = data.get("body", "")[:4000]

    # Find assets. We return BOTH the NSIS installer URL (for installed
    # builds) and the portable zip URL (for portable builds) so the
    # frontend / apply_update can pick the right one for the current
    # build.
    installer_url = ""
    portable_url = ""
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        url = asset.get("browser_download_url", "")
        size = asset.get("size", 0)
        if name.endswith(".exe") and "Setup" in name:
            installer_url = url
            result["installerSize"] = size
        elif name.endswith(".zip") and "portable" in name.lower():
            portable_url = url
            result["portableSize"] = size
    # assetUrl is whichever the current build wants.
    if is_installed_build():
        result["assetUrl"] = installer_url
    else:
        result["assetUrl"] = portable_url or installer_url
    result["installerUrl"] = installer_url
    result["portableUrl"] = portable_url

    result["hasUpdate"] = _version_greater(latest_version, VERSION) and bool(result["assetUrl"])
    _last_check = result
    _last_check_time = now
    return result


def apply_update(asset_url: str) -> bool:
    """Launch a self-contained PowerShell updater that survives parent exit.

    The PowerShell script does everything in order:
      1. Download the update package to a temp directory.
      2. Poll-wait for the KBase process to exit (up to 60 s).
      3. Apply the update (silent NSIS installer or portable zip extract).
      4. Restart KBase.exe.

    This function returns as soon as the PowerShell process is spawned;
    the calling server handler should tell the frontend to close the
    window, which triggers KBase.exe shutdown → the PS script proceeds.
    """
    if not asset_url:
        return False

    installed = is_installed_build()
    tmp_dir = Path(tempfile.gettempdir()) / "KBaseUpdate"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    install_dir = str(_exe_dir())
    exe_name = "KBase.exe" if getattr(sys, "frozen", False) else "python.exe"

    if installed:
        pkg_path = tmp_dir / "KBase-Setup.exe"
        apply_block = f"""Write-Host '[3/4] Running silent installer...'
$installArgs = @('/S', '/D=' + $targetDir)
$p = Start-Process -FilePath $pkg -ArgumentList $installArgs -Wait -PassThru -NoNewWindow
if ($p.ExitCode -ne 0) {{
    Write-Host "ERROR: Installer exited with code $($p.ExitCode)"
    exit 1
}}
Write-Host '[4/4] Installer finished successfully.'"""
    else:
        pkg_path = tmp_dir / "KBase-portable.zip"
        # For portable builds, extract to a staging dir then robocopy over
        # the install dir so we don't hit locked-file errors on _internal/.
        apply_block = f"""Write-Host '[3/4] Extracting portable update...'
$stageDir = Join-Path $env:TEMP 'KBaseUpdateStage'
Remove-Item $stageDir -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -Path $pkg -DestinationPath $stageDir -Force
Write-Host '[4/4] Copying files over $targetDir...'
# Use robocopy to mirror the staging dir over the install dir.
# Retry on locked files (up to 3 attempts with 2 s delay).
$maxRetry = 3
$retry = 0
$robocopyOk = $false
while ($retry -lt $maxRetry) {{
    $result = robocopy $stageDir $targetDir /MIR /R:2 /W:2 /NP /NDL /NFL
    if ($LASTEXITCODE -lt 8) {{
        $robocopyOk = $true
        break
    }}
    $retry++
    Write-Host "  Retry $retry/$maxRetry (robocopy exit $LASTEXITCODE)..."
    Start-Sleep 2
}}
Remove-Item $stageDir -Recurse -Force -ErrorAction SilentlyContinue
if (-not $robocopyOk) {{
    Write-Host "WARNING: Some files could not be overwritten — they will be updated on next launch."
}}"""

    updater_body = f"""$ErrorActionPreference = 'Stop'
$pkg = '{pkg_path}'
$targetDir = '{install_dir}'
$exeName = '{exe_name}'

Write-Host '=== KBase Updater ==='
Write-Host "  Package  : $pkg"
Write-Host "  Target   : $targetDir"
Write-Host "  Mode     : {'NSIS installer' if installed else 'Portable zip'}"

# ---- 1. Download ----
Write-Host '[1/4] Downloading update package...'
$ProgressPreference = 'SilentlyContinue'
try {{
    Invoke-WebRequest -Uri '{asset_url}' -OutFile $pkg -UseBasicParsing -TimeoutSec 600
}} catch {{
    Write-Host "ERROR: Download failed: $($_.Exception.Message)"
    exit 1
}}
$pkgSize = [math]::Round((Get-Item $pkg).Length / 1MB, 1)
Write-Host "  Downloaded $pkgSize MB"

# ---- 2. Wait for KBase to exit ----
Write-Host '[2/4] Waiting for KBase to exit...'
$timeout = 60
$elapsed = 0
while ($elapsed -lt $timeout) {{
    $proc = Get-Process -Name 'KBase' -ErrorAction SilentlyContinue
    if (-not $proc) {{
        Write-Host '  KBase has exited.'
        break
    }}
    Start-Sleep 1
    $elapsed++
}}
if ($elapsed -ge $timeout) {{
    Write-Host '  Timeout waiting for KBase to exit — forcing termination.'
    Get-Process -Name 'KBase' -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
}}

# ---- 3. Apply update ----
{apply_block}

# ---- 4. Restart ----
Write-Host 'Restarting KBase...'
$kbaseExe = Join-Path $targetDir 'KBase.exe'
if (Test-Path $kbaseExe) {{
    Start-Process -FilePath $kbaseExe
}} else {{
    Write-Host 'WARNING: KBase.exe not found at expected location.'
}}

# ---- Cleanup ----
Start-Sleep 2
Remove-Item $pkg -Force -ErrorAction SilentlyContinue
Remove-Item $PSCommandPath -Force -ErrorAction SilentlyContinue
Write-Host 'Updater finished.'
"""

    updater_ps1 = tmp_dir / "update.ps1"
    updater_ps1.write_text(updater_body, encoding="utf-8")

    try:
        subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
             "-File", str(updater_ps1)],
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            ) if sys.platform == "win32" else 0,
            close_fds=True,
        )
    except Exception:
        return False

    return True


def _version_greater(a: str, b: str) -> bool:
    """Compare two semver-like version strings (e.g. '0.4.0' > '0.3.0')."""
    try:
        parts_a = [int(x) for x in a.split(".")]
        parts_b = [int(x) for x in b.split(".")]
        # Pad to same length
        while len(parts_a) < len(parts_b):
            parts_a.append(0)
        while len(parts_b) < len(parts_a):
            parts_b.append(0)
        return parts_a > parts_b
    except (ValueError, AttributeError):
        # If we can't parse versions, assume no update
        # (safer than assuming any difference is an update).
        return False
