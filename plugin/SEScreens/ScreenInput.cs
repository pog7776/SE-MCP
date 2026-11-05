using System;
using System.Collections.Generic;
using System.Diagnostics;
using Sandbox.Game.Gui;
using Sandbox.Game.World;
using Sandbox.Graphics.GUI;
using Sandbox.ModAPI;
using VRage.Input;
using VRageMath;
using IMyInput = VRage.Input.IMyInput;

namespace SEScreens
{
    // Cursor mode: use what screens show with the real mouse and keyboard, like an interaction mode.
    //
    // Tap Alt (pressed and released with no other key and no mouse movement, so holding it to look around is
    // unchanged) or press the Menu key near a screen that shows a source you can use. A GUI screen of ours then takes
    // focus, the way chat or the terminal does: the game stops reading the controls, the camera holds still and the
    // mouse cursor shows. The cursor's line from the camera finds the screen under it, which gets the mouse; clicking
    // a screen gives it the keyboard (the screen at the crosshair has it to start with). Clicking off every screen
    // takes the keyboard back; Escape with no screen focused, another Alt tap or the Menu key leave. Alt held with
    // another key is still Alt to the source.
    internal static class ScreenInput
    {
        // Tapped, or pressed: the Menu (Application) key isn't a modifier, so it toggles straight away.
        public const MyKeys TapToggle = MyKeys.Alt, PressToggle = MyKeys.Apps;
        private const int TapMs = 400;
        // Metres from the camera a screen can be used at.
        private const double Reach = 8;

        private static CaptureScreen capture;
        private static readonly TapDetector GameTap = new TapDetector();
        private static readonly List<MyKeys> Keys = new List<MyKeys>();

        public static bool Active => capture != null;

        // The screen keyboard input goes to while in input mode.
        internal static Screens.Feed Focused => capture?.Focus;

        // Every frame, from Screens.Update.
        public static void Update()
        {
            if (capture != null)
            {
                if (capture.State == MyGuiScreenState.CLOSED || capture.State == MyGuiScreenState.CLOSING)
                    capture = null;
                return;
            }
            // Only while playing: not in menus, chat or the terminal.
            if (!(MyScreenManager.GetScreenWithFocus() is MyGuiScreenGamePlay) || MyInput.Static == null)
            {
                GameTap.Reset();
                return;
            }
            MyInput.Static.GetPressedKeys(Keys);
            bool moved = MyInput.Static.GetMouseX() != 0 || MyInput.Static.GetMouseY() != 0; // looking around
            if (GameTap.Update(Keys, MyInput.Static, moved) || MyInput.Static.IsNewKeyPressed(PressToggle))
                Enter();
        }

        public static void Stop()
        {
            capture?.Release();
            capture?.CloseScreenNow();
            capture = null;
        }

        // Only near a screen you can use, so a tap elsewhere does nothing. The screen at the crosshair gets the keyboard.
        private static void Enter()
        {
            var camera = MySector.MainCamera;
            if (camera == null || !Screens.AnyUsableWithin(camera.Position, Reach))
                return;
            var line = new LineD(camera.Position, camera.Position + (Vector3D)camera.ForwardVector * Reach);
            capture = new CaptureScreen(Nearest(line, out _));
            MyGuiSandbox.AddScreen(capture);
            MyAPIGateway.Utilities?.ShowNotification("Cursor mode: click a screen to use it. Tap Alt or press Menu to go back", 3000);
        }

        // The nearest usable screen the line crosses, and the pixel of its source there.
        internal static Screens.Feed Nearest(LineD line, out Vector2I pixel)
        {
            pixel = default;
            Screens.Feed best = null;
            double bestDistance = double.MaxValue;
            foreach (var f in Screens.UsableFeeds())
            {
                var geometry = Screens.Geometry(f);
                if (geometry == null || !geometry.Hit(f.Block.WorldMatrix, line, out double distance, out Vector2 uv) || distance >= bestDistance)
                    continue;
                best = f;
                bestDistance = distance;
                pixel = new Vector2I(
                    MathHelper.Clamp((int)(uv.X * f.Source.Width), 0, f.Source.Width - 1),
                    MathHelper.Clamp((int)(uv.Y * f.Source.Height), 0, f.Source.Height - 1));
            }
            return best;
        }

        internal static InputModifiers Modifiers(List<MyKeys> keys, IMyInput input)
        {
            var m = InputModifiers.None;
            foreach (var k in keys)
            {
                switch (Generic(k))
                {
                    case MyKeys.Shift: m |= InputModifiers.Shift; break;
                    case MyKeys.Control: m |= InputModifiers.Control; break;
                    case MyKeys.Alt: m |= InputModifiers.Alt; break;
                }
            }
            if (input.IsLeftMousePressed()) m |= InputModifiers.LeftButton;
            if (input.IsMiddleMousePressed()) m |= InputModifiers.MiddleButton;
            if (input.IsRightMousePressed()) m |= InputModifiers.RightButton;
            return m;
        }

