param([string]$PreviewPath)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'show-attention.ps1') -ValidateOnly
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class FeRenderChecks {
    [DllImport("user32.dll")] private static extern int GetWindowLongW(IntPtr window, int index);
    [DllImport("user32.dll")] private static extern bool GetLayeredWindowAttributes(IntPtr window, out uint key, out byte alpha, out uint flags);
    public static bool OpaqueAndClickThrough(IntPtr window) {
        int style = GetWindowLongW(window, -20);
        uint key, flags; byte alpha;
        return (style & 0x08080020) == 0x08080020 &&
            GetLayeredWindowAttributes(window, out key, out alpha, out flags) && alpha == 255;
    }
}
'@
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
    if ($popup.Opacity -ne 1 -or -not [FeRenderChecks]::OpaqueAndClickThrough($popup.Handle)) { throw 'Opaque text or live click-through protection missing' }
    foreach ($dpi in @(96, 120, 144, 192)) {
        $popup.SetDpi($dpi)
        if ($popup.ClientSize.Width -ne [Math]::Round(500 * $dpi / 96) -or $popup.ClientSize.Height -ne [Math]::Round(330 * $dpi / 96)) { throw "Incorrect layout at $dpi DPI" }
        if ($PreviewPath -and $dpi -eq 144) {
            $scaled = [Drawing.Bitmap]::new($popup.Width, $popup.Height)
            try {
                $popup.DrawToBitmap($scaled, $popup.ClientRectangle)
                $scaled.Save([IO.Path]::GetFullPath($PreviewPath).Replace('.png', '-150.png'), [Drawing.Imaging.ImageFormat]::Png)
            } finally { $scaled.Dispose() }
        }
    }
    $popup.SetDpi(96)
    if ($PreviewPath) {
        $bitmap = [Drawing.Bitmap]::new($popup.Width, $popup.Height)
        try {
            $popup.DrawToBitmap($bitmap, $popup.ClientRectangle)
            $bitmap.Save([IO.Path]::GetFullPath($PreviewPath), [Drawing.Imaging.ImageFormat]::Png)
        } finally { $bitmap.Dispose() }
    }
    'UI checks passed: compilation, screen edges, placement, opaque text, live click-through styles, and 100/125/150/200 percent DPI layouts.'
} finally { $popup.Dispose() }
$banner = New-Object FeTitleBanner -ArgumentList '알림을 호출한 채팅 예시'
try {
    $banner.Place($bounds)
    if ($banner.Left -lt $bounds.Left -or $banner.Right -gt $bounds.Right) { throw 'Title banner outside screen' }
    $property = $banner.GetType().GetProperty('CreateParams', [Reflection.BindingFlags]'Instance,NonPublic')
    if (($property.GetValue($banner, $null).ExStyle -band 0x08000020) -ne 0x08000020) { throw 'Title focus protection missing' }
    if ($banner.Opacity -ne 1 -or -not [FeRenderChecks]::OpaqueAndClickThrough($banner.Handle)) { throw 'Title opacity or click-through protection missing' }
} finally { $banner.Dispose() }
