param([string]$RequestPath, [switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
if ($ValidateOnly) {
    Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing -Path (Join-Path $PSScriptRoot 'attention_ui.cs')
    [FeDisplay]::EnableDpiAwareness()
    return
}
if ([string]::IsNullOrWhiteSpace($RequestPath)) { throw 'RequestPath is required' }
$request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$duration = [Math]::Min(30, [Math]::Max(3, [int]$request.seconds))
$isAlarm = $request.color -eq 'red'
$statusPath = $RequestPath + '.status.json'
$persistent = $isAlarm -and -not [string]::IsNullOrWhiteSpace([string]$request.while_active)
$edgeDuration = if ($persistent) { $null } else { $duration }
$script:leaseExpires = 0.0
$script:leaseActive = $false
$script:contributors = $null
$script:usageHover = $null
function Update-UsagePopup {
    if ($null -eq $script:usageHover) { return }
    $data = $script:contributors
    $rows = @($data.rows | Where-Object { $null -ne $_ })
    $minutes = if ($null -ne $data) { [Math]::Round([double]$data.window_seconds / 60, 1) } else { 5 }
    $basis = if ($data.basis -eq 'tokens') { '토큰 활동 비중' } else { '소모 추정 비중' }
    $footer = if ($data.basis -eq 'tokens') { '미지원 모델 포함: 가중치 없이 토큰 수로 비교' } else { '표준 크레딧 요율 기준 · 속도 모드 미반영' }
    if ([double]$data.other_percent -gt 0) { $footer += (' · 나머지 {0:0.0}%' -f [double]$data.other_percent) }
    $footer += "`n이 PC에서 관측된 기록 기준 · 실제 할당량 배분과 다름"
    if ($data.partial) { $footer += "`n기록이 많아 일부만 집계됨" }
    $script:usageHover.Popup.SetContent("최근 $minutes" + "분 · $basis", $footer,
        [string[]]@($rows | ForEach-Object { [string]$_.title }),
        [string[]]@($rows | ForEach-Object { [string]$_.models }),
        [double[]]@($rows | ForEach-Object { [double]$_.percent }))
}
function Test-AttentionLease([bool]$Refresh = $true) {
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    if ($Refresh) {
        try {
            $lease = Get-Content -LiteralPath ([string]$request.while_active) -Raw -Encoding UTF8 | ConvertFrom-Json
            $script:leaseActive = $lease.active -and $lease.token -eq $request.lease_token
            $script:leaseExpires = [double]$lease.valid_until
            $script:contributors = $lease.contributors
            Update-UsagePopup
        } catch {
            # Transient read conflicts use only the last valid, bounded heartbeat.
        }
    }
    return $script:leaseActive -and $script:leaseExpires -gt $now
}
if ($persistent -and -not (Test-AttentionLease)) {
    @{state='closed'; persistent=$true; reason='inactive or expired lease'} | ConvertTo-Json |
        Set-Content -LiteralPath $statusPath -Encoding UTF8
    return
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$edgeColor = if ($isAlarm) { [System.Drawing.Color]::FromArgb(255, 40, 40) } else { [System.Drawing.Color]::FromArgb(0, 180, 255) }
Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing -Path (Join-Path $PSScriptRoot 'attention_ui.cs')
[FeDisplay]::EnableDpiAwareness()
$edges = New-Object 'System.Collections.Generic.List[System.Windows.Forms.Form]'
$icon = New-Object System.Windows.Forms.NotifyIcon
$timer = New-Object System.Windows.Forms.Timer
$leaseChanges = $null
$titleHover = $null
try {
    foreach ($screen in [System.Windows.Forms.Screen]::AllScreens) {
        $b = $screen.Bounds
        $thickness = 5
        $rectangles = @(
            [System.Drawing.Rectangle]::new($b.Left, $b.Top, $b.Width, $thickness),
            [System.Drawing.Rectangle]::new($b.Left, $b.Bottom-$thickness, $b.Width, $thickness),
            [System.Drawing.Rectangle]::new($b.Left, $b.Top+$thickness, $thickness, $b.Height-2*$thickness),
            [System.Drawing.Rectangle]::new($b.Right-$thickness, $b.Top+$thickness, $thickness, $b.Height-2*$thickness)
        )
        foreach ($rect in $rectangles) {
            $edge = New-Object FeAttentionEdge -ArgumentList $rect,$edgeColor
            $edges.Add($edge)
            $edge.Show()
        }
    }
    $icon.Icon = if ($isAlarm) { [System.Drawing.SystemIcons]::Warning } else { [System.Drawing.SystemIcons]::Information }
    $icon.Text = if ($isAlarm) { 'Codex usage alert' } else { 'Chat attention' }
    $icon.Visible = $true
    $icon.BalloonTipTitle = [string]$request.title
    $icon.BalloonTipText = [string]$request.message
    $icon.BalloonTipIcon = if ($isAlarm) { [System.Windows.Forms.ToolTipIcon]::Warning } else { [System.Windows.Forms.ToolTipIcon]::Info }
    $icon.ShowBalloonTip($duration * 1000)
    if ($isAlarm) { [System.Media.SystemSounds]::Exclamation.Play() } else { [System.Media.SystemSounds]::Asterisk.Play() }
    @{state='shown'; edgeCount=$edges.Count; durationSeconds=$edgeDuration; persistent=$persistent; color=$edgeColor.Name; notification='Windows NotifyIcon balloon requested'} |
        ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
    if ($persistent) {
        $leaseChanges = New-Object FeLeaseChanges -ArgumentList ([string]$request.while_active)
        $script:usageHover = New-Object FeUsageHover
        Update-UsagePopup
        $script:usageHover.Start()
        $script:leaseClock = [System.Diagnostics.Stopwatch]::StartNew()
        $script:nextLeaseFallback = 5000
        $timer.Interval = 1000
        $timer.Add_Tick({
            $refresh = $leaseChanges.TakeDirty() -or $script:leaseClock.ElapsedMilliseconds -ge $script:nextLeaseFallback
            if ($refresh) { $script:nextLeaseFallback = $script:leaseClock.ElapsedMilliseconds + 5000 }
            if (-not (Test-AttentionLease $refresh)) { $timer.Stop(); [System.Windows.Forms.Application]::ExitThread() }
        })
    } else {
        if (-not $isAlarm -and -not [string]::IsNullOrWhiteSpace([string]$request.chat_title)) {
            $titleHover = New-Object FeTitleHover -ArgumentList ([string]$request.chat_title)
            $titleHover.Start()
        }
        $timer.Interval = $duration * 1000
        $timer.Add_Tick({ $timer.Stop(); [System.Windows.Forms.Application]::ExitThread() })
    }
    $timer.Start()
    [System.Windows.Forms.Application]::Run()
} finally {
    $timer.Dispose()
    if ($null -ne $leaseChanges) { $leaseChanges.Dispose() }
    if ($null -ne $script:usageHover) { $script:usageHover.Dispose() }
    if ($null -ne $titleHover) { $titleHover.Dispose() }
    $icon.Visible = $false
    $icon.Dispose()
    foreach ($edge in $edges) { $edge.Close(); $edge.Dispose() }
    @{state='closed'; durationSeconds=$edgeDuration; persistent=$persistent; color=$edgeColor.Name; edgeCount=$edges.Count} | ConvertTo-Json |
        Set-Content -LiteralPath $statusPath -Encoding UTF8
}
