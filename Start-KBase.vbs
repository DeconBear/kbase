' Launch KBase desktop without a Python console window.
Option Explicit

Dim sh, fso, root, desktop, pythonw, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
desktop = root & "\kb\desktop.py"
If Not fso.FileExists(desktop) Then
  MsgBox "Cannot find kb\desktop.py under:" & vbCrLf & root, vbCritical, "KBase"
  WScript.Quit 1
End If

pythonw = FindPythonw()
If pythonw = "" Then
  MsgBox "pythonw.exe not found. Activate a Python env with pythonw, or install conda env py311.", vbCritical, "KBase"
  WScript.Quit 1
End If

sh.CurrentDirectory = root
cmd = """" & pythonw & """ """ & desktop & """"
' 0 = hide any host window; pythonw itself has no console.
sh.Run cmd, 0, False

Function FindPythonw()
  Dim p, execObj, whereOut
  FindPythonw = ""

  p = sh.ExpandEnvironmentStrings("%CONDA_PREFIX%\pythonw.exe")
  If InStr(p, "%") = 0 And fso.FileExists(p) Then FindPythonw = p : Exit Function

  p = sh.ExpandEnvironmentStrings("%USERPROFILE%\.conda\envs\py311\pythonw.exe")
  If fso.FileExists(p) Then FindPythonw = p : Exit Function

  p = "D:\anaconda3\envs\py311\pythonw.exe"
  If fso.FileExists(p) Then FindPythonw = p : Exit Function

  p = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe")
  If fso.FileExists(p) Then FindPythonw = p : Exit Function

  On Error Resume Next
  Set execObj = sh.Exec("where pythonw")
  Do While execObj.Status = 0
    WScript.Sleep 50
  Loop
  whereOut = Trim(execObj.StdOut.ReadLine)
  On Error GoTo 0
  If whereOut <> "" And fso.FileExists(whereOut) Then FindPythonw = whereOut
End Function
