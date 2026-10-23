# Codex Attention

Small Windows tools for making agent activity easier to notice:

- **Attention:** a notification, sound, and blue screen border when you need to check a chat. Hover at an edge to see which chat called you. Closes after 12 seconds by default.
- **Quota alert:** a persistent red border when Codex's recorded quota is dropping quickly. Hover at an edge to see the most active local chats. Clears when the measured rate falls or the source becomes stale.

The quota watcher uses the attention renderer. You can use the blue attention tool by itself.

An unofficial, experimental utility. Windows 10/11, Windows PowerShell 5.1, and Python 3.10+ are required. The Python standard library is enough; there are no pip dependencies.

## Get started

Download the source ZIP from **Code → Download ZIP**, or clone this repository:

```powershell
git clone https://github.com/Tl-I/codex-attention.git
cd codex-attention
```

Keep the folder in a writable location. The launchers look for `CODEX_ATTENTION_PYTHON`, Codex's bundled Python runtime, the Windows `py` launcher, and then `python` on PATH.

Show an attention alert:

```powershell
.\attention.cmd "Please check the chat" --title "Attention"
```

The calling chat's current sidebar title is read locally using `CODEX_THREAD_ID` when available. Other agents can pass `--chat-title "Chat title"`. Hover over the blue edge to show the title at the top of that screen. The title disappears when you leave the edge, and the helper exits with the blue alert. It does not take focus or intercept clicks.

Start quota monitoring manually:

```powershell
.\codex-usage-watch.cmd start
.\codex-usage-watch.cmd status
.\codex-usage-watch.cmd stop
```

`start` resumes monitoring after a manual `stop`. The blue attention tool does not require the quota watcher or an autostart task.

## Start with Codex

In PowerShell, preview and then install the current-user scheduled task:

```powershell
.\Install-CodexUsageAutostart.ps1 -PrintPlan
.\Install-CodexUsageAutostart.ps1
```

An explicit interpreter is also supported:

```powershell
.\Install-CodexUsageAutostart.ps1 -PythonPath "D:\Python\python.exe"
```

The task watches for the Microsoft Store Codex package, starts monitoring when that app runs, and recovers a stopped monitor. A manual `stop` remains paused until `start`. Keep the installation folder in place while the task is registered.

Remove the autostart task:

```powershell
.\Install-CodexUsageAutostart.ps1 -Uninstall
```

This removes the task for this copy and stops its monitor. Generated local logs remain in the folder.

## Quota detection

The watcher reads Codex Desktop's existing `token_count.rate_limits` events under `$CODEX_HOME/sessions` or `~/.codex/sessions`.

Default settings in `usage-watch-config.json`:

| Setting | Default |
| --- | --- |
| Turn red | A measured decrease of 1 percentage point per 600 seconds or faster |
| Clear red | Rate falls to 1 percentage point per 800 seconds or slower |
| Missing fresh quota records | Clear after 120 seconds |
| File size checks | Every 5 seconds |
| Status heartbeat | Every 30 seconds |
| Recent chat comparison | Last 300 seconds, top 5 chats |

These are rate comparisons; the watcher evaluates newly available data without waiting for a fixed ten-minute bucket. The initial single percentage change is insufficient to establish an interval. Multiple-point drops can be detected using a conservative allowance for integer rounding.

The red border stays while the fast state remains active. Its helper gets a short renewable lease, so it closes if the monitor dies. Notifications and sounds occur when the red border starts, rather than on every update.

Windows file notifications, cached file paths, lightweight size checks, and a kernel timeout keep idle work small. Open files can delay Windows change notifications, so size checks remain as a fallback.

## Hover over the red border

Move the pointer to a red screen edge and pause briefly. A small panel shows the actual sidebar titles, models, and relative shares for the five largest recent local contributors. Leave the edge and panel to hide it. It stays click-through and never takes keyboard focus.

The panel compares the last five minutes by default. It shares the quota watcher's existing incremental reads, retains a bounded in-memory history, and refreshes with the existing red-border lease. It creates no extra process or network poll for attribution. Title lookups use a local database in read-only mode, with a local index fallback. The renderer checks the pointer only while an alert exists; lease files are read on change with a fallback check instead of being parsed twice a second.

For supported models, the comparison score is `(input - cached input) × input rate + cached input × cache rate + output × output rate`, using the [published Standard ChatGPT credit rates](https://learn.chatgpt.com/docs/pricing) checked on 2026-10-23. Reasoning output is not added a second time. These rates are stored in `usage_attribution.py`; no online price lookup occurs at runtime.

**The percentage is a comparison among locally observed chats, not a percentage of your subscription quota.** Subscription allocation, speed modes, image/tool charges, missing records, and activity on other computers can differ. Speed mode is not present in the observed records, so the panel explicitly says it is not included. If any observed model has no known rate, the whole panel falls back to token activity shares and labels that basis. Forked history and repeated token snapshots are filtered; child-agent work is grouped with its parent when that relationship is available.

## Privacy and limits

- No AI calls, network requests, sign-in, API keys, telemetry upload, or account credential reads by the tools.
- The watcher selects quota, token counters, model context, and chat metadata from local session files. It does not save chat messages. Those source files can contain conversations, so source access remains local.
- Notification text and local status are written to `logs/` and `usage-watch/`. Both directories are excluded from Git. Review generated files before sharing them separately.
- Chat titles and recent comparison shares are stored locally in the active red-border lease. Generated state stays local and is excluded from Git.
- Quota is account-wide. The hover panel estimates local contributors; it cannot allocate exact quota percentages to individual chats, models, or computers.
- Integer percentages and delayed app updates limit timing accuracy. The local log format and Store package detection may need maintenance after app updates.
- The program is a source utility; it does not include an EXE installer or macOS/Linux support.

## Use with another agent

See [INSTALL_FOR_AGENTS.md](INSTALL_FOR_AGENTS.md) and [AGENTS.example.md](AGENTS.example.md). An agent can inspect the source, place it in a folder you choose, and adapt the example instructions. Installation does not automatically edit your agent configuration.

## Test

```powershell
.\run-python.cmd -m unittest -v test_codex_usage_watch test_usage_attribution
powershell -NoProfile -STA -File .\test_attention_ui.ps1
```

The tests use synthetic records and temporary directories. They cover detection, cached-value rebounds, quota resets, partial writes, Windows notifications, timer expiry, attribution, forks, and current sidebar titles. The UI check compiles the renderer and checks placement and focus protection without showing real test alerts.

## License

[MIT](LICENSE). Reuse and modifications are welcome; retain the copyright and license notice.

---

## 한국어 요약

Windows에서 사용하는 작은 알림 도구입니다. 파란 테두리로 사용자 확인을 요청하고,
Codex 할당량이 빠르게 줄면 빨간 테두리로 알려 줍니다. 두 기능을 한 저장소에 묶었으며
어텐션만 따로 사용해도 됩니다.

별도 AI 호출이나 계정 인증정보 없이 기존 로컬 기록을 읽습니다. 빨간 가장자리에 마우스를 대면
최근 5분간 많이 사용한 채팅의 실제 제목과 추정 소모 비중을 보여 줍니다. 파란 가장자리에서는
알림을 호출한 채팅 제목을 보여 줍니다. 비중은 로컬 기록 비교이며 정확한 할당량 배분은 아닙니다.
설치 경로와 Python을 자동으로 찾도록 정리한 소스 도구이며,
다른 AI에게 이 저장소를 읽고 원하는 위치에 설치해 달라고 요청해도 됩니다.
