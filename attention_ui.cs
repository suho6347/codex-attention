using System;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

public static class FeDisplay {
    [DllImport("user32.dll")] private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr value);
    [DllImport("user32.dll")] private static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] private static extern uint GetDpiForWindow(IntPtr window);
    [DllImport("user32.dll")] private static extern bool SetLayeredWindowAttributes(IntPtr window, uint key, byte alpha, uint flags);
    public static void EnableDpiAwareness() {
        try {
            if (SetThreadDpiAwarenessContext(new IntPtr(-4)) == IntPtr.Zero)
                SetThreadDpiAwarenessContext(new IntPtr(-3));
        } catch (EntryPointNotFoundException) { SetProcessDPIAware(); }
    }
    public static int DpiFor(Form form) {
        try { uint dpi = GetDpiForWindow(form.Handle); if (dpi > 0) return (int)dpi; }
        catch (EntryPointNotFoundException) { }
        using (var graphics = form.CreateGraphics()) return (int)graphics.DpiX;
    }
    public static void ApplyOpacity(Form form) {
        // Keep layered click-through hit testing even when text is fully opaque.
        SetLayeredWindowAttributes(form.Handle, 0, (byte)Math.Round(form.Opacity * 255), 2);
    }
}

public class FeAttentionEdge : Form {
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams {
        get {
            var cp = base.CreateParams;
            cp.ExStyle |= 0x08000000 | 0x00000020 | 0x00000080 | 0x00080000;
            return cp;
        }
    }
    public FeAttentionEdge(Rectangle bounds, Color color) {
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false;
        StartPosition = FormStartPosition.Manual;
        AutoScaleMode = AutoScaleMode.None;
        Bounds = bounds;
        BackColor = color;
        Opacity = 0.85;
        TopMost = true;
    }
    protected override void OnHandleCreated(EventArgs e) {
        base.OnHandleCreated(e);
        FeDisplay.ApplyOpacity(this);
    }
}

public sealed class FeUsagePopup : FeAttentionEdge {
    private string heading = "", footer = "";
    private string[] titles = new string[0], models = new string[0];
    private double[] shares = new double[0];
    private Font titleFont, bodyFont, smallFont;
    private float scale;
    private int logicalHeight;
    public FeUsagePopup() : base(new Rectangle(0, 0, 470, 160), Color.FromArgb(24, 28, 35)) {
        DoubleBuffered = true;
        Opacity = 1;
        SetDpi(96);
    }
    public void SetContent(string newHeading, string newFooter, string[] newTitles,
                           string[] newModels, double[] newShares) {
        heading = newHeading; footer = newFooter;
        titles = newTitles; models = newModels; shares = newShares;
        ResizePanel();
        Invalidate();
    }
    public void SetDpi(int dpi) {
        float next = Math.Max(1, dpi / 96f);
        if (scale == next) return;
        scale = next;
        if (titleFont != null) { titleFont.Dispose(); bodyFont.Dispose(); smallFont.Dispose(); }
        titleFont = new Font("Malgun Gothic", 11 * 96f / 72 * scale, FontStyle.Bold, GraphicsUnit.Pixel);
        bodyFont = new Font("Malgun Gothic", 10 * 96f / 72 * scale, FontStyle.Regular, GraphicsUnit.Pixel);
        smallFont = new Font("Malgun Gothic", 9 * 96f / 72 * scale, FontStyle.Regular, GraphicsUnit.Pixel);
        ResizePanel();
        Invalidate();
    }
    public void Prepare(Rectangle work) {
        Location = work.Location;
        SetDpi(FeDisplay.DpiFor(this));
    }
    private Rectangle Box(float x, float y, float width, float height) {
        return Rectangle.Round(new RectangleF(x * scale, y * scale, width * scale, height * scale));
    }
    private void ResizePanel() {
        logicalHeight = 132 + Math.Max(1, titles.Length) * 66;
        ClientSize = new Size((int)Math.Round(500 * scale), (int)Math.Round(logicalHeight * scale));
    }
    private void TextLine(Graphics g, string text, Font font, Color color, RectangleF bounds) {
        TextRenderer.DrawText(g, text, font, Box(bounds.X, bounds.Y, bounds.Width, bounds.Height), color,
            TextFormatFlags.EndEllipsis | TextFormatFlags.SingleLine | TextFormatFlags.NoPrefix | TextFormatFlags.NoPadding);
    }
    protected override void OnPaint(PaintEventArgs e) {
        base.OnPaint(e);
        var g = e.Graphics;
        using (var pen = new Pen(Color.FromArgb(238, 77, 88), 2 * scale))
            g.DrawRectangle(pen, 1, 1, ClientSize.Width - 3, ClientSize.Height - 3);
        TextLine(g, heading, titleFont, Color.White, new RectangleF(18, 15, 464, 27));
        if (titles.Length == 0)
            TextLine(g, "최근 로컬 사용 기록이 없습니다.", bodyFont, Color.LightGray, new RectangleF(18, 59, 464, 24));
        for (int i = 0; i < titles.Length; i++) {
            int y = 51 + i * 66;
            TextLine(g, (i + 1) + ". " + titles[i], bodyFont, Color.White, new RectangleF(18, y, 384, 24));
            TextLine(g, shares[i].ToString("0.0") + "%", titleFont, Color.FromArgb(255, 145, 151), new RectangleF(410, y - 1, 72, 26));
            TextLine(g, models[i], smallFont, Color.FromArgb(190, 200, 216), new RectangleF(18, y + 24, 464, 21));
            using (var track = new SolidBrush(Color.FromArgb(47, 53, 65)))
            using (var bar = new SolidBrush(Color.FromArgb(222, 70, 85))) {
                g.FillRectangle(track, Box(18, y + 48, 464, 3));
                g.FillRectangle(bar, Box(18, y + 48, (float)(464 * Math.Max(0, Math.Min(100, shares[i])) / 100), 3));
            }
        }
        TextRenderer.DrawText(g, footer, smallFont, Box(18, logicalHeight - 68, 464, 62),
            Color.FromArgb(190, 200, 216), TextFormatFlags.WordBreak | TextFormatFlags.NoPrefix | TextFormatFlags.NoPadding);
    }
    protected override void Dispose(bool disposing) {
        if (disposing) { titleFont.Dispose(); bodyFont.Dispose(); smallFont.Dispose(); }
        base.Dispose(disposing);
    }
}

