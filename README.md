# Codex Attention

Small Windows tools for making agent activity easier to notice:

- **Attention:** a notification, sound, and blue screen border when you need to check a chat. Hover at an edge to see which chat called you. Closes after 12 seconds by default.
- **Quota alert:** a persistent red border when Codex's recorded quota is dropping quickly. Hover at an edge to see the most active local chats. Clears when the measured rate falls or the source becomes stale.

The quota watcher uses the attention renderer. You can use the blue attention tool by itself.

## Local web dashboard (이 브랜치의 로컬 웹 모니터)

화면 테두리 대신 로컬 웹의 **상태 버튼 불빛**으로 채팅과 사용량을 확인할 수 있습니다.
Python 표준 라이브러리만 사용하며, 프런트엔드는 `web/`의 HTML/CSS/JavaScript입니다.

**더블클릭 실행:** [`exec/Open-Codex-Dashboard.cmd`](exec/Open-Codex-Dashboard.cmd)를
열면 웹 서버를 백그라운드에서 시작하고 기본 브라우저에 대시보드를 엽니다.
이미 실행 중이면 기존 서버를 사용해 페이지만 엽니다. 어느 폴더에서 실행해도 됩니다.

```powershell
# 기존 화면 테두리 감시기를 중지하고 자동 재시작도 일시정지
.\codex-usage-watch.cmd stop

# 백그라운드 실행 + 기본 브라우저에서 열기
.\codex-dashboard.cmd start --open

# 실행 상태 / 종료
.\codex-dashboard.cmd status
.\codex-dashboard.cmd stop
```

주소: **http://127.0.0.1:8765**. 터미널에서 직접 실행하려면
`.\codex-dashboard.cmd run`, 다른 포트는 `--port 8766`, Codex 기록 경로는
`--home "D:\path\to\.codex"`로 지정합니다. 종료할 때도 같은 `--port`를 사용하세요.
Windows 시작 작업이나 서비스를 추가하지 않으며, 한 포트에서 중복 실행하지 않습니다.

- 채팅의 진행 중 / 확인 대기 / 응답 완료 / 중단 / 오류 / 기록 지연 상태를 표시합니다.
- 채팅 제목, 프로젝트, 모델 검색과 상태 필터, 정렬, 하위 에이전트·보관된 채팅 표시를 지원합니다.
- 목록의 **사용된 누적 토큰 수**는 누적 입력 + 출력 합계입니다. 캐시 입력·추론 출력은 중복 합산하지 않습니다.
  세션에 토큰 기록이 없으면 로컬 DB의 누적값을 사용하고, 어느 쪽에도 없으면 `—`로 표시합니다.
- 상태 버튼을 누르면 작업 시각, 토큰 수, 최근 상태 기록, 복사 가능한 채팅 ID가 나옵니다.
- 사용량 신호 버튼은 빠른 소모에 **빨강**, 정상 범위에 **초록**, 관측 중·오래된 기록에 **회색**으로 켜집니다.
  버튼을 누르면 판단 기준을 펼칩니다. 판정 설정은 기존 `usage-watch-config.json`을 사용합니다.
- 할당량 잔여 비율, 최근 5분간 로컬 사용 비중, 기존 Attention 알림 내역을 함께 표시합니다.
- 기본 1초마다 파일 추가분을 읽고 SSE로 웹에 전달합니다. 새 채팅·이름 변경은 최대 약 5초 후 반영합니다.
  연결이 끊기면 재접속하며, SSE를 사용할 수 없으면 5초 폴링으로 전환합니다.

웹 서버는 `127.0.0.1`에서만 열리고 외부 요청·다른 웹사이트의 교차 출처 요청은 차단합니다.
화면 테두리, 소리, Windows 알림을 실행하지 않습니다. 기존 테두리 감시기를 별도로 다시 시작하면
그 감시기의 화면 알림은 다시 작동합니다. `stop`으로 설정한 일시정지는 `start` 전까지 유지됩니다.

상태는 이 PC의 **로컬 기록을 바탕으로 한 관측값**입니다. 앱 내부 상태 API와 직접 연결하지 않으므로,
원격에서만 실행되는 채팅이나 기록에 나타나지 않는 승인 대기는 확인할 수 없습니다.
진행 기록이 3분 이상 끊기면 완료로 추측하지 않고 `기록 지연`으로 표시합니다
(`--stale-seconds`로 조정). 큰 세션은 시작 시 마지막 1 MiB만 읽으며, 앞선 기록이 생략될 수 있습니다.
DB 메타데이터는 최신 500개까지 읽고, 로컬 세션에서 발견한 채팅도 함께 표시합니다.
사용 비중은 이 범위 안에서 관측된 최근 이벤트의 추정치입니다.

채팅 본문·도구 인수·도구 출력은 웹 API에 포함하거나 별도로 저장하지 않습니다.
채팅 제목·프로젝트 경로와 기존 알림 문구는 브라우저에 표시되므로 화면 공유 시 참고하세요.
인증정보를 읽지 않습니다. 실행 상태는 Git에서 제외된 `dashboard-state/`에 저장됩니다.

검증:

```powershell
.\run-python.cmd -m unittest -v test_codex_dashboard test_codex_usage_watch test_usage_attribution
```

Hover text uses an opaque background and native Windows text rendering. The helper opts into display DPI awareness and sizes its text and layout for the target monitor; it keeps click-through input and does not change Windows display settings.

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

The tests use synthetic records and temporary directories. They cover detection, cached-value rebounds, quota resets, partial writes, Windows notifications, timer expiry, attribution, forks, and current sidebar titles. The UI check compiles the renderer and checks placement, opaque text, actual click-through window styles, and 100/125/150/200% display layouts without showing real test alerts.

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
