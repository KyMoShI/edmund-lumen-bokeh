using System;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;

internal static class Setup
{
    internal const string Product = "伯爵大光圈模拟";
    internal const string Exe = Product + ".exe";
    internal const string Version = "v1.2.5 · 现代主义";

    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length == 3 && args[0] == "--install-to")
        {
            try { Install(args[1], delegate { }); File.WriteAllText(args[2], "ok"); return 0; }
            catch (Exception ex) { File.WriteAllText(args[2], ex.ToString()); return 1; }
        }
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(true);
        if (args.Length == 2 && args[0] == "--preview")
        {
            using (var window = new SetupWindow())
            {
                window.ShowInTaskbar = false;
                window.StartPosition = FormStartPosition.Manual;
                window.Location = new Point(-20000,-20000);
                window.Show();
                Application.DoEvents();
                using (var image = new Bitmap(window.Width, window.Height))
                {
                    window.DrawToBitmap(image,new Rectangle(Point.Empty,window.Size));
                    image.Save(args[1],System.Drawing.Imaging.ImageFormat.Png);
                }
            }
            return 0;
        }
        Application.Run(new SetupWindow());
        return 0;
    }

    internal static string Install(string target, Action<int> progress)
    {
        if (string.IsNullOrWhiteSpace(target)) throw new IOException("请选择安装目录。");
        string path = Path.GetFullPath(target.Trim()).TrimEnd(Path.DirectorySeparatorChar);
        string parent = Path.GetDirectoryName(path);
        if (string.IsNullOrEmpty(parent)) throw new IOException("请选择磁盘中的子文件夹。");
        if (File.Exists(path) || (Directory.Exists(path) &&
            (!IsEmpty(path) || (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0)))
            throw new IOException("安装目录已有文件。请选择一个新目录或空文件夹。");
        Directory.CreateDirectory(parent);
        string staging = Path.Combine(parent, ".baoyue-install-" + Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(staging);
            string prefix = staging + Path.DirectorySeparatorChar;
            using (var file = File.OpenRead(Application.ExecutablePath))
            using (var zip = new ZipArchive(file, ZipArchiveMode.Read))
            {
                long total = 0, done = 0;
                foreach (var entry in zip.Entries) total += entry.Length;
                if (total == 0) throw new IOException("安装包内没有程序文件。");
                var drive = new DriveInfo(Path.GetPathRoot(parent));
                if (drive.IsReady && drive.AvailableFreeSpace < total + 16L * 1024 * 1024)
                    throw new IOException("所选磁盘空间不足。");
                var buffer = new byte[256 * 1024];
                int reported = -1;
                foreach (var entry in zip.Entries)
                {
                    var relative = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                    string output = Path.GetFullPath(Path.Combine(staging, relative));
                    if (!output.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                        throw new IOException("安装包包含无效路径。");
                    if (entry.Name.Length == 0) { Directory.CreateDirectory(output); continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(output));
                    long written = 0;
                    using (var input = entry.Open())
                    using (var destination = new FileStream(output, FileMode.CreateNew))
                    {
                        int n;
                        while ((n = input.Read(buffer, 0, buffer.Length)) > 0)
                        {
                            destination.Write(buffer, 0, n);
                            done += n; written += n;
                            int value = (int)(done * 99 / total);
                            if (value != reported) { progress(value); reported = value; }
                        }
                    }
                    if (written != entry.Length) throw new IOException("安装文件不完整。");
                }
            }
            if (!File.Exists(Path.Combine(staging, Exe)) || !Directory.Exists(Path.Combine(staging, "_internal")))
                throw new IOException("安装包缺少程序或运行库。");
            if (Directory.Exists(path)) Directory.Delete(path, false);
            Directory.Move(staging, path);
            progress(100);
            return Path.Combine(path, Exe);
        }
        finally
        {
            if (Directory.Exists(staging))
                try { Directory.Delete(staging, true); } catch (IOException) { } catch (UnauthorizedAccessException) { }
        }
    }

    private static bool IsEmpty(string path)
    {
        using (var items = Directory.EnumerateFileSystemEntries(path).GetEnumerator())
            return !items.MoveNext();
    }
}

internal sealed class SetupWindow : Form
{
    private static readonly Color Blue = ColorTranslator.FromHtml("#001EFF");
    private static readonly Color Red = ColorTranslator.FromHtml("#FF3D36");
    private static readonly Color Ink = ColorTranslator.FromHtml("#262626");
    private readonly TextBox directory = new TextBox();
    private readonly Label caption;
    private readonly Button browse, action, close;
    private readonly Panel track = new Panel();
    private readonly Panel bar = new Panel();
    private readonly PrivateFontCollection fonts = new PrivateFontCollection();
    private readonly IntPtr fontData;
    private readonly Image illustration;
    private bool busy;
    private string installed;

    internal SetupWindow()
    {
        Text = Setup.Product + " · 安装";
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        StartPosition = FormStartPosition.CenterScreen;
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MinimizeBox = false; MaximizeBox = false;
        AutoScaleMode = AutoScaleMode.Dpi;
        ClientSize = new Size(710, 424);
        BackColor = Color.White;
        DoubleBuffered = true;
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("sarasa.ttf"))
        {
            var bytes = new byte[(int)stream.Length];
            using (var reader = new BinaryReader(stream)) bytes = reader.ReadBytes(bytes.Length);
            fontData = Marshal.AllocCoTaskMem(bytes.Length);
            Marshal.Copy(bytes, 0, fontData, bytes.Length);
            fonts.AddMemoryFont(fontData, bytes.Length);
        }
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("illustration.png"))
        using (var original = Image.FromStream(stream))
            illustration = new Bitmap(original);
        AddLabel("伯爵大光圈", 54, 55, 370, 54, 32, Ink);
        AddLabel(Setup.Version, 58, 119, 335, 27, 13, Blue);
        AddLabel("安装目录", 58, 189, 300, 27, 13, Ink);
        directory.SetBounds(59, 223, 503, 29);
        directory.Font = new Font("Microsoft YaHei UI", 9.5f, GraphicsUnit.Point);
        directory.Text = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs", Setup.Product);
        Controls.Add(directory);
        browse = AddButton("浏览", 571, 219, 77, 39, false);
        browse.Click += delegate
        {
            using (var dialog = new FolderBrowserDialog())
            {
                dialog.Description = "选择空文件夹作为安装目录";
                if (Directory.Exists(directory.Text)) dialog.SelectedPath = directory.Text;
                if (dialog.ShowDialog(this) == DialogResult.OK) directory.Text = dialog.SelectedPath;
            }
        };
        caption = AddLabel("程序和运行库将安装到所选目录。", 58, 280, 593, 49, 11, Ink);
        track.SetBounds(58, 337, 590, 4);
        track.BackColor = Color.FromArgb(232, 235, 255);
        bar.BackColor = Blue; bar.Dock = DockStyle.Left; bar.Width = 0;
        track.Controls.Add(bar); Controls.Add(track);
        close = AddButton("退出", 421, 357, 97, 40, false);
        close.Click += delegate { Close(); };
        action = AddButton("安装", 529, 357, 119, 40, true);
        action.Click += InstallOrLaunch;
        AcceptButton = action; CancelButton = close;
        Shown += delegate { action.Select(); directory.Select(0,0); };
        FormClosing += delegate(object sender, FormClosingEventArgs e) { if (busy) e.Cancel = true; };
    }

    private Font UiFont(int size) { return new Font(fonts.Families[0], size, GraphicsUnit.Pixel); }

    private Label AddLabel(string value, int x, int y, int w, int h, int size, Color color)
    {
        var label = new Label { Text = value, BackColor = Color.Transparent, ForeColor = color,
            Font = UiFont(size == 32 ? 48 : size + 5), UseCompatibleTextRendering = true };
        label.SetBounds(x,y,w,h); Controls.Add(label); return label;
    }

    private Button AddButton(string value, int x, int y, int w, int h, bool primary)
    {
        var button = new Button { Text = value, FlatStyle = FlatStyle.Flat,
            BackColor = primary ? Ink : Color.White, ForeColor = primary ? Color.White : Ink,
            Font = UiFont(18), UseCompatibleTextRendering = true };
        button.FlatAppearance.BorderColor = primary ? Ink : Blue;
        button.SetBounds(x,y,w,h); Controls.Add(button); return button;
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        var g = e.Graphics;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        float scale = ClientSize.Width / 710f;
        g.ScaleTransform(scale, ClientSize.Height / 424f);
        using (var red = new SolidBrush(Red)) g.FillRectangle(red,0,0,10,424);
        using (var pen = new Pen(Red)) g.DrawLine(pen,39,43,650,43);
        g.DrawImage(illustration,new Rectangle(408,63,234,89));
        using (var shadow = new SolidBrush(Color.FromArgb(16,0,0,0)))
        using (var outline = new Pen(Blue))
        using (var face = Rounded(new RectangleF(39,169,630,235),15))
        using (var shade = Rounded(new RectangleF(41,172,630,235),15))
        {
            g.FillPath(shadow,shade); g.FillPath(Brushes.White,face); g.DrawPath(outline,face);
        }
        using (var blue = new SolidBrush(Blue))
        using (var red = new SolidBrush(Red))
        {
            g.FillEllipse(Brushes.Black,617,181,8,8);
            g.FillEllipse(blue,629,181,8,8);
            g.FillEllipse(red,641,181,8,8);
        }
    }

    private static GraphicsPath Rounded(RectangleF r, int radius)
    {
        var path = new GraphicsPath();
        int d = radius * 2;
        path.AddArc(r.Left,r.Top,d,d,180,90); path.AddArc(r.Right-d,r.Top,d,d,270,90);
        path.AddArc(r.Right-d,r.Bottom-d,d,d,0,90); path.AddArc(r.Left,r.Bottom-d,d,d,90,90);
        path.CloseFigure(); return path;
    }

    private async void InstallOrLaunch(object sender, EventArgs args)
    {
        if (installed != null)
        {
            try
            {
                Process.Start(new ProcessStartInfo(installed) { WorkingDirectory = Path.GetDirectoryName(installed) });
                Close();
            }
            catch (Exception ex) { caption.Text = "程序未能启动。\n" + ex.Message; }
            return;
        }
        busy = true;
        action.Enabled = browse.Enabled = directory.Enabled = close.Enabled = false;
        var selected = directory.Text;
        var feedback = new Progress<int>(value => { bar.Width = track.Width * value / 100; caption.Text = "正在安装 · " + value + "%"; });
        try
        {
            installed = await Task.Run(() => Setup.Install(selected, ((IProgress<int>)feedback).Report));
            caption.Text = "安装完成";
            bar.Width = track.Width;
            action.Text = "启动软件";
            close.Text = "关闭";
        }
        catch (Exception ex)
        {
            caption.Text = "安装失败。\n" + ex.Message;
            bar.Width = 0;
            browse.Enabled = directory.Enabled = true;
        }
        finally { busy = false; action.Enabled = close.Enabled = true; }
    }

    protected override void Dispose(bool disposing)
    {
        base.Dispose(disposing);
        if (disposing) { illustration.Dispose(); fonts.Dispose(); Marshal.FreeCoTaskMem(fontData); }
    }
}
