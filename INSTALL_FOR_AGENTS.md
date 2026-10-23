# Installing this utility with an agent

This is a Windows source utility. Read `README.md`, the Python files, and the PowerShell renderer before installing it.

1. Choose a stable, writable directory with the user. Copy only the source, launchers, default configuration, documentation, and license. Do not copy another user's `logs/` or `usage-watch/` directories.
2. Locate Python 3.10+. The launchers support `CODEX_ATTENTION_PYTHON`; the autostart installer supports `-PythonPath`. Do not copy or request account credentials.
3. Run `run-python.cmd -m unittest -v test_codex_usage_watch test_usage_attribution`, `powershell -NoProfile -STA -File test_attention_ui.ps1`, and check the launcher help commands. These checks do not show screen alerts.
4. Keep `notify_attention.py`, `show-attention.ps1`, `attention_ui.cs`, and `usage_attribution.py` together for blue attention alerts. Codex resolves the calling chat's title from its local thread ID. Other agents can pass `--chat-title`. Use the example instructions in `AGENTS.example.md` if the user wants agent integration. Adapt paths to this installation.
5. For quota monitoring, use `codex-usage-watch.cmd start/status/stop`. If the user wants automatic startup with Codex, preview the task with `Install-CodexUsageAutostart.ps1 -PrintPlan` and then install it. Do not create a second recurring AI automation.
6. Preserve existing agent instructions when adding attention guidance. Do not overwrite a user's entire `AGENTS.md`, skills, hooks, or settings to install this tool.

The quota watcher relies on existing local records. It cannot provide exact per-chat quota billing, and it needs updates if the source log format or app package identity changes.
