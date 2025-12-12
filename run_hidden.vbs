Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "pythonw """ & Replace(WScript.ScriptFullName, "run_hidden.vbs", "main.py") & """", 0, False
