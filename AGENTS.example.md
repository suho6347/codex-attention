# Optional attention instructions

Adapt the paths below to the installation directory and merge this section into your existing agent instructions.

- Use the normal question tool when an answer, choice, or confirmation is needed.
- When the user must operate a device, press a button, or select a file, explain all required actions in the chat first. Then call the attention tool once for that request.
- Do not invent a question merely to attract attention. Do not repeat the alert for the same pending action.

Example in PowerShell:

```powershell
& "<installation directory>\attention.cmd" "Please check the chat instructions" --title "Action needed"
```

The default blue border and sound last 12 seconds. The alert does not take keyboard focus. The quota watcher manages its own persistent red border separately.

Hovering over the blue edge shows the calling chat's current sidebar title. Codex titles are resolved locally from `CODEX_THREAD_ID`; other agents should pass `--chat-title "Current chat title"` when available.
