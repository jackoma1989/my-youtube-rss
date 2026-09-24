# Trigger GitHub Actions Podcast Sync Workflow (YouTube and/or Douyin)
param(
    [string]$Workflow = "podcast_sync.yml",
    [string]$Token = ""
)

$Repo = "jackoma1989/my-youtube-rss"
$Ref = "main"

# Auto-detect token if not explicitly provided
if (-not $Token) {
    if ($env:GITHUB_TOKEN) {
        $Token = $env:GITHUB_TOKEN.Trim()
    } else {
        try {
            $ghToken = (gh auth token 2>$null)
            if ($ghToken) {
                $Token = $ghToken.Trim()
            }
        } catch {}
    }
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = (Get-Location).Path }
$LogFile = Join-Path $ScriptDir "trigger_sync.log"
$Timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")

if (-not $Token) {
    $ErrMsg = "[$Timestamp] ERROR: GitHub Token not found. Please configure GITHUB_TOKEN or login with gh."
    Write-Error $ErrMsg
    Add-Content -Path $LogFile -Value $ErrMsg
    exit 1
}

$Headers = @{
    "Accept"               = "application/vnd.github+json"
    "Authorization"        = "Bearer $Token"
    "X-GitHub-Api-Version" = "2022-11-28"
}
$Body = @{
    ref = $Ref
} | ConvertTo-Json

$WorkflowsToTrigger = @()
if ($Workflow -eq "all") {
    $WorkflowsToTrigger = @("podcast_sync.yml", "douyin_sync.yml", "bilibili_sync.yml")
} elseif ($Workflow -eq "bilibili" -or $Workflow -eq "bili") {
    $WorkflowsToTrigger = @("bilibili_sync.yml")
} elseif ($Workflow -eq "douyin") {
    $WorkflowsToTrigger = @("douyin_sync.yml")
} elseif ($Workflow -eq "youtube") {
    $WorkflowsToTrigger = @("podcast_sync.yml")
} else {
    $WorkflowsToTrigger = @($Workflow)
}

$allSuccess = $true
foreach ($wf in $WorkflowsToTrigger) {
    $Uri = "https://api.github.com/repos/$Repo/actions/workflows/$wf/dispatches"
    try {
        $Response = Invoke-RestMethod -Uri $Uri -Method Post -Headers $Headers -Body $Body -ErrorAction Stop
        $SuccessMsg = "[$Timestamp] SUCCESS: Successfully dispatched GitHub Actions workflow '$wf' on branch '$Ref'."
        Write-Host $SuccessMsg -ForegroundColor Green
        Add-Content -Path $LogFile -Value $SuccessMsg
    } catch {
        $allSuccess = $false
        $ErrDetail = $_.Exception.Message
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            $ErrDetail += " (" + $_.ErrorDetails.Message + ")"
        }
        $FailMsg = "[$Timestamp] ERROR: Failed to trigger workflow '$wf': $ErrDetail"
        Write-Error $FailMsg
        Add-Content -Path $LogFile -Value $FailMsg
    }
}

if ($allSuccess) { exit 0 } else { exit 1 }
