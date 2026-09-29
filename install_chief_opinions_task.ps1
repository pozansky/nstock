param(
    [string]$At = "18:30",
    [int]$Workers = 2
)

$ErrorActionPreference = "Stop"
$taskName = "NStockChiefOpinionsDaily"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$syncScript = Join-Path $projectRoot "chief_opinions_sync.py"
$python = (Get-Command python -ErrorAction Stop).Source
$token = [Environment]::GetEnvironmentVariable("CHIEF_OPINIONS_TOKEN", "User")

if (-not $token) {
    throw "请先设置当前用户的 CHIEF_OPINIONS_TOKEN，再安装计划任务。"
}

$workers = [Math]::Max(1, [Math]::Min(16, $Workers))
$taskCommand = "`"$python`" `"$syncScript`" --limit 1 --workers $workers --max-age-hours 20 --retries 2"
& schtasks.exe /Create /SC DAILY /ST $At /TN $taskName /TR $taskCommand /F
if ($LASTEXITCODE -ne 0) {
    throw "创建计划任务失败，schtasks.exe 退出码：$LASTEXITCODE"
}
& schtasks.exe /Query /TN $taskName /V /FO LIST
