using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Threading;
using CefSharp;
using CefSharp.OffScreen;

namespace SEScreens.Browser
{
    // One off-screen Chromium page for one SEScreens screen.
    //
    //   SEScreens.Browser.exe --map <name> --width W --height H --fps F --url URL --parent PID [--cache DIR]
    //
    // Frames and sound: SEScreens creates the shared memory <name> (FrameMap layout below); each paint is copied in as BGRA,
    // guarded by a sequence number that is odd while a copy is in progress.
    // Commands, one per line on stdin: navigate URL | move X Y | down X Y [left|right|middle] | up X Y [...] |
    // wheel X Y DY | text STRING | eval SCRIPT | quit. Events, one per line on stdout: ready | loaded URL | error MESSAGE |
    // console MESSAGE (the page's console.log) | audio ... (stream state).
    // Exits when told to, when stdin closes, or when the parent (the game) exits.
    internal static class Program
    {
        private static ChromiumWebBrowser browser;
        private static MemoryMappedFile map;
        private static MemoryMappedViewAccessor view;
        private static int width, height;
        private static readonly object writeLock = new object();

        private static int Main(string[] args)
        {
            var a = new Args(args);
            width = a.Int("width", 1024);
            height = a.Int("height", 1024);
            int fps = a.Int("fps", 30);
            string url = a.String("url", "about:blank");
            // --audio system: no audio handler, Chromium plays to the Windows default device (for testing).
            string audio = a.String("audio", "capture");
            string cache = a.String("cache", Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "SEScreens", "cef-cache"));

            map = MemoryMappedFile.OpenExisting(a.String("map", null));
            view = map.CreateViewAccessor();
            WatchParent(a.Int("parent", 0));

            var settings = new CefSettings
            {
                CachePath = cache,
                LogSeverity = LogSeverity.Warning,
                LogFile = Path.Combine(cache, "cef.log"),
            };
            settings.SetOffScreenRenderingBestPerformanceArgs();
            // Screens play video without anyone clicking first.
            settings.CefCommandLineArgs["autoplay-policy"] = "no-user-gesture-required";
            // CefSharp.OffScreen's defaults include --mute-audio, which silences everything, the audio handler included.
            settings.CefCommandLineArgs.Remove("mute-audio");
            if (!Cef.Initialize(settings, performDependencyCheck: true, browserProcessHandler: null))
            {
                Send("error CEF failed to start");
                return 1;
            }

            // Created by hand once the handlers are set: CEF reads the audio handler when the browser is created.
            var browserSettings = new BrowserSettings { WindowlessFrameRate = fps };
            browser = new ChromiumWebBrowser(url, browserSettings, automaticallyCreateBrowser: false);
            browser.Size = new System.Drawing.Size(width, height);
            browser.Paint += OnPaint;
            if (audio != "system")
                browser.AudioHandler = new AudioToRing(view, FrameMap.AudioRing(width, height), Send);
            browser.LoadingStateChanged += (_, e) =>
            {
                if (e.IsLoading)
                    return;
                // CEF can mute a whole browser apart from the page; make sure ours isn't.
                var host = browser.GetBrowserHost();
                if (host.IsAudioMuted)
                {
                    Send("audio was muted by CEF; unmuting");
                    host.SetAudioMuted(false);
                }
                Send("loaded " + browser.Address);
            };
            browser.LoadError += (_, e) =>
            {
                if (e.ErrorCode != CefErrorCode.Aborted)
                    Send($"error {e.ErrorCode} {e.FailedUrl}");
            };
            browser.ConsoleMessage += (_, e) => Send("console " + e.Message);
            browser.CreateBrowser(null, browserSettings);
            Send("ready");

            string line;
            // UTF-8, skipping a byte-order mark some parents write first (the console default is the OEM code page).
            var input = new StreamReader(Console.OpenStandardInput(), new System.Text.UTF8Encoding(false), detectEncodingFromByteOrderMarks: true);
            while ((line = input.ReadLine()) != null)
            {
                try
                {
                    if (!Command(line.Trim()))
                        break;
                }
                catch (Exception e)
                {
                    Send("error " + e.Message);
                }
            }

            browser.Dispose();
            Cef.Shutdown();
            return 0;
        }

        // False on quit.
        private static bool Command(string line)
        {
            if (line.Length == 0)
                return true;
            string[] parts = line.Split(' ');
            var host = browser.IsBrowserInitialized ? browser.GetBrowserHost() : null;
            switch (parts[0])
            {
                case "quit":
                    return false;
                case "navigate":
                    browser.LoadUrl(line.Substring("navigate ".Length).Trim());
                    break;
                case "move":
                    host?.SendMouseMoveEvent(Mouse(parts), false);
                    break;
                case "down":
                case "up":
                    host?.SendMouseClickEvent(Mouse(parts), Button(parts), parts[0] == "up", 1);
                    break;
                case "wheel":
                    host?.SendMouseWheelEvent(Mouse(parts), 0, int.Parse(parts[3], CultureInfo.InvariantCulture));
                    break;
                case "audio":
                    Send($"audio muted={host?.IsAudioMuted}");
                    break;
                case "eval":
                    // Diagnostics: runs one line of script in the page; the result comes back as "eval RESULT".
                    browser.EvaluateScriptAsync(line.Substring("eval ".Length)).ContinueWith(t =>
                        Send("eval " + (t.Result.Success ? t.Result.Result?.ToString() ?? "null" : "failed " + t.Result.Message)));
                    break;
                case "text":
                    foreach (char c in line.Substring("text ".Length))
                        host?.SendKeyEvent(new KeyEvent { Type = KeyEventType.Char, WindowsKeyCode = c, Modifiers = CefEventFlags.None });
                    break;
                default:
                    Send("error unknown command " + parts[0]);
                    break;
            }
            return true;
        }

