Option Explicit
Dim shell, fso, root, exe, python, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
exe = fso.BuildPath(root, "dist\CodexUsageWidget.exe")
shell.CurrentDirectory = root
If fso.FileExists(exe) Then
    shell.Run Chr(34) & exe & Chr(34), 0, False
Else
    python = "D:\python3.12\pythonw.exe"
    If fso.FileExists(python) Then
        command = Chr(34) & python & Chr(34) & " " & Chr(34) & fso.BuildPath(root, "main.py") & Chr(34)
        shell.Run command, 0, False
    Else
        MsgBox "Please build dist\CodexUsageWidget.exe or run main.py with Python 3.12.", 48, "Codex Usage Widget"
    End If
End If
