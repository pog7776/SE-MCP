using System;
using System.Runtime.InteropServices;

namespace SEScreens
{
    // Input for a mirrored window: posted as the mouse and keyboard messages Windows would send it, to the child
    // window under the point. Most desktop apps take these; apps that read raw input or the global key state
    // (many games, some Chromium and UWP apps) may not. The window isn't brought to the front.
    // The test card draws a marker where the mouse is instead, for checking where input lands.
    internal sealed partial class WindowCapture
    {
        private const uint WmMouseMove = 0x200, WmMouseWheel = 0x20A, WmKeyDown = 0x100, WmKeyUp = 0x101, WmChar = 0x102,
            WmSysKeyDown = 0x104, WmSysKeyUp = 0x105;
        private const int MkLeft = 1, MkRight = 2, MkShift = 4, MkControl = 8, MkMiddle = 0x10;

        // The window's client size at the last capture, for mapping frame pixels back to it.
        private volatile int clientWidth, clientHeight;
        private volatile int markX = -1, markY = -1;
        private volatile bool markDown;
        private IntPtr keyTarget;

        public void Focus(bool focused)
        {
            if (!focused)
                markX = markY = -1;
        }

        public void MouseMove(int x, int y, InputModifiers m)
        {
            Mark(x, y, m);
            if (ToWindow(x, y, out IntPtr target, out int cx, out int cy))
                PostMessage(target, WmMouseMove, (IntPtr)Keys(m), Point(cx, cy));
        }

        public void MouseButton(int x, int y, MouseButton button, bool down, int clicks, InputModifiers m)
        {
            Mark(x, y, m);
            if (!ToWindow(x, y, out IntPtr target, out int cx, out int cy))
                return;
            // WM_LBUTTONDOWN 0x201 / UP 0x202 / DBLCLK 0x203; right from 0x204; middle from 0x207.
            uint first = button == SEScreens.MouseButton.Left ? 0x201u : button == SEScreens.MouseButton.Right ? 0x204u : 0x207u;
            uint message = !down ? first + 1 : clicks == 2 ? first + 2 : first;
            if (down)
                keyTarget = target;
            PostMessage(target, message, (IntPtr)Keys(m), Point(cx, cy));
        }

        public void MouseWheel(int x, int y, int delta, InputModifiers m)
        {
            if (!ToWindow(x, y, out IntPtr target, out int cx, out int cy))
                return;
            // The wheel message carries screen coordinates.
            var p = new NativePoint { X = cx, Y = cy };
            ClientToScreen(target, ref p);
            PostMessage(target, WmMouseWheel, (IntPtr)((delta << 16) | (Keys(m) & 0xFFFF)), Point(p.X, p.Y));
        }

        public void Key(int virtualKey, bool down, bool repeat, InputModifiers m)
        {
            IntPtr target = KeyTarget();
            if (target == IntPtr.Zero)
                return;
            int scan = (int)MapVirtualKey((uint)virtualKey, 0);
            long lParam = 1 | (scan << 16);
            if (repeat)
                lParam |= 1L << 30;
            if (!down)
                lParam |= (1L << 30) | (1L << 31);
            bool alt = (m & InputModifiers.Alt) != 0 && (m & InputModifiers.Control) == 0;
            PostMessage(target, down ? (alt ? WmSysKeyDown : WmKeyDown) : (alt ? WmSysKeyUp : WmKeyUp), (IntPtr)virtualKey, (IntPtr)lParam);
        }

        public void Char(char c, InputModifiers m)
        {
            IntPtr target = KeyTarget();
            if (target != IntPtr.Zero)
                PostMessage(target, WmChar, (IntPtr)c, (IntPtr)1);
        }