        private static MouseEvent Mouse(string[] parts) =>
            new MouseEvent(int.Parse(parts[1], CultureInfo.InvariantCulture), int.Parse(parts[2], CultureInfo.InvariantCulture), CefEventFlags.None);

        private static MouseButtonType Button(string[] parts) =>
            parts.Length > 3 && parts[3] == "right" ? MouseButtonType.Right
            : parts.Length > 3 && parts[3] == "middle" ? MouseButtonType.Middle
            : MouseButtonType.Left;

        // CEF's UI thread. Popups (open dropdowns) aren't composited yet.
        private static unsafe void OnPaint(object sender, OnPaintEventArgs e)
        {
            if (e.IsPopup || e.Width != width || e.Height != height)
                return;
            lock (writeLock)
            {
                long seq = view.ReadInt64(FrameMap.Sequence);
                view.Write(FrameMap.Sequence, seq + 1); // odd: copying
                byte* ptr = null;
                view.SafeMemoryMappedViewHandle.AcquirePointer(ref ptr);
                try
                {
                    long bytes = (long)width * height * 4;
                    Buffer.MemoryCopy((void*)e.BufferHandle, ptr + view.PointerOffset + FrameMap.Pixels, bytes, bytes);
                }
                finally
                {
                    view.SafeMemoryMappedViewHandle.ReleasePointer();
                }
                view.Write(FrameMap.Sequence, seq + 2);
            }
        }

        private static void WatchParent(int pid)
        {
            if (pid == 0)
                return;
            var thread = new Thread(() =>
            {
                try
                {
                    Process.GetProcessById(pid).WaitForExit();
                }
                catch (ArgumentException)
                {
                    // Already gone.
                }
                Environment.Exit(0);
            }) { IsBackground = true };
            thread.Start();
        }

        private static void Send(string line)
        {
            lock (Console.Out)
            {
                Console.Out.WriteLine(line.Replace('\n', ' '));
                Console.Out.Flush();
            }
        }

        private sealed class Args
        {
            private readonly string[] args;

            public Args(string[] args) => this.args = args;

            public string String(string name, string fallback)
            {
                int i = Array.IndexOf(args, "--" + name);
                return i >= 0 && i + 1 < args.Length ? args[i + 1] : fallback;
            }

            public int Int(string name, int fallback) =>
                int.TryParse(String(name, null), NumberStyles.Integer, CultureInfo.InvariantCulture, out int v) ? v : fallback;
        }
    }

    // Shared memory layout; SEScreens.BrowserSource has the same numbers.
    internal static class FrameMap
    {
        public const int Sequence = 0;      // int64, odd while a frame is being copied in
        public const int AudioWritten = 8;  // int64, samples written to the audio ring so far
        public const int Pixels = 64;       // width * height * 4 bytes, BGRA, top row first
        // After the pixels: a ring of AudioRingSamples int16 samples, 24 kHz mono (the game's streamed-voice format).
        public const int AudioRate = 24000;
        public const int AudioRingSamples = AudioRate * 2;

        public static long AudioRing(int width, int height) => Pixels + (long)width * height * 4;
    }

    // The page's sound comes here instead of the PC's speakers, already in the format the game plays, and goes
    // into the shared ring for SEScreens to play from the screen.
    internal sealed class AudioToRing : IAudioHandler
    {
        private readonly MemoryMappedViewAccessor view;
        private readonly long ring;
        private readonly Action<string> report;
        private int channels = 1;
        private long written;

        public AudioToRing(MemoryMappedViewAccessor view, long ring, Action<string> report)
        {
            this.view = view;
            this.ring = ring;
            this.report = report;
        }

        public bool GetAudioParameters(IWebBrowser chromiumWebBrowser, IBrowser browser, ref CefSharp.Structs.AudioParameters parameters)
        {
            parameters.SampleRate = FrameMap.AudioRate;
            parameters.ChannelLayout = CefSharp.Enums.ChannelLayout.LayoutMono;
            parameters.FramesPerBuffer = FrameMap.AudioRate / 50; // 20 ms packets
            report("audio parameters asked");
            return true;
        }

        public void OnAudioStreamStarted(IWebBrowser chromiumWebBrowser, IBrowser browser, CefSharp.Structs.AudioParameters parameters, int channels)
        {
            this.channels = Math.Max(1, channels);
            report($"audio started {parameters.SampleRate} Hz, {channels} channels");
        }

        // data: one float* per channel, planar, -1..1. Mixed down to mono int16.
        public unsafe void OnAudioStreamPacket(IWebBrowser chromiumWebBrowser, IBrowser browser, IntPtr data, int noOfFrames, long pts)
        {
            float** planes = (float**)data;
            for (int i = 0; i < noOfFrames; i++)
            {
                float sum = 0;
                for (int c = 0; c < channels; c++)
                    sum += planes[c][i];
                float s = Math.Clamp(sum / channels, -1f, 1f);
                view.Write(ring + (written % FrameMap.AudioRingSamples) * 2, (short)(s * 32767));
                written++;
            }
            view.Write(FrameMap.AudioWritten, written);
        }

        public void OnAudioStreamStopped(IWebBrowser chromiumWebBrowser, IBrowser browser)
        {
        }

        public void OnAudioStreamError(IWebBrowser chromiumWebBrowser, IBrowser browser, string errorMessage)
        {
            report("error audio: " + errorMessage);
        }

        public void Dispose()
        {
        }
    }
}
