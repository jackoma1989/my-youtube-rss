<#
.SYNOPSIS
    Register or manage Windows Scheduled Tasks for Podcast Sync (YouTube, Douyin, Bilibili).

.PARAMETER Action
    Create  - Register or overwrite the scheduled task(s) (Default)
    Delete  - Remove the scheduled task(s)
    RunNow  - Immediately trigger a run of the task
    Status  - Display current task status and schedule details

.PARAMETER Platform
    Douyin    - Douyin sync daily at 10:30 and 22:30
    Bilibili  - Bilibili sync daily at 10:15 and 22:15
    YouTube   - YouTube sync daily at 10:00 and 22:00
    All       - Register/manage tasks for all three platforms
#>

param (
    [ValidateSet("Create", "Delete", "RunNow", "Status")]
    [string]$Action = "Create",

    [ValidateSet("Douyin", "Bilibili", "YouTube", "All")]
    [string]$Platform = "Bilibili"
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$TriggerScript = Join-Path $ScriptDir "trigger_podcast_sync.ps1"

if (-not (Test-Path $TriggerScript)) {
    Write-Error "Trigger script not found: $TriggerScript"
    exit 1
}

$PlatformMap = @{
    "YouTube"  = @{
        TaskName    = "YouTubePodcastSyncTrigger";
        Workflow    = "podcast_sync.yml";
        Times       = @("10:00", "22:00");
        Description = "Trigger GitHub Actions YouTube Podcast Sync daily at 10:00 and 22:00."
    };
    "Bilibili" = @{
        TaskName    = "BilibiliPodcastSyncTrigger";
        Workflow    = "bilibili_sync.yml";
        Times       = @("10:15", "22:15");
        Description = "Trigger GitHub Actions Bilibili Podcast Sync daily at 10:15 and 22:15."
    };
    "Douyin"   = @{
        TaskName    = "DouyinPodcastSyncTrigger";
        Workflow    = "douyin_sync.yml";
        Times       = @("10:30", "22:30");
        Description = "Trigger GitHub Actions Douyin Podcast Sync daily at 10:30 and 22:30."
    };
}

$PlatformsToProcess = if ($Platform -eq "All") { @("YouTube", "Bilibili", "Douyin") } else { @($Platform) }

foreach ($p in $PlatformsToProcess) {
    $info = $PlatformMap[$p]
    $tName = $info.TaskName
    $wf = $info.Workflow
    $times = $info.Times
    $desc = $info.Description

    Write-Host "========================================" -ForegroundColor DarkGray
    Write-Host "Processing [$p] -> Task: $tName" -ForegroundColor Cyan

    switch ($Action) {
        "Status" {
            $task = Get-ScheduledTask -TaskName $tName -ErrorAction SilentlyContinue
            if ($task) {
                Write-Host "Task '$tName' exists." -ForegroundColor Green
                Write-Host "  State: $($task.State)"
                $tInfo = Get-ScheduledTaskInfo -TaskName $tName
                Write-Host "  Last Run Time: $($tInfo.LastRunTime)"
                Write-Host "  Last Result  : $($tInfo.LastTaskResult)"
                Write-Host "  Next Run Time: $($tInfo.NextRunTime)"
            } else {
                Write-Host "Task '$tName' is NOT registered." -ForegroundColor Yellow
            }
        }

        "RunNow" {
            $task = Get-ScheduledTask -TaskName $tName -ErrorAction SilentlyContinue
            if (-not $task) {
                Write-Warning "Task '$tName' not found. Creating it first..."
                & $PSCommandPath -Action Create -Platform $p
            }
            Write-Host "Starting task '$tName' immediately..." -ForegroundColor Cyan
            Start-ScheduledTask -TaskName $tName
            Start-Sleep -Seconds 2
            Get-ScheduledTask -TaskName $tName | Select-Object TaskName, State
        }

        "Delete" {
            $task = Get-ScheduledTask -TaskName $tName -ErrorAction SilentlyContinue
            if ($task) {
                Unregister-ScheduledTask -TaskName $tName -Confirm:$false
                Write-Host "Task '$tName' successfully removed." -ForegroundColor Green
            } else {
                Write-Host "Task '$tName' does not exist." -ForegroundColor Yellow
            }
        }

        "Create" {
            Write-Host "Registering task: $tName" -ForegroundColor Cyan
            Write-Host "  Scheduled times : $($times -join ', ') Daily"
            Write-Host "  Workflow        : $wf"

            $Arguments = "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$TriggerScript`" -Workflow `"$wf`""
            $TaskAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $Arguments -WorkingDirectory $ProjectRoot

            $triggers = @()
            foreach ($t in $times) {
                $triggers += New-ScheduledTaskTrigger -Daily -At $t
            }

            $TaskSettings = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -StartWhenAvailable `
                -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
                -RestartCount 1 `
                -RestartInterval (New-TimeSpan -Minutes 5)

            Register-ScheduledTask `
                -TaskName $tName `
                -Action $TaskAction `
                -Trigger $triggers `
                -Settings $TaskSettings `
                -Description $desc `
                -Force | Out-Null

            Write-Host "Task '$tName' registered successfully!" -ForegroundColor Green
            $tInfo = Get-ScheduledTaskInfo -TaskName $tName
            Write-Host "Next run time: $($tInfo.NextRunTime)" -ForegroundColor Yellow
        }
    }
}
Write-Host ""
