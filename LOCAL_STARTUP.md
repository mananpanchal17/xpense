# Automatic local startup

The local sample website is configured as the current user's Windows scheduled task `Xpense Local Website`. It starts at Windows sign-in, runs without a terminal window, and restarts after an unexpected failure. Windows must be running and awake. Open http://127.0.0.1:5055/ in any browser on this computer.

It uses the existing synthetic preview ledger in `.qa/preview-v2.db` and automatic sample-user sign-in. The server listens only on 127.0.0.1. The original downloaded project and personal database remain separate. Keep this workspace and the original `.venv` folder at their current locations; the launcher refers to them by absolute path.

Server output is saved to `.qa/local-server.log` and `.qa/local-server.error.log` and replaced on each start. To stop it, open Windows Task Scheduler, select `Xpense Local Website` and choose End. To prevent future automatic starts, choose Disable. Choose Run to start it again. Removing the task does not delete the application or its data.

