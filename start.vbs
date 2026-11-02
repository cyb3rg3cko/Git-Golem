' Starts Git Project Manager on Windows without a console window.
' Double-click this file. It must sit in the same folder as git_project_manager.py.
Option Explicit

Dim shell, fso, folder, script, launchers, i
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

folder = fso.GetParentFolderName(WScript.ScriptFullName)
script = fso.BuildPath(folder, "git_project_manager.py")

If Not fso.FileExists(script) Then
    MsgBox "Could not find git_project_manager.py in:" & vbCrLf & folder, vbExclamation, "Git Project Manager"
    WScript.Quit 1
End If

shell.CurrentDirectory = folder

' pyw = the Python launcher (installed by python.org even when Python is not on PATH)
' pythonw = Python on PATH. Both start the app without a console window.
launchers = Array("pyw -3", "pythonw")

On Error Resume Next
For i = 0 To UBound(launchers)
    Err.Clear
    shell.Run launchers(i) & " """ & script & """", 1, False
    If Err.Number = 0 Then WScript.Quit 0
Next
On Error GoTo 0

MsgBox "Could not find Python." & vbCrLf & vbCrLf & _
       "Install Python 3 from https://www.python.org/downloads/ " & _
       "(tick 'Add python.exe to PATH' during setup), then try again.", _
       vbExclamation, "Git Project Manager"
WScript.Quit 1
