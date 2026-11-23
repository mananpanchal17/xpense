# Running the Xpense copy

The copy runs at **http://127.0.0.1:5056**. Your original keeps running at http://127.0.0.1:5055, and the two don't share any files.

## It starts by itself

The Windows scheduled task **Xpense Copy Local Website** starts the copy hidden every time you sign in to Windows. If the server stops or crashes, the launcher (`tools\start-local.ps1`) restarts it within about 10 seconds. Just open http://127.0.0.1:5056/ in your browser. Your computer needs to be on and awake.

Stop it (until next sign-in):

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\MANAN\Downloads\Expense_Manager\expense_manager\xpense-copy\tools\stop-local.ps1"
```

Start it again now:

```powershell
Start-ScheduledTask -TaskName "Xpense Copy Local Website"
```

Turn off auto-start (or use Task Scheduler → Disable):

```powershell
Disable-ScheduledTask -TaskName "Xpense Copy Local Website"
```

Don't use Task Scheduler's **End** button by itself. It stops only the restart loop and leaves the server running. Use `stop-local.ps1` instead.

Logs: `.qa\local-server.log`, `.qa\local-server.error.log`, and `.qa\local-server.previous-error.log` (from before the last restart).

## Start it by hand (when auto-start is off)

Double-click `start-copy.bat` (only while the task isn't running, or port 5056 will already be in use), or in PowerShell:

```powershell
cd "C:\Users\MANAN\Downloads\Expense_Manager\expense_manager\xpense-copy"
.\start-copy.bat
```

Leave that window open while you use the site, and press Ctrl+C to stop it. If `.venv` is missing, the batch file creates it and installs `requirements.txt` first.

## Start it manually

```powershell
cd "C:\Users\MANAN\Downloads\Expense_Manager\expense_manager\xpense-copy"
$env:XPENSE_PREVIEW_PORT = "5056"
.\.venv\Scripts\python.exe tools\preview.py
```

To use another port, change `XPENSE_PREVIEW_PORT`.

## Rebuild the virtual environment

```powershell
cd "C:\Users\MANAN\Downloads\Expense_Manager\expense_manager\xpense-copy"
& "C:\Program Files\Python314\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Run the tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp "$env:TEMP\xpense-copy-pytest"
```

## Data

The site uses `.qa\preview-v2.db`, a snapshot of the original's sample ledger taken on 23 Nov 2026. Every visitor is signed in automatically as "Preview workspace". To start with an empty sample ledger, stop the server, delete `.qa\preview-v2.db`, and start it again; the sample data is reseeded.

For a real multi-user setup with login, copy `.env.example` to `.env`, set a random `SECRET_KEY` of 32 or more characters, then run:

```powershell
.\.venv\Scripts\python.exe -m flask --app run init-db
.\.venv\Scripts\python.exe -m flask --app run run --port 5056
```
