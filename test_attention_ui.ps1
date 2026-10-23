param([string]$PreviewPath)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'show-attention.ps1') -ValidateOnly
$bounds = [Drawing.Rectangle]::new(-1920, 0, 1920, 1080)
if (-not [FeUsageHover]::NearEdge([Drawing.Point]::new(-1920, 200), $bounds, 8)) { throw 'Left edge missed' }
if ([FeUsageHover]::NearEdge([Drawing.Point]::new(-1000, 500), $bounds, 8)) { throw 'Interior incorrectly matched' }
if ([FeUsageHover]::NearEdge([Drawing.Point]::new(0, 200), $bounds, 8)) { throw 'Other monitor incorrectly matched' }
$position = [FeUsageHover]::PopupLocation([Drawing.Point]::new(-1, 1079), $bounds, [Drawing.Size]::new(470, 436))
if ($position.X -lt $bounds.Left -or $position.X + 470 -gt $bounds.Right -or $position.Y + 436 -gt $bounds.Bottom) { throw 'Popup outside screen' }
$popup = New-Object FeUsagePopup
try {
    $popup.SetContent('최근 5분 · 소모 추정 비중', "표준 크레딧 요율 기준 · 속도 모드 미반영`n이 PC에서 관측된 기록 기준 · 실제 할당량 배분과 다름",
        [string[]]@('코드 작업 예시', '문서 작성 예시', '가벼운 질문 예시'),
        [string[]]@('gpt-6.1-sol', 'gpt-6.1-sol', 'gpt-6-luna'), [double[]]@(72.4, 23.1, 4.5))
    $property = $popup.GetType().GetProperty('CreateParams', [Reflection.BindingFlags]'Instance,NonPublic')
    $style = $property.GetValue($popup, $null).ExStyle
    if (($style -band 0x08000020) -ne 0x08000020) { throw 'Focus or click-through protection missing' }
    if ($PreviewPath) {
        $bitmap = [Drawing.Bitmap]::new($popup.Width, $popup.Height)
        try {
            $popup.DrawToBitmap($bitmap, $popup.ClientRectangle)
            $bitmap.Save([IO.Path]::GetFullPath($PreviewPath), [Drawing.Imaging.ImageFormat]::Png)
        } finally { $bitmap.Dispose() }
    }
    'UI checks passed: compilation, screen edges, popup placement, focus protection, synthetic rendering.'
} finally { $popup.Dispose() }
$banner = New-Object FeTitleBanner -ArgumentList '알림을 호출한 채팅 예시'
try {
    $banner.Place($bounds)
    if ($banner.Left -lt $bounds.Left -or $banner.Right -gt $bounds.Right) { throw 'Title banner outside screen' }
    $property = $banner.GetType().GetProperty('CreateParams', [Reflection.BindingFlags]'Instance,NonPublic')
    if (($property.GetValue($banner, $null).ExStyle -band 0x08000020) -ne 0x08000020) { throw 'Title focus protection missing' }
} finally { $banner.Dispose() }
