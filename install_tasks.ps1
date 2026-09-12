# 마켓 브리핑 예약작업 등록 (창 안 뜸: wscript + 숨김 vbs)
#   MarketBriefing_3h    : 3시간마다 정기 브리핑
#   MarketBriefing_Watch : 10분마다 급등/급락 감시
# 실행:  powershell -ExecutionPolicy Bypass -File C:\dev\market-briefing\install_tasks.ps1

$ErrorActionPreference = "Stop"
$base = "C:\dev\market-briefing"

function New-HiddenTask {
    param([string]$Name, [string]$Vbs, [Microsoft.Management.Infrastructure.CimInstance[]]$Triggers, [string]$Desc)

    $action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$base\$Vbs`"" -WorkingDirectory $base
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                    -StartWhenAvailable -MultipleInstances IgnoreNew `
                    -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $Triggers `
        -Settings $settings -Principal $principal -Description $Desc | Out-Null
    Write-Host "  등록됨: $Name"
}

# --- 3시간마다 정기 브리핑 (00/03/06/09/12/15/18/21시 정각)
$briefTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours(0) `
                    -RepetitionInterval (New-TimeSpan -Hours 3) `
                    -RepetitionDuration (New-TimeSpan -Days 3650)
New-HiddenTask -Name "MarketBriefing_3h" -Vbs "run_brief.vbs" -Triggers $briefTrigger `
    -Desc "코인/도미넌스/미국·한국 주식 3시간 정기 브리핑 -> 슬랙"

# --- 10분마다 급등/급락 감시
$watchTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
                    -RepetitionInterval (New-TimeSpan -Minutes 10) `
                    -RepetitionDuration (New-TimeSpan -Days 3650)
New-HiddenTask -Name "MarketBriefing_Watch" -Vbs "run_watch.vbs" -Triggers $watchTrigger `
    -Desc "급등/급락 임계치 감시 -> 슬랙 즉시 알림"

Write-Host ""
Write-Host "완료. 확인:  Get-ScheduledTask MarketBriefing_*"
Write-Host "지금 바로 한 번 돌려보기:  Start-ScheduledTask -TaskName MarketBriefing_3h"
