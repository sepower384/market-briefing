# 슬랙 웹훅 URL 등록 + 즉시 테스트 발송
#   powershell -ExecutionPolicy Bypass -File C:\dev\market-briefing\set_webhook.ps1 https://hooks.slack.com/services/XXX/YYY/ZZZ
param([Parameter(Mandatory=$true)][string]$Url)

$ErrorActionPreference = "Stop"
$base = "C:\dev\market-briefing"
$cfgPath = Join-Path $base "config.json"

if ($Url -notmatch '^https://hooks\.slack\.com/services/') {
    throw "슬랙 웹훅 URL 형식이 아닙니다. https://hooks.slack.com/services/... 로 시작해야 합니다."
}

$raw = Get-Content $cfgPath -Raw -Encoding UTF8
$cfg = $raw | ConvertFrom-Json
$cfg.slack_webhook_url = $Url
$cfg.slack_mode = "webhook"
$cfg | ConvertTo-Json -Depth 10 | Set-Content $cfgPath -Encoding UTF8

Write-Host "웹훅 저장 완료 -> $cfgPath"
Write-Host "테스트 발송 중..."

$env:PYTHONUTF8 = "1"
& python (Join-Path $base "briefing.py") test
