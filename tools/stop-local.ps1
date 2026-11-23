# Stops the xpense-copy website: ends the sign-in task's restart loop, then the server itself.
# Only processes started from this folder's .venv are stopped; the original on 5055 is untouched.
$projectDirectory = Split-Path -Parent $PSScriptRoot
Stop-ScheduledTask -TaskName 'Xpense Copy Local Website' -ErrorAction SilentlyContinue
$venvPython = Join-Path $projectDirectory '.venv\Scripts\python.exe'
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*$venvPython*" -or $_.CommandLine -like "*$projectDirectory\tools\preview.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Output 'Xpense copy stopped. It starts again at next sign-in, or run: Start-ScheduledTask -TaskName "Xpense Copy Local Website"'