        // Frame pixel to the client point of the deepest visible child there. False off the letterboxed picture.
        private bool ToWindow(int x, int y, out IntPtr target, out int cx, out int cy)
        {
            target = IntPtr.Zero;
            cx = cy = 0;
            int w = clientWidth, h = clientHeight;
            if (Window == IntPtr.Zero || w <= 0 || h <= 0)
                return false;
            Letterbox(w, h, out int lx, out int ly, out int lw, out int lh);
            if (x < lx || y < ly || x >= lx + lw || y >= ly + lh)
                return false;
            var p = new NativePoint { X = (x - lx) * w / lw, Y = (y - ly) * h / lh };
            IntPtr hwnd = Window;
            for (int depth = 0; depth < 16; depth++)
            {
                IntPtr child = ChildWindowFromPointEx(hwnd, p, 1 | 2 | 4); // skip invisible, disabled, transparent
                if (child == IntPtr.Zero || child == hwnd)
                    break;
                MapWindowPoints(hwnd, child, ref p, 1);
                hwnd = child;
            }
            target = hwnd;
            cx = p.X;
            cy = p.Y;
            return true;
        }

        // The window's own focus (its thread keeps one even in the background), else what was last clicked.
        private IntPtr KeyTarget()
        {
            if (Window == IntPtr.Zero)
                return IntPtr.Zero;
            var info = new GuiThreadInfo { Size = Marshal.SizeOf<GuiThreadInfo>() };
            uint thread = GetWindowThreadProcessId(Window, out _);
            if (GetGUIThreadInfo(thread, ref info) && info.Focus != IntPtr.Zero)
                return info.Focus;
            return keyTarget != IntPtr.Zero && IsWindow(keyTarget) ? keyTarget : Window;
        }

        private static int Keys(InputModifiers m) =>
            ((m & InputModifiers.LeftButton) != 0 ? MkLeft : 0) | ((m & InputModifiers.RightButton) != 0 ? MkRight : 0)
            | ((m & InputModifiers.MiddleButton) != 0 ? MkMiddle : 0) | ((m & InputModifiers.Shift) != 0 ? MkShift : 0)
            | ((m & InputModifiers.Control) != 0 ? MkControl : 0);

        private static IntPtr Point(int x, int y) => (IntPtr)((y << 16) | (x & 0xFFFF));

        private void Mark(int x, int y, InputModifiers m)
        {
            markX = x;
            markY = y;
            markDown = (m & (InputModifiers.LeftButton | InputModifiers.RightButton | InputModifiers.MiddleButton)) != 0;
        }

        // Test card: a cross at the mouse, red while a button is held.
        private void DrawMarker(byte[] frame)
        {
            int mx = markX, my = markY;
            if (mx < 0 || my < 0)
                return;
            byte r = 255, g = markDown ? (byte)0 : (byte)255, b = g;
            int arm = Math.Max(6, Width / 64), thick = Math.Max(1, Width / 512);
            for (int d = -arm; d <= arm; d++)
                for (int t = -thick; t <= thick; t++)
                {
                    Plot(frame, mx + d, my + t, r, g, b);
                    Plot(frame, mx + t, my + d, r, g, b);
                }
        }

        private void Plot(byte[] frame, int x, int y, byte r, byte g, byte b)
        {
            if (x < 0 || y < 0 || x >= Width || y >= Height)
                return;
            int i = (y * Width + x) * 4;
            frame[i] = r;
            frame[i + 1] = g;
            frame[i + 2] = b;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct NativePoint
        {
            public int X, Y;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct GuiThreadInfo
        {
            public int Size, Flags;
            public IntPtr Active, Focus, Capture, MenuOwner, MoveSize, Caret;
            public Rect CaretRect;
        }

        [DllImport("user32.dll")] private static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);
        [DllImport("user32.dll")] private static extern IntPtr ChildWindowFromPointEx(IntPtr parent, NativePoint point, uint flags);
        [DllImport("user32.dll")] private static extern int MapWindowPoints(IntPtr from, IntPtr to, ref NativePoint points, int count);
        [DllImport("user32.dll")] private static extern bool ClientToScreen(IntPtr hwnd, ref NativePoint point);
        [DllImport("user32.dll")] private static extern bool GetGUIThreadInfo(uint thread, ref GuiThreadInfo info);
        [DllImport("user32.dll")] private static extern uint MapVirtualKey(uint code, uint mapType);
    }
}
