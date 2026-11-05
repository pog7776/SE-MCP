using System;
using System.Diagnostics;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Threading;

namespace SEScreens
{
    // A web page, rendered by the SEScreens.Browser helper (Chromium off screen, one process per screen) into
    // shared memory. A reader thread turns new frames into texture frames at the screen's fps.
    // Its sound comes back through the same shared memory (see TakeAudio), not out of the PC's speakers.
    internal sealed class BrowserSource : IFrameSource, IAudioSource
    {
        // Same layout as SEScreens.Browser's FrameMap.
        private const int SequenceOffset = 0, AudioWrittenOffset = 8, PixelsOffset = 64;
        private const int AudioRate = 24000, AudioRingSamples = AudioRate * 2;
        // Behind by more than this (the game paused, a hitch): skip ahead rather than play late.
        private const int MaxAudioLagSamples = AudioRate / 2;

        public int Width { get; }
        public int Height { get; }
        public bool Ended => process.HasExited;
        public string Error { get; private set; }
        public int Frames { get; private set; }
        public string Url { get; private set; }

        private readonly SourceInfo info;
        private readonly Process process;
        private readonly MemoryMappedFile map;
        private readonly MemoryMappedViewAccessor view;
        private readonly int intervalMs;
        private readonly long audioRing;
        private long audioRead;
        private volatile bool stop;
        private byte[] latest;

        public BrowserSource(SourceInfo info, string url, int width, int height, int fps)
        {
            this.info = info;
            Width = width;
            Height = height;
            Url = url;
            intervalMs = 1000 / Math.Max(1, fps);

            string exe = HelperPath();
            if (!File.Exists(exe))
                throw new InvalidOperationException($"Browser helper not found: {exe}");
            string name = "SEScreens_" + Guid.NewGuid().ToString("N");
            audioRing = PixelsOffset + (long)width * height * 4;
            map = MemoryMappedFile.CreateNew(name, audioRing + AudioRingSamples * 2);
            view = map.CreateViewAccessor();

            process = new Process
            {
                StartInfo = new ProcessStartInfo(exe,
                    $"--map {name} --width {width} --height {height} --fps {fps} --parent {Process.GetCurrentProcess().Id} --url \"{url}\"")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardInput = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    WorkingDirectory = Path.GetDirectoryName(exe),
                },
                EnableRaisingEvents = true,
            };
            process.OutputDataReceived += (_, e) => OnEvent(e.Data);
            process.ErrorDataReceived += (_, e) =>
            {
                if (!string.IsNullOrWhiteSpace(e.Data))
                    Error = e.Data;
            };
            process.Start();
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            new Thread(Read) { IsBackground = true, Name = "SEScreens browser frames" }.Start();
        }

        // %AppData%\SpaceEngineers\SEScreens\browser, where the helper's build deploys; SESCREENS_BROWSER overrides.
        public static string HelperPath()
        {
            string path = Environment.GetEnvironmentVariable("SESCREENS_BROWSER");
            if (!string.IsNullOrEmpty(path))
                return path;
            string appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            return Path.Combine(appData, "SpaceEngineers", "SEScreens", "browser", "SEScreens.Browser.exe");
        }

        public byte[] TakeLatest() => Interlocked.Exchange(ref latest, null);

        public byte[] TakeAudio()
        {
            if (stop)
                return null;
            long written = view.ReadInt64(AudioWrittenOffset);
            if (written - audioRead > MaxAudioLagSamples)
                audioRead = written - AudioRate / 10;
            int count = (int)(written - audioRead);
            if (count <= 0)
                return null;
            var pcm = new byte[count * 2];
            long start = audioRead % AudioRingSamples;
            // The ring may wrap inside this read.
            int first = (int)Math.Min(count, AudioRingSamples - start);
            view.ReadArray(audioRing + start * 2, pcm, 0, first * 2);
            if (first < count)
                view.ReadArray(audioRing, pcm, first * 2, (count - first) * 2);
            audioRead = written;
            return pcm;
        }

        public void Navigate(string url) => Send("navigate " + url);

        // Raw helper commands (move/down/up/wheel/text); see SEScreens.Browser.Program.
        public void Send(string command)
        {
            try
            {
                if (!process.HasExited)
                    process.StandardInput.WriteLine(command.Replace('\n', ' '));
            }
            catch (IOException)
            {
                // The helper is gone; Ended reports it.
            }
        }

        public void Dispose()
        {
            stop = true;
            Send("quit");
            new Thread(() =>
            {
                if (!process.WaitForExit(3000))
                {
                    try
                    {
                        process.Kill();
                    }
                    catch (InvalidOperationException)
                    {
                        // Exited meanwhile.
                    }
                }
                view.Dispose();
                map.Dispose();
                process.Dispose();
            }) { IsBackground = true }.Start();
        }

        private void OnEvent(string line)
        {
            if (string.IsNullOrEmpty(line))
                return;
            if (line.StartsWith("loaded ", StringComparison.Ordinal))
            {
                Url = line.Substring("loaded ".Length);
                info.Detail = Url;
                Error = null;
            }
            else if (line.StartsWith("error ", StringComparison.Ordinal))
            {
                Error = line.Substring("error ".Length);
            }
        }

        private void Read()
        {
            long last = 0;
            int pixelBytes = Width * Height * 4;
            try
            {
                while (!stop && !process.HasExited)
                {
                    long seq = view.ReadInt64(SequenceOffset);
                    if (seq != last && (seq & 1) == 0)
                    {
                        var frame = new byte[FrameBuffer.Bytes(Width, Height)];
                        view.ReadArray(PixelsOffset, frame, 0, pixelBytes);
                        // A paint landed mid-copy: drop it, the next one is coming.
                        if (view.ReadInt64(SequenceOffset) == seq)
                        {
                            for (int i = 0; i < pixelBytes; i += 4)
                            {
                                byte blue = frame[i];
                                frame[i] = frame[i + 2];
                                frame[i + 2] = blue;
                                frame[i + 3] = 0; // metalness: keep the screen glowing
                            }
                            Interlocked.Exchange(ref latest, frame);
                            Frames++;
                            last = seq;
                        }
                    }
                    Thread.Sleep(intervalMs);
                }
            }
            catch (Exception e)
            {
                Error = e.Message;
            }
        }
    }
}