        // Left/right modifier keys as Windows sends them in key messages.
        internal static MyKeys Generic(MyKeys k)
        {
            switch (k)
            {
                case MyKeys.LeftShift: case MyKeys.RightShift: return MyKeys.Shift;
                case MyKeys.LeftControl: case MyKeys.RightControl: return MyKeys.Control;
                case MyKeys.LeftAlt: case MyKeys.RightAlt: return MyKeys.Alt;
                default: return k;
            }
        }

        // A tap: Alt pressed and released within TapMs with no other key, button or (in game) mouse movement between.
        private sealed class TapDetector
        {
            private readonly Stopwatch held = new Stopwatch();
            private bool down, spoiled;

            public void Reset()
            {
                down = false;
                held.Reset();
            }

            public bool Update(List<MyKeys> keys, IMyInput input, bool mouseMoved)
            {
                bool now = keys.Exists(k => Generic(k) == TapToggle);
                if (now && !down)
                {
                    down = true;
                    spoiled = mouseMoved || keys.Exists(k => Generic(k) != TapToggle); // anything else already held
                    held.Restart();
                    return false;
                }
                if (now)
                {
                    if (mouseMoved || keys.Exists(k => Generic(k) != TapToggle) || input.IsAnyNewMousePressed()
                        || input.DeltaMouseScrollWheelValue() != 0)
                        spoiled = true;
                    return false;
                }
                if (!down)
                    return false;
                down = false;
                return !spoiled && held.ElapsedMilliseconds <= TapMs;
            }
        }

        // Invisible while it has focus, apart from the cursor; the world still draws behind it.
        private sealed class CaptureScreen : MyGuiScreenBase
        {
            private const int RepeatDelayMs = 500, RepeatEveryMs = 33, DoubleClickMs = 500, DoubleClickPixels = 4;
            private static readonly MyMouseButtonsEnum[] Buttons = { MyMouseButtonsEnum.Left, MyMouseButtonsEnum.Middle, MyMouseButtonsEnum.Right };

            private Screens.Feed focus;
            public Screens.Feed Focus => focus;
            private Screens.Feed hover;
            private Vector2I hoverPixel = new Vector2I(-1, -1);
            private readonly Screens.Feed[] pressedOn = new Screens.Feed[3];
            private readonly Vector2I[] pressedAt = new Vector2I[3];
            private readonly TapDetector tap = new TapDetector();
            private readonly List<MyKeys> keys = new List<MyKeys>();
            private readonly HashSet<MyKeys> held = new HashSet<MyKeys>();
            private MyKeys repeatKey;
            private readonly Stopwatch repeatClock = new Stopwatch();
            private long nextRepeat;
            private readonly Stopwatch clickClock = Stopwatch.StartNew();
            private long lastClickAt = -DoubleClickMs;
            private Vector2I lastClickPixel;
            private int lastClickButton = -1, clicks;

            public CaptureScreen(Screens.Feed target)
                : base(new Vector2(0.5f, 0.5f), null, null)
            {
                EnabledBackgroundFade = false;
                CanHideOthers = false;
                CanBeHidden = false;
                DrawMouseCursor = true;
                m_closeOnEsc = false; // Escape belongs to the source
                focus = target;
                Sink(focus)?.Focus(true);
            }

            public override string GetFriendlyName() => "SEScreensInput";

            // Not base.HandleInput: that closes on Escape and moves between GUI controls; we have none.
            public override void HandleInput(bool receivedFocusInThisUpdate)
            {
                var input = MyInput.Static;
                var camera = MySector.MainCamera;
                if (input == null || camera == null || !Screens.AnyUsableWithin(camera.Position, Reach * 2))
                {
                    Leave();
                    return;
                }
                if (focus != null && !Screens.IsUsable(focus))
                    focus = null;
                input.GetPressedKeys(keys);
                bool escape = focus == null && input.IsNewKeyPressed(MyKeys.Escape);
                if (tap.Update(keys, input, mouseMoved: false) || input.IsNewKeyPressed(PressToggle) || escape)
                {
                    Leave();
                    return;
                }
                var mods = Modifiers(keys, input);
                var line = camera.WorldLineFromScreen(input.GetMousePosition());
                line = new LineD(line.From, line.From + line.Direction * Reach);
                hover = Nearest(line, out Vector2I pixel);
                if (hover != null && pixel != hoverPixel)
                {
                    hoverPixel = pixel;
                    Sink(hover)?.MouseMove(pixel.X, pixel.Y, mods);
                }
                else if (hover == null)
                {
                    hoverPixel = new Vector2I(-1, -1);
                }
                if (Mouse(input, mods, pixel))
                    return;
                int wheel = input.DeltaMouseScrollWheelValue();
                if (wheel != 0 && hover != null)
                    Sink(hover)?.MouseWheel(pixel.X, pixel.Y, wheel, mods);
                Keyboard(input, mods);
            }

