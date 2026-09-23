<#
.SYNOPSIS
    Register or manage Windows Scheduled Task for Douyin Podcast Sync.
    Executes automatically every day at 10:00 and 22:00.

.PARAMETER Action
    Create  - Register or overwrite the scheduled task (Default)
    Delete  - Remove the scheduled task
    RunNow  - Immediately trigger a run of the task
    Status  - Display current task status and schedule details
#>

param (
    [ValidateSet("Create", "Delete", "RunNow", "Status")]
    [string]$Action = "Create"
)

$TaskName = "DouyinPodcastSync"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$BatScript = Join-Path $ScriptDir "run_douyin_sync.bat"

if (-not (Test-Path $BatScript)) {
    Write-Error "Launcher script not found: $BatScript"
    exit 1
}

switch ($Action) {
    "Status" {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            Write-Host "Scheduled Task '$TaskName' exists." -ForegroundColor Green
            Write-Host "State: $($task.State)"
            $info = Get-ScheduledTaskInfo -TaskName $TaskName
            Write-Host "Last Run Time: $($info.LastRunTime)"
            Write-Host "Last Result  : $($info.LastTaskResult)"
            Write-Host "Next Run Time: $($info.NextRunTime)"
        } else {
            Write-Host "Scheduled Task '$TaskName' is NOT registered." -ForegroundColor Yellow
        }
    }

    "RunNow" {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-Warning "Task '$TaskName' not found. Creating it first..."
            & $PSCommandPath -Action Create
        }
        Write-Host "Starting task '$TaskName' immediately..." -ForegroundColor Cyan
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 2
        Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
    }

    "Delete" {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Host "Scheduled Task '$TaskName' has been successfully removed." -ForegroundColor Green
        } else {
            Write-Host "Scheduled Task '$TaskName' does not exist." -ForegroundColor Yellow
        }
    }

    "Create" {
        Write-Host "Registering Windows Scheduled Task: $TaskName" -ForegroundColor Cyan
        Write-Host "Project directory: $ProjectRoot"
        Write-Host "Script path      : $BatScript"

        # Action: Run bat file minimized / hidden via cmd.exe or powershell
        $TaskAction = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$BatScript`"" -WorkingDirectory $ProjectRoot

        # Triggers: 10:00 and 22:00 every day
        $Trigger1 = New-ScheduledTaskTrigger -Daily -At "10:00"
        $Trigger2 = New-ScheduledTaskTrigger -Daily -At "22:00"

        # Settings: Allow battery run, wake machine if supported, stop if runaway
        $TaskSettings = New-ScheduledTaskSettingsSet `
            -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries `
            -StartWhenAvailable `
            -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
            -RestartCount 1 `
            -RestartInterval (New-TimeSpan -Minutes 5)

        # Description
        $Description = "Douyin Podcast Sync to Cloudflare R2 and Apple Podcasts. Runs daily at 10:00 and 22:00."

        # Register task under current user
        Register-ScheduledTask `
            -TaskName $TaskName `
            -Action $TaskAction `
            -Trigger @($Trigger1, $Trigger2) `
            -Settings $TaskSettings `
            -Description $Description `
            -Force | Out-Null

        Write-Host ""
        Write-Host "Scheduled Task '$TaskName' registered successfully!" -ForegroundColor Green
        Write-Host "Scheduled times : 10:00 and 22:00 Daily" -ForegroundColor Yellow
        Write-Host "Log output file : $ProjectRoot\output\douyin_sync_cron.log" -ForegroundColor Gray
        Write-Host ""

        # Display status
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "Next run time   : $($info.NextRunTime)" -ForegroundColor Cyan
    }
}
