' nomad_stock telegram bot launcher (hidden, no console window).
' Placed in the Startup folder so it runs at every login without admin.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\nomad_stock"
sh.Run """C:\Python314\pythonw.exe"" run_bot.py", 0, False
