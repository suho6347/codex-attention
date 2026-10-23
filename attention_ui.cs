using System;
using System.Drawing;
using System.IO;
using System.Threading;
using System.Windows.Forms;

public class FeAttentionEdge : Form {
    protected override bool ShowWithoutActivation { get { return true; } }
    protected override CreateParams CreateParams {
        get {
            var cp = base.CreateParams;
            cp.ExStyle |= 0x08000000 | 0x00000020 | 0x00000080;
            return cp;
        }
    }
    public FeAttentionEdge(Rectangle bounds, Color color) {
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false;
        StartPosition = FormStartPosition.Manual;
        Bounds = bounds;
        BackColor = color;
        Opacity = 0.85;
        TopMost = true;
    }
}

public sealed class FeUsagePopup : FeAttentionEdge {
    private string heading = "", footer = "";
    private string[] titles = new string[0], models = new string[0];
    private double[] shares = new double[0];
    private readonly Font titleFont = new Font("Malgun Gothic", 11, FontStyle.Bold);
    private readonly Font bodyFont = new Font("Malgun Gothic", 9);
    private readonly Font smallFont = new Font("Malgun Gothic", 8);
    public FeUsagePopup() : base(new Rectangle(0, 0, 470, 160), Color.FromArgb(24, 28, 35)) {
        DoubleBuffered = true;
        Opacity = 0.97;
    }
    public void SetContent(string newHeading, string newFooter, string[] newTitles,
                           string[] newModels, double[] newShares) {
        heading = newHeading; footer = newFooter;
        titles = newTitles; models = newModels; shares = newShares;
        ClientSize = new Size(470, 126 + Math.Max(1, titles.Length) * 62);
        Invalidate();
    }
    private void TextLine(Graphics g, string text, Font font, Color color, RectangleF bounds) {
        using (var brush = new SolidBrush(color))
        using (var format = new StringFormat()) {
            format.Trimming = StringTrimming.EllipsisCharacter;
            format.FormatFlags = StringFormatFlags.NoWrap;
            g.DrawString(text, font, brush, bounds, format);
        }
    }
    protected override void OnPaint(PaintEventArgs e) {
        base.OnPaint(e);
        var g = e.Graphics;
        using (var pen = new Pen(Color.FromArgb(238, 77, 88), 2))
            g.DrawRectangle(pen, 1, 1, ClientSize.Width - 3, ClientSize.Height - 3);
        TextLine(g, heading, titleFont, Color.White, new RectangleF(18, 15, 434, 27));
        if (titles.Length == 0)
            TextLine(g, "최근 로컬 사용 기록이 없습니다.", bodyFont, Color.LightGray, new RectangleF(18, 59, 430, 24));
        for (int i = 0; i < titles.Length; i++) {
            int y = 51 + i * 62;
            TextLine(g, (i + 1) + ". " + titles[i], bodyFont, Color.White, new RectangleF(18, y, 360, 22));
            TextLine(g, shares[i].ToString("0.0") + "%", titleFont, Color.FromArgb(255, 131, 139), new RectangleF(384, y - 1, 70, 24));
            TextLine(g, models[i], smallFont, Color.FromArgb(163, 174, 192), new RectangleF(18, y + 22, 430, 18));
            using (var track = new SolidBrush(Color.FromArgb(47, 53, 65)))
            using (var bar = new SolidBrush(Color.FromArgb(222, 70, 85))) {
                g.FillRectangle(track, 18, y + 44, 432, 3);
                g.FillRectangle(bar, 18, y + 44, (float)(432 * Math.Max(0, Math.Min(100, shares[i])) / 100), 3);
            }
        }
        using (var brush = new SolidBrush(Color.FromArgb(173, 183, 199)))
            g.DrawString(footer, smallFont, brush, new RectangleF(18, ClientSize.Height - 66, 434, 58));
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
        if (enteredAt == 0) enteredAt = clock.ElapsedMilliseconds;
        if (clock.ElapsedMilliseconds - enteredAt < 250) return;
        if (!Popup.Visible) {
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
    private readonly Font captionFont = new Font("Malgun Gothic", 17, FontStyle.Bold);
    public FeTitleBanner(string title) : base(new Rectangle(0, 0, 720, 64), Color.FromArgb(24, 28, 35)) {
        caption = title;
        DoubleBuffered = true;
        Opacity = 0.9;
    }
    public void Place(Rectangle work) {
        int width = Math.Max(240, Math.Min(work.Width - 24,
            TextRenderer.MeasureText(caption, captionFont).Width + 64));
        Bounds = new Rectangle(work.Left + (work.Width - width) / 2, work.Top + 12, width, 64);
    }
    protected override void OnPaint(PaintEventArgs e) {
        base.OnPaint(e);
        using (var pen = new Pen(Color.FromArgb(0, 180, 255), 2))
            e.Graphics.DrawRectangle(pen, 1, 1, ClientSize.Width - 3, ClientSize.Height - 3);
        TextRenderer.DrawText(e.Graphics, caption, captionFont, new Rectangle(22, 0, Width - 44, Height),
            Color.White, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter |
                         TextFormatFlags.EndEllipsis | TextFormatFlags.SingleLine);
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
            Banner.Place(edgeScreen.WorkingArea);
            if (!Banner.Visible) Banner.Show();
        };
    }
    public void Start() { timer.Start(); }
    public void Dispose() { timer.Dispose(); Banner.Close(); Banner.Dispose(); }
}
