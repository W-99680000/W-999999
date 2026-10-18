' ============================================================================
'  BabelPlan.vbs - silent launcher for the Babel Plan desktop shell
'
'  Why a .vbs: a consumer double-clicks one icon and must NOT see a black
'  console window. wscript.exe is a GUI-subsystem host, and pythonw.exe is a
'  GUI-subsystem interpreter, so no console is ever allocated for the chain.
'
'  What it does:
'    1. finds pythonw.exe (BABEL_PY override -> standard Python 3.12/3.11/3.10
'       -> bundled .dsh runtime);
'    2. sets the working directory to the PROJECT ROOT (app.py resolves
'       babel_admin/, model_registry.json etc. relative to itself, but a
'       correct cwd keeps logs/tools consistent);
'    3. starts  pythonw.exe "<project>\app.py" [extra args]  with window
'       style 0 (hidden) and without waiting;
'    4. appends one line to %LOCALAPPDATA%\BabelPlan\launch.log so a failed
'       double-click can be diagnosed.
'
'  Usage:
'      wscript.exe "<project>\desktop\BabelPlan.vbs"
'      wscript.exe "<project>\desktop\BabelPlan.vbs" --tray
'      wscript.exe "<project>\desktop\BabelPlan.vbs" --keep-engine
'
'  The shortcut created by desktop/create_shortcut.ps1 points at wscript.exe
'  with this file as the script argument and desktop\babel.ico as the icon.
'
'  NOTE: keep this file ASCII-only. Windows Script Host reads .vbs as ANSI
'  (or UTF-16 with BOM); a UTF-8 file with Chinese text would be mangled
'  and may even fail to parse.
' ============================================================================
Option Explicit

Dim fso, shell, here, root, appPy, pyw, cmd, i, cands
Dim envPy, envHome, envLocal, baseName, folder

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

' --- locate the project root: <project>\desktop\BabelPlan.vbs -> <project> ---
here = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(here)
appPy = fso.BuildPath(root, "app.py")

If Not fso.FileExists(appPy) Then
    MsgBox "app.py was not found:" & vbCrLf & appPy & vbCrLf & vbCrLf & _
           "Keep BabelPlan.vbs inside <project>\desktop\ and do not move it.", _
           16, "BabelPlan"
    WScript.Quit 2
End If

' --- 1. pythonw.exe: BABEL_PY override first -------------------------------
pyw = ""
envPy = shell.ExpandEnvironmentStrings("%BABEL_PY%")
If envPy <> "%BABEL_PY%" Then
    If fso.FileExists(envPy) Then
        folder = fso.GetParentFolderName(envPy)
        baseName = LCase(fso.GetFileName(envPy))
        If baseName = "pythonw.exe" Then
            pyw = envPy
        ElseIf baseName = "python.exe" Then
            ' python.exe -> pythonw.exe in the same folder (no console)
            If fso.FileExists(fso.BuildPath(folder, "pythonw.exe")) Then
                pyw = fso.BuildPath(folder, "pythonw.exe")
            End If
        End If
    End If
End If

' --- 2. standard interpreter locations -------------------------------------
If pyw = "" Then
    envHome = shell.ExpandEnvironmentStrings("%USERPROFILE%")
    envLocal = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%")
    cands = Array( _
        envLocal & "\Programs\Python\Python312\pythonw.exe", _
        envLocal & "\Programs\Python\Python311\pythonw.exe", _
        envLocal & "\Programs\Python\Python310\pythonw.exe", _
        envHome & "\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe")
    For i = 0 To UBound(cands)
        If fso.FileExists(cands(i)) Then
            pyw = cands(i)
            Exit For
        End If
    Next
End If

If pyw = "" Then
    MsgBox "pythonw.exe was not found." & vbCrLf & vbCrLf & _
           "Install Python 3.12 (python.org), or set BABEL_PY to your " & _
           "python.exe / pythonw.exe, then run this launcher again.", _
           16, "BabelPlan"
    WScript.Quit 1
End If

' --- 3. build the command line (quoted, extra args passed through) --------
cmd = """" & pyw & """ """ & appPy & """"
For i = 0 To WScript.Arguments.Count - 1
    cmd = cmd & " """ & WScript.Arguments(i) & """"
Next

' --- 4. run detached, hidden, cwd = project root --------------------------
shell.CurrentDirectory = root
AppendLog "launch pyw=" & pyw & " cwd=" & root & " args=" & CStr(WScript.Arguments.Count)

' Window style 1 (normal) - NOT 0. Windows copies this value into the
' child's STARTUPINFO.wShowWindow, and the child's FIRST ShowWindow call
' is forced to that value. With 0 (SW_HIDE) the pywebview window would
' stay invisible even though the app is running. pythonw.exe has no
' console at all, so there is no black window to hide in the first place.
On Error Resume Next
shell.Run cmd, 1, False
If Err.Number <> 0 Then
    AppendLog "RUN FAILED (" & Err.Number & "): " & Err.Description
    MsgBox "Failed to start pythonw.exe:" & vbCrLf & Err.Description & vbCrLf & vbCrLf & _
           "Command: " & cmd, 16, "BabelPlan"
    WScript.Quit 3
End If
On Error GoTo 0

WScript.Quit 0


' ============================================================================
'  helpers
' ============================================================================
Sub AppendLog(message)
    ' Best-effort launch log; never let a logging failure break the launch.
    On Error Resume Next
    Dim dirPath, logFile, fh
    dirPath = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\BabelPlan"
    If Not fso.FolderExists(dirPath) Then
        fso.CreateFolder dirPath
    End If
    logFile = dirPath & "\launch.log"
    Set fh = fso.OpenTextFile(logFile, 8, True)   ' 8 = ForAppending
    fh.WriteLine Now & "  " & message
    fh.Close
    On Error GoTo 0
End Sub
