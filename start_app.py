"""Launch KBase desktop without leaving a Python console window open."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    desktop = root / "kb" / "desktop.py"
    if not desktop.is_file():
        print(f"Cannot find {desktop}", file=sys.stderr)
        return 1

    python_exe = Path(sys.executable)
    pythonw = python_exe.with_name("pythonw.exe")
    exe = pythonw if pythonw.is_file() else python_exe

    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        if exe.name.lower() == "python.exe":
            creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    subprocess.Popen(
        [str(exe), str(desktop)],
        cwd=str(root),
        close_fds=True,
        creationflags=creationflags,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
