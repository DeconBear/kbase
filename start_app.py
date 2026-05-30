import sys
import subprocess
import os

def main():
    python_exe = sys.executable
    pythonw_exe = python_exe.replace("python.exe", "pythonw.exe")
    
    # Prefer pythonw if it exists to avoid console flash
    exe = pythonw_exe if os.path.exists(pythonw_exe) else python_exe
    
    startupinfo = None
    if sys.platform == 'win32' and exe == python_exe:
        # Hide console window if using python.exe
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    subprocess.Popen([exe, "kb/desktop.py"], startupinfo=startupinfo)
    print("KBase has been launched in the background!")
    print("You can now close this console window.")

if __name__ == "__main__":
    main()
