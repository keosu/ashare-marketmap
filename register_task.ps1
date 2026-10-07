# 在 Windows 任务计划程序里注册每日更新任务（云端 GitHub Actions 不可用时的替代方案）
# 用法（普通 PowerShell 即可，无需管理员）：
#   powershell -ExecutionPolicy Bypass -File register_task.ps1
#
# 任务：每个工作日 17:30 调用 daily.sh
# daily.sh 内部会自行判断是否交易日，非交易日直接跳过。

$ErrorActionPreference = 'Stop'
$proj    = 'C:\Users\Administrator\WorkBuddy\2026-10-07-22-39-45'
$taskName = 'AShare Daily Snapshot'
$bash    = 'C:\Program Files\Git\bin\bash.exe'

if (-not (Test-Path $bash)) {
    Write-Host "找不到 Git Bash：$bash" -ForegroundColor Red
    Write-Host "请把脚本里的 $bash 改成你本机的 bash 路径。" -ForegroundColor Yellow
    exit 1
}

$action = New-ScheduledTaskAction `
    -Execute  $bash `
    -Argument "-lc `"cd '$proj' && PUSH=1 ./daily.sh >> '$proj/.logs/task.log' 2>&1`""

# 周一到周五 17:30
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 17:30

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 45) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName    $taskName `
    -Action      $action `
    -Trigger     $trigger `
    -Settings    $settings `
    -Description '每个工作日收盘后抓取全市场 A 股市值与涨跌，更新 treemap 页面并推送' `
    -Force | Out-Null

Write-Host "已注册任务：$taskName" -ForegroundColor Green
Write-Host "  时间：周一至周五 17:30"
Write-Host "  日志：$proj\.logs\task.log"
Write-Host ""
Write-Host "立即试跑一次："
Write-Host "  Start-ScheduledTask -TaskName '$taskName'"
Write-Host "查看状态："
Write-Host "  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
