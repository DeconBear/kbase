"""Marker engine — local Surya-model-based PDF conversion."""
import json
import os
import subprocess
import shutil
import sys
from pathlib import Path

ARTICLES_DIR = Path(__file__).parent.parent / "articles"
REPO_ROOT = Path(__file__).parent.parent.parent
RUNTIME_CONFIG = ARTICLES_DIR.parent / "low_memory_config.json"
MARKER_DEPS_INSTALLING = Path(__file__).parent.parent / ".marker_deps_installing"


def _load_runtime_config():
    if not RUNTIME_CONFIG.exists():
        return {}
    try:
        return json.loads(RUNTIME_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def check_marker_available():
    """Check if the Marker engine (marker_single + torch + surya) is usable."""
    marker_exe = shutil.which("marker_single") or os.path.expandvars(
        r"%APPDATA%\Python\Python313\Scripts\marker_single.exe"
    )
    if marker_exe and Path(marker_exe).exists():
        return True
    try:
        result = subprocess.run(
            [sys.executable, "-c", "from marker.scripts.convert_single import convert_single_cli"],
            capture_output=True, text=True, timeout=15,
            cwd=str(REPO_ROOT),
        )
        return result.returncode == 0
    except Exception:
        return False


def install_marker_deps(log_callback=None):
    """Install PyTorch CPU and download Surya models for local Marker engine."""
    def log(msg):
        if log_callback:
            log_callback(msg)

    if MARKER_DEPS_INSTALLING.exists():
        log("⚠️ 已有安装任务在进行中...")
        return False

    MARKER_DEPS_INSTALLING.write_text("installing")
    try:
        log("📦 正在安装 PyTorch (CPU 版本)...")
        proc = subprocess.Popen(
            [sys.executable, "-m", "pip", "install", "torch", "--index-url",
             "https://download.pytorch.org/whl/cpu", "--quiet", "--disable-pip-version-check"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        for line in proc.stdout:
            log(f"  [pip] {line.rstrip()}")
        proc.wait(timeout=600)
        if proc.returncode != 0:
            log("❌ PyTorch 安装失败")
            return False

        log("📦 正在安装 marker 依赖 (transformers, surya-ocr, pillow, ...)")
        marker_reqs = ["transformers", "surya-ocr", "pillow", "rapidfuzz",
                       "pdftext", "markdownify", "ftfy", "tqdm", "filetype"]
        proc = subprocess.Popen(
            [sys.executable, "-m", "pip", "install"] + marker_reqs +
            ["--quiet", "--disable-pip-version-check"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        for line in proc.stdout:
            log(f"  [pip] {line.rstrip()}")
        proc.wait(timeout=600)
        if proc.returncode != 0:
            log("❌ 依赖安装失败")
            return False

        log("✅ 依赖安装完成！Marker 引擎现在可以使用了。")
        return True
    except Exception as e:
        log(f"❌ 安装出错: {e}")
        return False
    finally:
        try:
            MARKER_DEPS_INSTALLING.unlink()
        except Exception:
            pass


class MarkerEngine:
    name = "marker"

    def run(self, pdf_path: str, article_id: str, log_callback=None) -> bool:
        """Run marker_single on a PDF. Returns True on success."""
        def log(msg):
            if log_callback:
                log_callback(msg)

        if not check_marker_available():
            log("❌ Marker 引擎未安装。本引擎需要 PyTorch 和相关模型。")
            log("📥 请在设置中选择「下载 Marker 引擎依赖」以启用本地解析功能。")
            log("💡 也可以使用 DocParser 或 DocMind 云端引擎（无需安装依赖）。")
            return False

        marker_exe = shutil.which("marker_single") or os.path.expandvars(
            r"%APPDATA%\Python\Python313\Scripts\marker_single.exe"
        )
        env = os.environ.copy()
        env["MODEL_CACHE_DIR"] = str(REPO_ROOT / "models")
        env["PYTHONPATH"] = (
            str(REPO_ROOT)
            if not env.get("PYTHONPATH")
            else str(REPO_ROOT) + os.pathsep + env["PYTHONPATH"]
        )
        runtime_config = _load_runtime_config()
        device = runtime_config.get("device")
        if device in {"cpu", "cuda"}:
            env["TORCH_DEVICE"] = device

        article_dir = ARTICLES_DIR / article_id
        work_root = article_dir / ".marker_work"
        if work_root.exists():
            try:
                shutil.rmtree(work_root)
            except OSError:
                shutil.rmtree(work_root, ignore_errors=True)
        work_root.mkdir(parents=True, exist_ok=True)

        if marker_exe and Path(marker_exe).exists():
            cmd = [marker_exe, str(pdf_path), "--output_dir", str(work_root)]
        else:
            cmd = [
                sys.executable,
                "-m",
                "marker.scripts.convert_single",
                str(pdf_path),
                "--output_dir",
                str(work_root),
            ]

        log("Starting Marker engine...")
        log(f"Command: {' '.join(cmd)}")
        if device:
            log(f"Device: {device}")

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=str(REPO_ROOT),
            bufsize=1,
        )

        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log(line)

        proc.wait(timeout=600)
        log(f"Process exited with code {proc.returncode}")

        if proc.returncode != 0:
            return False

        # Move results from Marker's output folder to the article dir.
        pdf_stem = Path(pdf_path).stem
        src_dir = work_root / pdf_stem

        if src_dir.exists():
            dest = article_dir
            # Track renames so we can update image paths in the markdown
            _renames = {}  # old_name -> new_name
            for item in src_dir.iterdir():
                target_name = item.name
                if target_name == f"{pdf_stem}.md":
                    target_name = f"{article_id}.md"
                elif target_name == f"{pdf_stem}_meta.json":
                    target_name = f"{article_id}_meta.json"
                elif target_name.startswith(f"{pdf_stem}_"):
                    target_name = f"{article_id}_{target_name[len(pdf_stem) + 1:]}"
                    _renames[item.name] = target_name

                target = dest / target_name
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        os.remove(target)
                if item.is_dir():
                    shutil.copytree(item, target)
                else:
                    shutil.copy2(item, target)

            # Fix image references in the markdown to use the renamed paths
            md_file = dest / f"{article_id}.md"
            if md_file.exists() and _renames:
                try:
                    content = md_file.read_text(encoding="utf-8")
                    for _old, _new in sorted(_renames.items(), key=lambda x: -len(x[0])):
                        content = content.replace(_old, _new)
                    md_file.write_text(content, encoding="utf-8")
                except Exception:
                    pass

            shutil.rmtree(work_root)
            log(f"Merged result from {src_dir.name} into {dest}")
            return (dest / f"{article_id}.md").exists()

        log(f"WARNING: No conversion result folder found at {src_dir}")
        shutil.rmtree(work_root, ignore_errors=True)
        return False
