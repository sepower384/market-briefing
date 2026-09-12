# 마켓 브리핑 예약작업 제거
Unregister-ScheduledTask -TaskName "MarketBriefing_3h" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "MarketBriefing_Watch" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "예약작업 제거 완료"
