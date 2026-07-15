"""GitHub Release update checks and verified Windows update hand-off."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from .version import VERSION
except ImportError:
    from version import VERSION  # noqa: E402

GITHUB_API = "https://api.github.com/repos/DeconBear/kbase/releases/latest"
_UPDATE_CHECK_INTERVAL = 3600  # cache check result for 1 hour
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_VERSION_TOKEN_RE = r"[0-9A-Za-z][0-9A-Za-z._+-]*"

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
    return (_exe_dir() / "uninst.exe").exists()


def can_auto_update() -> bool:
    """Return whether this process can safely replace its own Windows build."""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _build_type() -> str:
    if not getattr(sys, "frozen", False):
        return "source"
    if sys.platform != "win32":
        return "unsupported"
    return "installed" if is_installed_build() else "portable"


def _asset_digest(asset: dict) -> str:
    digest = str(asset.get("digest") or "")
    if digest.lower().startswith("sha256:"):
        digest = digest.split(":", 1)[1]
    return digest.lower() if _SHA256_RE.fullmatch(digest) else ""


def check_for_update(force: bool = False) -> dict:
    """Query GitHub for the latest release and select an asset for this build."""
    global _last_check, _last_check_time

    now = time.time()
    if not force and _last_check is not None and (now - _last_check_time) < _UPDATE_CHECK_INTERVAL:
        return _last_check

    build_type = _build_type()
    result: dict = {
        "current": VERSION,
        "latest": VERSION,
        "hasUpdate": False,
        "releaseUrl": "",
        "releaseNotes": "",
        "assetUrl": "",
        "assetSize": 0,
        "assetSha256": "",
        "manualUrl": "",
        "installedBuild": is_installed_build(),
        "buildType": build_type,
        "canAutoUpdate": can_auto_update(),
    }

    try:
        req = urllib.request.Request(
            GITHUB_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "KBase-Updater"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            try:
                body_preview = exc.read()[:200].decode(errors="replace").lower()
            except Exception:
                body_preview = ""
            if "rate limit" in body_preview:
                result["error"] = "GitHub API 请求频率受限，请稍后重试"
            else:
                result["error"] = f"GitHub 返回了 {exc.code}"
        else:
            result["error"] = f"GitHub 返回了 {exc.code}"
        _last_check = result
        _last_check_time = now
        return result
    except Exception as exc:
        result["error"] = f"无法连接到 GitHub: {exc}"
        _last_check = result
        _last_check_time = now
        return result

    tag = str(data.get("tag_name") or "")
    latest_version = tag[1:] if tag.lower().startswith("v") else tag
    if not latest_version:
        result["error"] = "GitHub Release 缺少有效版本号"
        _last_check = result
        _last_check_time = now
        return result

    result["latest"] = latest_version
    result["releaseUrl"] = str(data.get("html_url") or "")
    result["releaseNotes"] = str(data.get("body") or "")[:4000]
    result["manualUrl"] = result["releaseUrl"]

    installer: dict = {}
    portable: dict = {}
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.lower().endswith(".exe") and "setup" in name.lower():
            installer = asset
        elif name.lower().endswith(".zip") and "portable" in name.lower():
            portable = asset

    def publish_asset(prefix: str, asset: dict) -> None:
        url = str(asset.get("browser_download_url") or "")
        size = int(asset.get("size") or 0)
        digest = _asset_digest(asset)
        result[f"{prefix}Url"] = url
        result[f"{prefix}Size"] = size
        result[f"{prefix}Sha256"] = digest

    publish_asset("installer", installer)
    publish_asset("portable", portable)

    selected = installer if build_type == "installed" else portable if build_type == "portable" else {}
    if selected and can_auto_update():
        result["assetUrl"] = str(selected.get("browser_download_url") or "")
        result["assetSize"] = int(selected.get("size") or 0)
        result["assetSha256"] = _asset_digest(selected)
        result["manualUrl"] = result["assetUrl"] or result["releaseUrl"]

    result["hasUpdate"] = _version_greater(latest_version, VERSION)
    _last_check = result
    _last_check_time = now
    return result


def _validate_asset_url(asset_url: str, installed: bool) -> bool:
    """Accept only KBase assets hosted on the repository's GitHub Releases path."""
    try:
        parsed = urllib.parse.urlsplit(asset_url)
    except (TypeError, ValueError):
        return False
    if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.query or parsed.fragment:
        return False
    prefix = r"^/DeconBear/kbase/releases/download/v?" + _VERSION_TOKEN_RE + "/"
    if installed:
        filename = r"KBase-Setup-v?" + _VERSION_TOKEN_RE + r"\.exe$"
    else:
        filename = r"KBase-v?" + _VERSION_TOKEN_RE + r"-portable\.zip$"
    return re.fullmatch(prefix + filename, parsed.path) is not None


