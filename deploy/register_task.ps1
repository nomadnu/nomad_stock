# nomad_stock 일봉 자동매매를 Windows 작업 스케줄러에 등록한다.
# 매 평일 09:05에 run_scheduler.py --once --live 를 1회 실행.
#
# 실행법: PowerShell에서
#     cd C:\nomad_stock
#     powershell -ExecutionPolicy Bypass -File deploy\register_task.ps1
#
# 삭제법:
#     Unregister-ScheduledTask -TaskName "nomad_stock_daily" -Confirm:$false

$python   = "C:\Python314\python.exe"
$workdir  = "C:\nomad_stock"
$taskName = "nomad_stock_daily"

$action  = New-ScheduledTaskAction -Execute $python -Argument "run_scheduler.py --once --live" -WorkingDirectory $workdir
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 9:05AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "nomad_stock 일봉 자동매매 (모의투자)" -Force

Write-Output "=== 등록 완료 ==="
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Output "다음 실행 예정: $($info.NextRunTime)"
