using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Threading.Tasks;
using System.Windows.Forms;

internal static class Installer
{
    private const string Version = "2026.10.17.1";
    private const string CompleteMarker = ".installation-complete";

    [STAThread]
    private static void Main(string[] args)
    {
        Application.EnableVisualStyles();
        if (args.Length == 2 && args[0] == "--extract-only")
        {
            Extract(args[1], null);
            return;
        }
        string target = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs", "PDF2ZH", Version);
        var window = new Form { Text = "安装论文 PDF 翻译", Width = 460, Height = 185,
            FormBorderStyle = FormBorderStyle.FixedDialog, MaximizeBox = false,
            StartPosition = FormStartPosition.CenterScreen };
        var label = new Label { Left = 24, Top = 25, Width = 405, Height = 48,
            Text = "正在准备论文 PDF 翻译……\n首次安装会展开模型与字体，完成后自动打开。" };
        var progress = new ProgressBar { Left = 24, Top = 90, Width = 400, Height = 22 };
        window.Controls.Add(label);
        window.Controls.Add(progress);
        window.Shown += async (sender, e) =>
        {
            try
            {
                var updates = new Progress<int>(value => progress.Value = value);
                string executable = Path.Combine(target, "PDF论文翻译.exe");
                if (!File.Exists(Path.Combine(target, CompleteMarker)) || !File.Exists(executable))
                    await Task.Run(() => Extract(target, updates));
                CreateShortcut(executable);
                Process.Start(new ProcessStartInfo(executable) { WorkingDirectory = target, UseShellExecute = true });
                window.Close();
            }
            catch (Exception error)
            {
                MessageBox.Show("安装未完成：\n" + error.Message, "论文 PDF 翻译", MessageBoxButtons.OK, MessageBoxIcon.Error);
                window.Close();
            }
        };
        Application.Run(window);
    }

    private static void Extract(string target, IProgress<int> progress)
    {
        Directory.CreateDirectory(target);
        string marker = Path.Combine(target, CompleteMarker);
        if (File.Exists(marker)) File.Delete(marker);
        using (var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("PDF2ZH.Payload"))
        using (var archive = new ZipArchive(stream, ZipArchiveMode.Read))
        {
            int completed = 0;
            foreach (var entry in archive.Entries)
            {
                string destination = Path.Combine(target, entry.FullName);
                if (entry.FullName.EndsWith("/")) Directory.CreateDirectory(destination);
                else
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(destination));
                    entry.ExtractToFile(destination, true);
                }
                if (progress != null) progress.Report(++completed * 100 / archive.Entries.Count);
            }
        }
        File.WriteAllText(marker, Version);
    }

    private static void CreateShortcut(string executable)
    {
        Type shellType = Type.GetTypeFromProgID("WScript.Shell");
        dynamic shell = Activator.CreateInstance(shellType);
        string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        dynamic link = shell.CreateShortcut(Path.Combine(desktop, "PDF论文翻译.lnk"));
        link.TargetPath = executable;
        link.Arguments = "";
        link.WindowStyle = 1;
        link.WorkingDirectory = Path.GetDirectoryName(executable);
        link.Description = "论文 PDF 翻译：免费翻译、API 服务与原文对照阅读";
        link.IconLocation = executable + ",0";
        link.Save();
    }
}
