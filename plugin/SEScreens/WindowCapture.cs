using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace SEScreens
{
    // Copies one desktop window into RGBA frames sized for an LCD texture, on its own thread.
    // GDI for now: PrintWindow with PW_RENDERFULLCONTENT also gets most GPU-drawn windows (browsers, Electron),
    // and works while the window is covered. Windows.Graphics.Capture in a helper process is the faster upgrade.
    internal sealed class WindowCapture : IFrameSource
    {
        private const uint PwClientOnly = 1, PwRenderFullContent = 2;
        private const int Halftone = 4, SrcCopy = 0x00CC0020;

        public readonly IntPtr Window;
        public int Width { get; }
        public int Height { get; }
        public string Error { get; private set; }
        public bool Ended { get; private set; }
        public int Frames { get; private set; }

        private readonly int intervalMs;
        private readonly int byteCount;
        private readonly Thread thread;
        private volatile bool stop;
        private byte[] latest;

        // A window with IntPtr.Zero is the test card.
        public WindowCapture(IntPtr window, int width, int height, int fps)
        {
            Window = window;
            Width = width;
            Height = height;
            byteCount = FrameBuffer.Bytes(width, height);
            intervalMs = 1000 / Math.Max(1, fps);
            thread = new Thread(Run) { IsBackground = true, Name = "SEScreens window mirror" };
            thread.Start();
        }

        // The newest frame since the last call, or null. Each frame is a new array: the renderer keeps a reference
        // until it uploads it, so arrays are never reused.
        public byte[] TakeLatest() => Interlocked.Exchange(ref latest, null);

        public void Dispose()
        {
            stop = true;
        }

        private void Run()
        {
            if (Window == IntPtr.Zero)
            {
                RunTestCard();
                return;
            }
            IntPtr screen = GetDC(IntPtr.Zero);
            IntPtr srcDc = CreateCompatibleDC(screen), dstDc = CreateCompatibleDC(screen);
            IntPtr srcBitmap = IntPtr.Zero, oldSrc = IntPtr.Zero;
            IntPtr dstBitmap = CreateDib(dstDc, Width, Height, out IntPtr dstBits);
            IntPtr oldDst = SelectObject(dstDc, dstBitmap);
            SetStretchBltMode(dstDc, Halftone);
            int srcW = 0, srcH = 0;
            var watch = Stopwatch.StartNew();
            try
            {
                while (!stop)
                {
                    long started = watch.ElapsedMilliseconds;
                    if (!IsWindow(Window))
                    {
                        Ended = true;
                        return;
                    }
                    if (!IsIconic(Window) && GetClientRect(Window, out Rect client) && client.Right > 0 && client.Bottom > 0)
                    {
                        if (client.Right != srcW || client.Bottom != srcH)
                        {
                            if (srcBitmap != IntPtr.Zero)
                            {
                                SelectObject(srcDc, oldSrc);
                                DeleteObject(srcBitmap);
                            }
                            srcW = client.Right;
                            srcH = client.Bottom;
                            srcBitmap = CreateCompatibleBitmap(screen, srcW, srcH);
                            oldSrc = SelectObject(srcDc, srcBitmap);
                        }
                        if (PrintWindow(Window, srcDc, PwClientOnly | PwRenderFullContent))
                        {
                            Letterbox(srcW, srcH, out int x, out int y, out int w, out int h);
                            PatBlt(dstDc, 0, 0, Width, Height, 0x00000042); // BLACKNESS
                            StretchBlt(dstDc, x, y, w, h, srcDc, 0, 0, srcW, srcH, SrcCopy);
                            GdiFlush();
                            Interlocked.Exchange(ref latest, ToRgba(dstBits));
                            Frames++;
                        }
                    }
                    int wait = intervalMs - (int)(watch.ElapsedMilliseconds - started);
                    if (wait > 0)
                        Thread.Sleep(wait);
                }
            }
            catch (Exception e)
            {
                Error = e.Message;
                Ended = true;
            }
            finally
            {
                if (srcBitmap != IntPtr.Zero)
                {
                    SelectObject(srcDc, oldSrc);
                    DeleteObject(srcBitmap);
                }
                SelectObject(dstDc, oldDst);
                DeleteObject(dstBitmap);
                DeleteDC(srcDc);
                DeleteDC(dstDc);
                ReleaseDC(IntPtr.Zero, screen);
            }
        }

        // No window: bars of known levels for checking how screens show colour. Top half white, 75%, 50%, 25%
        // and black (sRGB bytes); bottom half red, green, blue, and a slow sweep so it's visibly live.
        private void RunTestCard()
        {
            byte[] top = { 255, 191, 128, 64, 0 };
            int tick = 0;
            while (!stop)
            {
                var frame = new byte[byteCount];
                for (int y = 0; y < Height; y++)
                {
                    for (int x = 0; x < Width; x++)
                    {
                        int i = (y * Width + x) * 4;
                        byte r, g, b;
                        if (y < Height / 2)
                        {
                            r = g = b = top[Math.Min(top.Length - 1, x * top.Length / Width)];
                        }
                        else
                        {
                            int bar = x * 4 / Width;
                            byte sweep = (byte)((tick * 4) & 0xFF);
                            r = bar == 0 ? (byte)255 : bar == 3 ? sweep : (byte)0;
                            g = bar == 1 ? (byte)255 : bar == 3 ? sweep : (byte)0;
                            b = bar == 2 ? (byte)255 : bar == 3 ? sweep : (byte)0;
                        }
                        frame[i] = r;
                        frame[i + 1] = g;
                        frame[i + 2] = b;
                        frame[i + 3] = 0;
                    }
                }
                Interlocked.Exchange(ref latest, frame);
                Frames++;
                tick++;
                Thread.Sleep(intervalMs);
            }
        }

        // Fit the window inside the texture, keeping its aspect ratio.
        private void Letterbox(int srcW, int srcH, out int x, out int y, out int w, out int h)
        {
            double scale = Math.Min((double)Width / srcW, (double)Height / srcH);
            w = Math.Max(1, (int)(srcW * scale));
            h = Math.Max(1, (int)(srcH * scale));
            x = (Width - w) / 2;
            y = (Height - h) / 2;
        }

        // GDI gives BGRA with undefined alpha; the texture wants RGBA. The screen's material reads this texture
        // as its colour-metal map, so alpha is metalness: 0 keeps it a glowing screen (255 renders dark metal).
        private byte[] ToRgba(IntPtr bits)
        {
            var frame = new byte[byteCount];
            int length = Width * Height * 4;
            Marshal.Copy(bits, frame, 0, length);
            for (int i = 0; i < length; i += 4)
            {
                byte blue = frame[i];
                frame[i] = frame[i + 2];
                frame[i + 2] = blue;
                frame[i + 3] = 0;
            }
            return frame;
        }

        private static IntPtr CreateDib(IntPtr dc, int width, int height, out IntPtr bits)
        {
            var info = new BitmapInfoHeader
            {
                Size = Marshal.SizeOf<BitmapInfoHeader>(),
                Width = width,
                Height = -height, // top-down rows, like the texture
                Planes = 1,
                BitCount = 32,
            };
            IntPtr bitmap = CreateDIBSection(dc, ref info, 0, out bits, IntPtr.Zero, 0);
            if (bitmap == IntPtr.Zero)
                throw new InvalidOperationException("CreateDIBSection failed");
            return bitmap;
        }

        // Visible, titled top-level windows, front to back.
        public static List<WindowInfo> List()
        {
            var windows = new List<WindowInfo>();
            var own = Process.GetCurrentProcess().Id;
            EnumWindows((hwnd, _) =>
            {
                if (!IsWindowVisible(hwnd) || GetWindowTextLength(hwnd) == 0)
                    return true;
                if (DwmGetWindowAttribute(hwnd, 14 /* DWMWA_CLOAKED */, out int cloaked, 4) == 0 && cloaked != 0)
                    return true;
                GetWindowThreadProcessId(hwnd, out uint pid);
                if (pid == own)
                    return true;
                var title = new StringBuilder(GetWindowTextLength(hwnd) + 1);
                GetWindowText(hwnd, title, title.Capacity);
                GetClientRect(hwnd, out Rect client);
                string process = "";
                try
                {
                    process = Process.GetProcessById((int)pid).ProcessName;
                }
                catch (Exception)
                {
                    // Exited or not ours to open.
                }
                windows.Add(new WindowInfo { Handle = hwnd, Title = title.ToString(), Process = process, Width = client.Right, Height = client.Bottom });
                return true;
            }, IntPtr.Zero);
            return windows;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct Rect
        {
            public int Left, Top, Right, Bottom;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct BitmapInfoHeader
        {
            public int Size, Width, Height;
            public short Planes, BitCount;
            public int Compression, SizeImage, XPelsPerMeter, YPelsPerMeter, ClrUsed, ClrImportant;
        }

        private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);

        [DllImport("user32.dll")] private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);
        [DllImport("user32.dll")] private static extern bool IsWindow(IntPtr hwnd);
        [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
        [DllImport("user32.dll")] private static extern bool IsIconic(IntPtr hwnd);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder text, int max);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowTextLength(IntPtr hwnd);
        [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
        [DllImport("user32.dll")] private static extern bool GetClientRect(IntPtr hwnd, out Rect rect);
        [DllImport("user32.dll")] private static extern bool PrintWindow(IntPtr hwnd, IntPtr dc, uint flags);
        [DllImport("user32.dll")] private static extern IntPtr GetDC(IntPtr hwnd);
        [DllImport("user32.dll")] private static extern int ReleaseDC(IntPtr hwnd, IntPtr dc);
        [DllImport("dwmapi.dll")] private static extern int DwmGetWindowAttribute(IntPtr hwnd, int attribute, out int value, int size);
        [DllImport("gdi32.dll")] private static extern IntPtr CreateCompatibleDC(IntPtr dc);
        [DllImport("gdi32.dll")] private static extern IntPtr CreateCompatibleBitmap(IntPtr dc, int width, int height);
        [DllImport("gdi32.dll")] private static extern IntPtr CreateDIBSection(IntPtr dc, ref BitmapInfoHeader info, uint usage, out IntPtr bits, IntPtr section, uint offset);
        [DllImport("gdi32.dll")] private static extern IntPtr SelectObject(IntPtr dc, IntPtr obj);
        [DllImport("gdi32.dll")] private static extern bool DeleteObject(IntPtr obj);
        [DllImport("gdi32.dll")] private static extern bool DeleteDC(IntPtr dc);
        [DllImport("gdi32.dll")] private static extern int SetStretchBltMode(IntPtr dc, int mode);
        [DllImport("gdi32.dll")] private static extern bool StretchBlt(IntPtr dst, int x, int y, int w, int h, IntPtr src, int sx, int sy, int sw, int sh, int rop);
        [DllImport("gdi32.dll")] private static extern bool PatBlt(IntPtr dc, int x, int y, int w, int h, int rop);
        [DllImport("gdi32.dll")] private static extern bool GdiFlush();
    }
}