            // True if a click off every screen left input mode.
            private bool Mouse(IMyInput input, InputModifiers mods, Vector2I pixel)
            {
                for (int b = 0; b < Buttons.Length; b++)
                {
                    if (input.IsNewMousePressed(Buttons[b]))
                    {
                        if (hover == null)
                        {
                            // Off every screen: the keyboard goes back to nobody; cursor mode stays.
                            ReleaseKeys();
                            Sink(focus)?.Focus(false);
                            focus = null;
                            continue;
                        }
                        if (hover != focus)
                        {
                            ReleaseKeys();
                            Sink(focus)?.Focus(false);
                            focus = hover;
                            Sink(focus)?.Focus(true);
                        }
                        long now = clickClock.ElapsedMilliseconds;
                        bool again = b == lastClickButton && now - lastClickAt <= DoubleClickMs
                            && Math.Abs(pixel.X - lastClickPixel.X) <= DoubleClickPixels && Math.Abs(pixel.Y - lastClickPixel.Y) <= DoubleClickPixels;
                        clicks = again ? Math.Min(clicks + 1, 3) : 1;
                        lastClickAt = now;
                        lastClickPixel = pixel;
                        lastClickButton = b;
                        pressedOn[b] = hover;
                        pressedAt[b] = pixel;
                        Sink(hover)?.MouseButton(pixel.X, pixel.Y, (MouseButton)b, true, clicks, mods);
                    }
                    else if (pressedOn[b] != null && !input.IsMousePressed(Buttons[b]))
                    {
                        // A drag that ends off the screen lets go where it last was.
                        var at = hover == pressedOn[b] ? pixel : pressedAt[b];
                        Sink(pressedOn[b])?.MouseButton(at.X, at.Y, (MouseButton)b, false, clicks, mods);
                        pressedOn[b] = null;
                    }
                    else if (pressedOn[b] != null && hover == pressedOn[b])
                    {
                        pressedAt[b] = pixel;
                    }
                }
                return false;
            }

            private void Keyboard(IMyInput input, InputModifiers mods)
            {
                var sink = Sink(focus);
                if (sink == null)
                    return;
                var now = new HashSet<MyKeys>();
                foreach (var k in keys)
                    if ((int)k >= 8 && k != PressToggle) // not the mouse buttons, not ours
                        now.Add(Generic(k));
                foreach (var k in now)
                {
                    if (held.Contains(k))
                        continue;
                    sink.Key((int)k, true, false, mods);
                    if (k != MyKeys.Shift && k != MyKeys.Control && k != MyKeys.Alt)
                    {
                        repeatKey = k;
                        repeatClock.Restart();
                        nextRepeat = RepeatDelayMs;
                    }
                }
                foreach (var k in held)
                    if (!now.Contains(k))
                    {
                        sink.Key((int)k, false, false, mods);
                        if (k == repeatKey)
                            repeatKey = MyKeys.None;
                    }
                held.Clear();
                held.UnionWith(now);
                // Held keys repeat, as Windows repeats them (arrows, Backspace, Delete).
                if (repeatKey != MyKeys.None && repeatClock.ElapsedMilliseconds >= nextRepeat)
                {
                    sink.Key((int)repeatKey, true, true, mods);
                    nextRepeat += RepeatEveryMs;
                }
                bool enterTyped = false;
                foreach (char c in input.TextInput)
                {
                    enterTyped |= c == '\r' || c == '\n';
                    sink.Char(c == '\n' ? '\r' : c, mods);
                }
                // The game's text input can leave out Enter; the source still wants its character.
                if (!enterTyped && input.IsNewKeyPressed(MyKeys.Enter))
                    sink.Char('\r', mods);
            }

            private void Leave()
            {
                Release();
                CloseScreen();
            }

            // Let go of everything held, so the source isn't left with a stuck key or button.
            public void Release()
            {
                var mods = InputModifiers.None;
                for (int b = 0; b < pressedOn.Length; b++)
                    if (pressedOn[b] != null)
                    {
                        Sink(pressedOn[b])?.MouseButton(pressedAt[b].X, pressedAt[b].Y, (MouseButton)b, false, 1, mods);
                        pressedOn[b] = null;
                    }
                ReleaseKeys();
                Sink(focus)?.Focus(false);
            }

            // Key ups for the keys the focused screen saw go down, before the keyboard moves away from it.
            private void ReleaseKeys()
            {
                var sink = Sink(focus);
                foreach (var k in held)
                    sink?.Key((int)k, false, false, InputModifiers.None);
                held.Clear();
                repeatKey = MyKeys.None;
            }

            private static IInputSink Sink(Screens.Feed f) => f != null && Screens.IsUsable(f) ? f.Source as IInputSink : null;
        }
    }
}
