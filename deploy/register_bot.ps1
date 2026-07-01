# Register the nomad_stock telegram query bot to auto-start at logon.
# Runs silently (pythonw, no console). Logs go to logs\bot.log
#
# Run:  powershell -ExecutionPolicy Bypass -File C:\nomad_stock\deploy\register_bot.ps1
# Remove: Unregister-ScheduledTask -TaskName "nomad_stock_bot" -Confirm:$false

$pythonw  = "C:\Python314\pythonw.exe"
$workdir  = "C:\nomad_stock"
$taskName = "nomad_stock_bot"

$action   = New-ScheduledTaskAction -Execute $pythonw -Argument "run_bot.py" -WorkingDirectory $workdir
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "nomad_stock telegram query bot (auto-start at logon)" -Force

Write-Output "=== Registered OK ==="
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State | Format-Table -Auto
Write-Output "Auto-starts on next login. To start immediately: Start-ScheduledTask -TaskName nomad_stock_bot"
