# Runs the loopback-only sample website for the current Windows user.
$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
# xpense-copy: uses its own .venv and port 5056 so it never touches the original on 5055.
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$logDirectory = Join-Path $projectDirectory '.qa'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
if (-not $env:XPENSE_PREVIEW_PORT) { $env:XPENSE_PREVIEW_PORT = '5056' }
$env:PYTHONDONTWRITEBYTECODE = '1'
$previewScript = Join-Path $PSScriptRoot 'preview.py'
$outLog = Join-Path $logDirectory 'local-server.log'
$errLog = Join-Path $logDirectory 'local-server.error.log'
# Task Scheduler's "restart on failure" only covers failed launches, not a server that
# exits later, so this loop restarts the website itself. End the task to stop it.
# Errors never end the loop; each start, exit and error is written to .qa\launcher.log.
$launcherLog = Join-Path $logDirectory 'launcher.log'
function Write-LauncherLog($message) {
    try {
        if ((Test-Path $launcherLog) -and (Get-Item $launcherLog).Length -gt 1MB) { Move-Item $launcherLog "$launcherLog.old" -Force }
        Add-Content -Path $launcherLog -Value ("{0:yyyy-MM-dd HH:mm:ss}  pid {1}  {2}" -f (Get-Date), $PID, $message)
    } catch {}
}
Write-LauncherLog "launcher started (port $env:XPENSE_PREVIEW_PORT)"
$quickFailures = 0
while ($true) {
    $startedAt = Get-Date
    try {
        try { Copy-Item $errLog (Join-Path $logDirectory 'local-server.previous-error.log') -Force -ErrorAction Stop } catch {}
        $server = Start-Process -FilePath $pythonExecutable -ArgumentList ('"' + $previewScript + '"') -WorkingDirectory $projectDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput $outLog -RedirectStandardError $errLog
        $null = $server.Handle  # open the handle now, or ExitCode stays empty after exit
        Write-LauncherLog "server started (pid $($server.Id))"
        $server.WaitForExit()
        Write-LauncherLog "server exited (code $($server.ExitCode))"
    } catch {
        Write-LauncherLog "start failed: $($_.Exception.Message)"
    }
    # Back off when the server keeps dying straight away (e.g. missing .venv or a startup error).
    if (((Get-Date) - $startedAt).TotalSeconds -lt 30) { $quickFailures++ } else { $quickFailures = 0 }
    $wait = if ($quickFailures -ge 5) { 60 } else { 5 }
    if ($quickFailures -eq 5) { Write-LauncherLog "5 quick failures in a row; retrying every 60 seconds" }
    Start-Sleep -Seconds $wait
}