public sealed class FeUsageHover : IDisposable {
    public readonly FeUsagePopup Popup = new FeUsagePopup();
    private readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    private long enteredAt;
    private readonly System.Diagnostics.Stopwatch clock = System.Diagnostics.Stopwatch.StartNew();
    public FeUsageHover() {
        timer.Interval = 250; // Cursor sampling only while the red overlay exists.
        timer.Tick += delegate { Tick(); };
    }
    public static bool NearEdge(Point point, Rectangle bounds, int thickness) {
        return bounds.Contains(point) && (point.X < bounds.Left + thickness ||
            point.X >= bounds.Right - thickness || point.Y < bounds.Top + thickness ||
            point.Y >= bounds.Bottom - thickness);
    }
    public static Point PopupLocation(Point point, Rectangle work, Size size) {
        int x = Math.Max(work.Left + 10, Math.Min(point.X + 18, work.Right - size.Width - 10));
        int y = Math.Max(work.Top + 10, Math.Min(point.Y + 18, work.Bottom - size.Height - 10));
        return new Point(x, y);
    }
    public void Start() { timer.Start(); }
    private void Tick() {
        var point = Cursor.Position;
        if (Popup.Visible && Popup.Bounds.Contains(point)) return;
        Screen screen = null;
        foreach (var candidate in Screen.AllScreens)
            if (NearEdge(point, candidate.Bounds, 8)) { screen = candidate; break; }
        if (screen == null) {
            enteredAt = 0;
            if (Popup.Visible) Popup.Hide();
            return;
        }
        if (Popup.Visible && !screen.Bounds.IntersectsWith(Popup.Bounds)) {
            Popup.Hide();
            enteredAt = 0;
        }
        if (enteredAt == 0) enteredAt = clock.ElapsedMilliseconds;
        if (clock.ElapsedMilliseconds - enteredAt < 250) return;
        if (!Popup.Visible) {
            Popup.Prepare(screen.WorkingArea);
            Popup.Location = PopupLocation(point, screen.WorkingArea, Popup.Size);
            Popup.Show(); // WS_EX_NOACTIVATE and click-through preserve focus/input.
        }
    }
    public void Dispose() { timer.Dispose(); Popup.Close(); Popup.Dispose(); }
}

