param(
    [string]$PythonPath = '',
    [switch]$PrintPlan,
    [switch]$Uninstall
)
$ErrorActionPreference = 'Stop'
$taskName = 'Codex Attention - Usage Watch'
$root = $PSScriptRoot
$script = Join-Path $root 'codex_usage_watch.py'
$arguments = '"' + $script + '" guard'
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and $existing.Actions.Arguments -ne $arguments) {
    throw "The task name is already used by another installation. Uninstall that copy first: $taskName"
}
if ($Uninstall) {
    if ($PrintPlan) {
        @{action='uninstall'; taskName=$taskName; installed=[bool]$existing} | ConvertTo-Json
        return
    }
    if ($existing) {
        $state = Join-Path $root 'usage-watch'
        New-Item -ItemType Directory -Path $state -Force | Out-Null
        New-Item -ItemType File -Path (Join-Path $state 'paused.flag') -Force | Out-Null
        New-Item -ItemType File -Path (Join-Path $state 'stop.request') -Force | Out-Null
        Stop-ScheduledTask -TaskName $taskName
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }
    @{action='uninstalled'; taskName=$taskName} | ConvertTo-Json
    return
}
if (-not $PythonPath -and $env:CODEX_ATTENTION_PYTHON) { $PythonPath = $env:CODEX_ATTENTION_PYTHON }
if (-not $PythonPath) {
    $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundled -PathType Leaf) {
        $PythonPath = $bundled
    } elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $PythonPath = (& py.exe -3 -c 'import sys; print(sys.executable)')
    } else {
        $command = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($command) { $PythonPath = $command.Source }
    }
}
if (-not $PythonPath -or -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw 'Python 3.10+ is required. Pass -PythonPath or set CODEX_ATTENTION_PYTHON.'
}
$version = & $PythonPath -c 'import sys; print(sys.version_info[0], sys.version_info[1])'
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^3\s+(\d+)$' -or [int]$Matches[1] -lt 10) {
    throw 'Python 3.10 or newer is required.'
}
$pythonw = Join-Path (Split-Path -Parent $PythonPath) 'pythonw.exe'
foreach ($file in @($pythonw, $script)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing file: $file" }
}
if ($PrintPlan) {
    @{action='install'; taskName=$taskName; python=$pythonw; script=$script; root=$root; scope='current user'} | ConvertTo-Json
    return
}
if ($existing) { Stop-ScheduledTask -TaskName $taskName }
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$boundary = (Get-Date).AddSeconds(10).ToString('yyyy-MM-ddTHH:mm:ss')
$escapedPython = [System.Security.SecurityElement]::Escape($pythonw)
$escapedScript = [System.Security.SecurityElement]::Escape($arguments)
$escapedRoot = [System.Security.SecurityElement]::Escape($root)
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Codex Attention: local quota alerts. No AI or network calls.</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>$sid</UserId></LogonTrigger>
    <TimeTrigger><Repetition><Interval>PT1M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition><StartBoundary>$boundary</StartBoundary><Enabled>true</Enabled></TimeTrigger>
  </Triggers>
  <Principals><Principal id="CurrentUser"><UserId>$sid</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit><Priority>7</Priority>
    <RestartOnFailure><Interval>PT1M</Interval><Count>999</Count></RestartOnFailure>
  </Settings>
  <Actions Context="CurrentUser"><Exec><Command>$escapedPython</Command><Arguments>$escapedScript</Arguments><WorkingDirectory>$escapedRoot</WorkingDirectory></Exec></Actions>
</Task>
"@
Register-ScheduledTask -TaskName $taskName -Xml $xml -Force | Out-Null
$state = Join-Path $root 'usage-watch'
New-Item -ItemType Directory -Path $state -Force | Out-Null
$pause = Join-Path $state 'paused.flag'
if (Test-Path -LiteralPath $pause) { Remove-Item -LiteralPath $pause }
Start-ScheduledTask -TaskName $taskName
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName,State | ConvertTo-Json -Compress