def _download_asset(
    asset_url: str,
    target: Path,
    *,
    expected_sha256: str = "",
    expected_size: int = 0,
) -> None:
    """Download to a temporary file, verify it, then atomically publish it."""
    temp_target = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    digest = hashlib.sha256()
    size = 0
    try:
        req = urllib.request.Request(asset_url, headers={"User-Agent": "KBase-Updater"})
        with urllib.request.urlopen(req, timeout=600) as resp, temp_target.open("wb") as output:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if expected_size > 0 and size != expected_size:
            raise ValueError(f"安装包大小校验失败（期望 {expected_size}，实际 {size}）")
        if expected_sha256:
            if not _SHA256_RE.fullmatch(expected_sha256) or digest.hexdigest() != expected_sha256.lower():
                raise ValueError("安装包 SHA-256 校验失败")
        if size <= 0:
            raise ValueError("下载到的安装包为空")
        os.replace(temp_target, target)
    finally:
        temp_target.unlink(missing_ok=True)


def _ps_literal(value: str | Path) -> str:
    """Return a PowerShell single-quoted literal with embedded quotes escaped."""
    return "'" + str(value).replace("'", "''") + "'"


def apply_update(
    asset_url: str,
    *,
    expected_sha256: str = "",
    expected_size: int = 0,
) -> dict:
    """Download, verify, then launch a detached updater for the current build."""
    installed = is_installed_build()
    if not can_auto_update():
        return {"ok": False, "message": "一键更新仅支持 Windows 打包版 KBase.exe"}
    if not _validate_asset_url(asset_url, installed):
        return {"ok": False, "message": "更新地址不是受信任的 KBase GitHub Release 资产"}

    update_root = Path(tempfile.gettempdir()) / "KBaseUpdate"
    update_root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix=f"run-{os.getpid()}-", dir=update_root))
    pkg_path = run_dir / ("KBase-Setup.exe" if installed else "KBase-portable.zip")
    try:
        _download_asset(
            asset_url,
            pkg_path,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        )
    except Exception as exc:
        shutil.rmtree(run_dir, ignore_errors=True)
        return {"ok": False, "message": f"更新包下载或校验失败：{exc}"}

    install_dir = _exe_dir()
    parent_pid = os.getpid()

    if installed:
        apply_block = """Write-Log '[2/3] 正在运行安装程序...'
$installArgs = "/S /D=$targetDir"
try {
    $p = Start-Process -FilePath $pkg -ArgumentList $installArgs -Wait -PassThru -NoNewWindow
} catch {
    Write-Log "ERROR: 无法启动安装程序：$($_.Exception.Message)"
    exit 1
}
if ($p.ExitCode -ne 0) {
    Write-Log "ERROR: 安装程序退出码 $($p.ExitCode)"
    exit 1
}
Write-Log '[2/3] 安装程序已完成。'"""
    else:
        apply_block = """Write-Log '[2/3] 正在解压便携版更新...'
$stageDir = Join-Path $updateDir 'stage'
$backupDir = Join-Path $targetDir ('.kbase-update-backup-' + $parentPid)
Expand-Archive -LiteralPath $pkg -DestinationPath $stageDir -Force
$newExe = Join-Path $stageDir 'KBase.exe'
$newInternal = Join-Path $stageDir '_internal'
if (-not (Test-Path -LiteralPath $newExe -PathType Leaf) -or -not (Test-Path -LiteralPath $newInternal -PathType Container)) {
    Write-Log 'ERROR: 便携版压缩包结构无效。'
    exit 1
}

Remove-Item -LiteralPath $backupDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
$oldExe = Join-Path $targetDir 'KBase.exe'
$oldInternal = Join-Path $targetDir '_internal'
try {
    if (Test-Path -LiteralPath $oldExe) { Move-Item -LiteralPath $oldExe -Destination $backupDir -Force }
    if (Test-Path -LiteralPath $oldInternal) { Move-Item -LiteralPath $oldInternal -Destination $backupDir -Force }
    Copy-Item -LiteralPath $newExe -Destination $oldExe -Force
    $null = robocopy $newInternal $oldInternal /E /R:2 /W:2 /NP /NDL /NFL
    if ($LASTEXITCODE -ge 8) { throw "robocopy 退出码 $LASTEXITCODE" }
    Get-ChildItem -LiteralPath $stageDir -File | Where-Object Name -ne 'KBase.exe' |
        Copy-Item -Destination $targetDir -Force
} catch {
    Write-Log "ERROR: 替换应用文件失败，正在回滚：$($_.Exception.Message)"
    Remove-Item -LiteralPath $oldExe -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $oldInternal -Recurse -Force -ErrorAction SilentlyContinue
    $backupExe = Join-Path $backupDir 'KBase.exe'
    $backupInternal = Join-Path $backupDir '_internal'
    if (Test-Path -LiteralPath $backupExe) { Move-Item -LiteralPath $backupExe -Destination $oldExe -Force }
    if (Test-Path -LiteralPath $backupInternal) { Move-Item -LiteralPath $backupInternal -Destination $oldInternal -Force }
    exit 1
}
Remove-Item -LiteralPath $backupDir -Recurse -Force -ErrorAction SilentlyContinue
Write-Log '[2/3] 便携版文件替换完成。'"""

    updater_body = f"""$ErrorActionPreference = 'Stop'
$pkg = {_ps_literal(pkg_path)}
$updateDir = {_ps_literal(run_dir)}
$targetDir = {_ps_literal(install_dir)}
$parentPid = {parent_pid}
$logFile = Join-Path $env:TEMP 'KBaseUpdate/update.log'

$logDir = Split-Path $logFile -Parent
if (-not (Test-Path -LiteralPath $logDir)) {{ New-Item -ItemType Directory -Path $logDir -Force | Out-Null }}
function Write-Log($msg) {{
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
}}

Write-Log '=== KBase Updater ==='
Write-Log "  Target: $targetDir"
Write-Log "  Mode: {'NSIS installer' if installed else 'Portable zip'}"
Write-Log '[1/3] 更新包已下载并通过校验，等待当前 KBase 实例退出...'
$timeout = 60
$elapsed = 0
while ($elapsed -lt $timeout) {{
    $parent = Get-Process -Id $parentPid -ErrorAction SilentlyContinue
    if (-not $parent) {{ break }}
    Start-Sleep 1
    $elapsed++
}}
if ($elapsed -ge $timeout) {{
    Write-Log "当前实例退出超时，仅终止 PID $parentPid。"
    Stop-Process -Id $parentPid -Force -ErrorAction SilentlyContinue
    Start-Sleep 2
}}

{apply_block}

Write-Log '[3/3] 正在重启 KBase...'
$kbaseExe = Join-Path $targetDir 'KBase.exe'
if (-not (Test-Path -LiteralPath $kbaseExe -PathType Leaf)) {{
    Write-Log "ERROR: 未找到 $kbaseExe"
    exit 1
}}
Start-Process -FilePath $kbaseExe -WindowStyle Normal
Write-Log '更新完成。'
Start-Sleep 2
Remove-Item -LiteralPath $updateDir -Recurse -Force -ErrorAction SilentlyContinue
"""

    updater_ps1 = run_dir / "update.ps1"
    temp_script = run_dir / f"update.ps1.tmp-{parent_pid}"
    # Windows PowerShell 5.1 treats BOM-less UTF-8 scripts as the active
    # ANSI code page; multibyte log messages can then corrupt quote parsing.
    temp_script.write_text(updater_body, encoding="utf-8-sig")
    os.replace(temp_script, updater_ps1)

    try:
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(updater_ps1),
            ],
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            ),
            close_fds=True,
        )
    except Exception as exc:
        shutil.rmtree(run_dir, ignore_errors=True)
        return {"ok": False, "message": f"无法启动更新程序：{exc}"}

    return {"ok": True, "message": "更新包已下载并校验，正在交接更新程序"}


def _version_greater(a: str, b: str) -> bool:
    """Compare two semver-like version strings (e.g. '0.4.0' > '0.3.0')."""
    try:
        parts_a = [int(x) for x in a.split(".")]
        parts_b = [int(x) for x in b.split(".")]
        while len(parts_a) < len(parts_b):
            parts_a.append(0)
        while len(parts_b) < len(parts_a):
            parts_b.append(0)
        return parts_a > parts_b
    except (ValueError, AttributeError):
        return False
