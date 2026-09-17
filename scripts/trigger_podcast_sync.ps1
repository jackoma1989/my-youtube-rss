# Trigger GitHub Actions Podcast Sync Workflow
param(
    [string]$Token = ""
)

$Repo = "jackoma1989/my-youtube-rss"
$Workflow = "podcast_sync.yml"
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

$Uri = "https://api.github.com/repos/$Repo/actions/workflows/$Workflow/dispatches"
$Headers = @{
    "Accept"               = "application/vnd.github+json"
    "Authorization"        = "Bearer $Token"
    "X-GitHub-Api-Version" = "2022-11-28"
}
$Body = @{
    ref = $Ref
} | ConvertTo-Json

try {
    $Response = Invoke-RestMethod -Uri $Uri -Method Post -Headers $Headers -Body $Body -ErrorAction Stop
    $SuccessMsg = "[$Timestamp] SUCCESS: Successfully dispatched GitHub Actions workflow '$Workflow' on branch '$Ref'."
    Write-Host $SuccessMsg -ForegroundColor Green
    Add-Content -Path $LogFile -Value $SuccessMsg
    exit 0
} catch {
    $ErrDetail = $_.Exception.Message
    if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
        $ErrDetail += " (" + $_.ErrorDetails.Message + ")"
    }
    $FailMsg = "[$Timestamp] ERROR: Failed to trigger workflow: $ErrDetail"
    Write-Error $FailMsg
    Add-Content -Path $LogFile -Value $FailMsg
    exit 1
}
