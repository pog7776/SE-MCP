using System;
using System.Collections.Generic;
using VRage.Input;
using VRageMath;
using IMyInput = VRage.Input.IMyInput;

namespace SEScreens
{
    // A click in cursor mode that no screen took: the camera line through the cursor, already cut to cursor mode's
    // reach, and where the cursor is in fullscreen pixels (MyGuiManager.GetFullscreenRectangle's space).
    public sealed class CursorClick
    {
        public LineD Line;
        public MouseButton Button;
        public InputModifiers Modifiers;
        public Vector2 Cursor;
    }

    // For other plugins: what cursor mode does off screens. SEScreens only does screens; a click on nothing it owns
    // is offered to the handlers registered here (newest first), so something like a block menu can live elsewhere.
    // A handler may open its own GUI screen on top; cursor mode gets focus back when that closes. Game thread.
    public static class CursorMode
    {
        private static readonly List<Func<CursorClick, bool>> Handlers = new List<Func<CursorClick, bool>>();

        public static bool Active => ScreenInput.Active;

        // A handler returns true when it used the click. Remove it again before unloading (hot reload).
        public static void AddClickHandler(Func<CursorClick, bool> handler)
        {
            if (handler != null && !Handlers.Contains(handler))
                Handlers.Add(handler);
        }

        public static void RemoveClickHandler(Func<CursorClick, bool> handler) => Handlers.Remove(handler);

        // Leaves cursor mode, as the leave keys do.
        public static void Leave() => ScreenInput.Leave();

        internal static bool OffScreenClick(CursorClick click)
        {
            for (int i = Handlers.Count - 1; i >= 0; i--)
            {
                try
                {
                    if (Handlers[i](click))
                        return true;
                }
                catch (Exception e)
                {
                    Screens.Log($"Cursor mode click handler failed: {e}");
                }
            }
            return false;
        }

        internal static void Clear() => Handlers.Clear();

        // The keys that leave cursor mode (an Alt tap, the Menu key), for a screen opened on top of it that takes
        // focus and so hides them from cursor mode. Call Update every HandleInput; true when one was pressed.
        public sealed class LeaveKeys
        {
            private readonly ScreenInput.TapDetector tap = new ScreenInput.TapDetector();
            private readonly List<MyKeys> keys = new List<MyKeys>();

            public bool Update(IMyInput input)
            {
                input.GetPressedKeys(keys);
                return tap.Update(keys, input, mouseMoved: false) | input.IsNewKeyPressed(ScreenInput.PressToggle);
            }
        }
    }
}