public sealed class FeLeaseChanges : IDisposable {
    private readonly FileSystemWatcher watcher;
    private int dirty = 1;
    public FeLeaseChanges(string path) {
        watcher = new FileSystemWatcher(Path.GetDirectoryName(path), Path.GetFileName(path));
        watcher.NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.Size;
        watcher.Changed += delegate { Interlocked.Exchange(ref dirty, 1); };
        watcher.Created += delegate { Interlocked.Exchange(ref dirty, 1); };
        watcher.Deleted += delegate { Interlocked.Exchange(ref dirty, 1); };
        watcher.Renamed += delegate { Interlocked.Exchange(ref dirty, 1); };
        watcher.Error += delegate { Interlocked.Exchange(ref dirty, 1); };
        watcher.EnableRaisingEvents = true;
    }
    public bool TakeDirty() { return Interlocked.Exchange(ref dirty, 0) != 0; }
    public void Dispose() { watcher.Dispose(); }
}

public sealed class FeTitleBanner : FeAttentionEdge {
    private readonly string caption;
    private Font captionFont;
    private float scale;
    public FeTitleBanner(string title) : base(new Rectangle(0, 0, 720, 64), Color.FromArgb(24, 28, 35)) {
        caption = title;
        DoubleBuffered = true;
        Opacity = 1;
        SetDpi(96);
    }
    public void SetDpi(int dpi) {
        float next = Math.Max(1, dpi / 96f);
        if (scale == next) return;
        scale = next;
        if (captionFont != null) captionFont.Dispose();
        captionFont = new Font("Malgun Gothic", 17 * 96f / 72 * scale, FontStyle.Bold, GraphicsUnit.Pixel);
        Invalidate();
    }
    public void Place(Rectangle work) {
        if (!Visible) { Location = work.Location; SetDpi(FeDisplay.DpiFor(this)); }
        int width = Math.Min(work.Width - 24, Math.Max((int)(240 * scale),
            TextRenderer.MeasureText(caption, captionFont).Width + (int)(64 * scale)));
        Bounds = new Rectangle(work.Left + (work.Width - width) / 2, work.Top + (int)(12 * scale), width, (int)(64 * scale));
    }
    protected override void OnPaint(PaintEventArgs e) {
        base.OnPaint(e);
        using (var pen = new Pen(Color.FromArgb(0, 180, 255), 2 * scale))
            e.Graphics.DrawRectangle(pen, 1, 1, ClientSize.Width - 3, ClientSize.Height - 3);
        int padding = (int)(22 * scale);
        TextRenderer.DrawText(e.Graphics, caption, captionFont, new Rectangle(padding, 0, Width - 2 * padding, Height),
            Color.White, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter |
                         TextFormatFlags.EndEllipsis | TextFormatFlags.SingleLine | TextFormatFlags.NoPrefix);
    }
    protected override void Dispose(bool disposing) {
        if (disposing) captionFont.Dispose();
        base.Dispose(disposing);
    }
}

public sealed class FeTitleHover : IDisposable {
    public readonly FeTitleBanner Banner;
    private readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    public FeTitleHover(string title) {
        Banner = new FeTitleBanner(title);
        timer.Interval = 250;
        timer.Tick += delegate {
            var point = Cursor.Position;
            if (Banner.Visible && Banner.Bounds.Contains(point)) return;
            Screen edgeScreen = null;
            foreach (var screen in Screen.AllScreens)
                if (FeUsageHover.NearEdge(point, screen.Bounds, 8)) { edgeScreen = screen; break; }
            if (edgeScreen == null) { if (Banner.Visible) Banner.Hide(); return; }
            if (!Banner.Visible || !edgeScreen.Bounds.IntersectsWith(Banner.Bounds)) {
                Banner.Hide();
                Banner.Place(edgeScreen.WorkingArea);
                Banner.Show();
            }
        };
    }
    public void Start() { timer.Start(); }
    public void Dispose() { timer.Dispose(); Banner.Close(); Banner.Dispose(); }
}
