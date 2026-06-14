"""GitHub-release update checker and applier for the installed (NSIS) build.

Portable builds do not use this module — they show a "请下载绿色版压缩包
手动覆盖" message instead.
"""
from __future__ import annotations

import json
import os
import shutil
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
        if e.code == 403 and "rate limit" in str(e.read()[:200]).lower():
            result["error"] = "GitHub API rate limited — try again later"
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


def apply_update(asset_url: str, log_callback=None) -> bool:
    """Download the update package and replace the running install.

    Two flows are supported:
      - Installed (NSIS) build: download the new installer and run
        it silently with /S after the current process exits.
      - Portable / source build: download the new portable zip and
        extract over the current install dir after exit.

    Returns True if the updater was launched successfully.
    """

    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    if not asset_url:
        log("no update package URL available")
        return False

    installed = is_installed_build()
    tmp_dir = Path(tempfile.gettempdir()) / "KBaseUpdate"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    install_dir = str(_exe_dir())

    if installed:
        pkg_path = tmp_dir / "KBase-Setup.exe"
    else:
        pkg_path = tmp_dir / "KBase-portable.zip"

    log(f"downloading update package... ({asset_url})")
    try:
        req = urllib.request.Request(asset_url, headers={"User-Agent": "KBase-Updater"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            with open(pkg_path, "wb") as f:
                shutil.copyfileobj(resp, f, length=8192 * 1024)
    except Exception as e:
        log(f"download failed: {e}")
        return False

    pkg_size_mb = pkg_path.stat().st_size / (1024 * 1024)
    log(f"download done ({pkg_size_mb:.1f} MB) -> {pkg_path}")

    updater_ps1 = tmp_dir / "update.ps1"
    if installed:
        updater_body = """$ErrorActionPreference = 'Stop'
$installer = '""" + str(pkg_path) + """'
$targetDir = '""" + install_dir + """'

Write-Host "Waiting for KBase to exit..."
Start-Sleep 2
$proc = Get-Process -Name "KBase" -ErrorAction SilentlyContinue
if ($proc) { $proc | Wait-Process -Timeout 30 }

Write-Host "Running installer silently..."
$args = @('/S', '/D=' + $targetDir)
$p = Start-Process -FilePath $installer -ArgumentList $args -Wait -PassThru -NoNewWindow
if ($p.ExitCode -eq 0) {
    Write-Host "Update successful, restarting KBase..."
    Start-Process (Join-Path $targetDir 'KBase.exe')
}

Start-Sleep 2
Remove-Item $installer -Force -ErrorAction SilentlyContinue
Remove-Item $PSCommandPath -Force -ErrorAction SilentlyContinue
"""
    else:
        updater_body = """$ErrorActionPreference = 'Stop'
$pkg = '""" + str(pkg_path) + """'
$targetDir = '""" + install_dir + """'

Write-Host "Waiting for KBase to exit..."
Start-Sleep 2
$proc = Get-Process -Name "KBase" -ErrorAction SilentlyContinue
if ($proc) { $proc | Wait-Process -Timeout 30 }

Write-Host "Extracting portable update over $targetDir..."
Expand-Archive -Path $pkg -DestinationPath $targetDir -Force

Write-Host "Update successful, restarting KBase..."
Start-Process (Join-Path $targetDir 'KBase.exe')

Start-Sleep 2
Remove-Item $pkg -Force -ErrorAction SilentlyContinue
Remove-Item $PSCommandPath -Force -ErrorAction SilentlyContinue
"""
    updater_ps1.write_text(updater_body, encoding="utf-8")

    try:
        subprocess.Popen(
            ["powershell.exe", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
             "-File", str(updater_ps1)],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0)
             if sys.platform == "win32" else 0,
            close_fds=True,
        )
    except Exception as e:
        log(f"failed to launch updater: {e}")
        return False

    log("updater launched, application will exit shortly...")
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
        return a != b  # fallback: any difference counts
