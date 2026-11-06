using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Sandbox.Game.Entities;
using Sandbox.Game.Entities.Cube;
using Sandbox.Game.Gui;
using Sandbox.Game.World;
using Sandbox.Graphics;
using Sandbox.Graphics.GUI;
using Sandbox.ModAPI;
using Sandbox.ModAPI.Interfaces;
using Sandbox.ModAPI.Interfaces.Terminal;
using SEScreens;
using VRage;
using VRage.Game.ModAPI;
using VRage.Input;
using VRage.Utils;
using VRageMath;
using ITerminalAction = Sandbox.Game.Gui.ITerminalAction;

namespace SEMcp.Host
{
    // Cursor mode's block menu, in the style of Build Vision: in SEScreens' cursor mode, right-click a block (anything
    // that isn't a usable screen) and its terminal controls float next to it: switches and checkboxes, sliders, colour
    // channels, buttons, then the block's other actions. Values are set through the terminal properties themselves,
    // the same SetValue the terminal screen calls, so they sync in multiplayer as the terminal does.
    //
    // Mouse: hover picks a row, click toggles or runs it, drag a bar to set it, the wheel nudges a bar (Shift x10,
    // Ctrl /10) or scrolls the list. Keyboard: Up/Down pick, Left/Right nudge or toggle, Enter or Space toggles or runs.
    // Escape or a click off the menu closes it; cursor mode's leave keys close it and leave cursor mode.
    internal static class BlockMenu
    {
        private static MenuScreen open;

        public static void Start() => CursorMode.AddClickHandler(Click);

        public static void Stop()
        {
            CursorMode.RemoveClickHandler(Click);
            open?.CloseScreenNow();
            open = null;
        }

        private static bool Click(CursorClick click)
        {
            if (click.Button != MouseButton.Right)
                return false;
            var block = Pick(click.Line);
            if (block == null)
                return false;
            if (!block.HasLocalPlayerAccess())
            {
                MyAPIGateway.Utilities?.ShowNotification($"{block.CustomName}: access denied", 1500, "Red");
                return true;
            }
            open?.CloseScreenNow();
            open = new MenuScreen(block, click.Cursor);
            MyGuiSandbox.AddScreen(open);
            return true;
        }

        // The terminal block whose mesh the line meets first (not the player's own body). Triangles, as the game's own
        // interaction ray (MyCharacterRaycastDetectorComponent) tests them: physics shapes and grid cells are whole
        // blocks, so they pick the wrong one around panels, buttons and small blocks.
        private static MyTerminalBlock Pick(LineD line)
        {
            var hit = MyEntities.GetIntersectionWithLine(ref line, MySession.Static?.LocalCharacter, null, ignoreChildren: false, ignoreFloatingObjects: false);
            if (hit == null)
                return null;
            var entity = hit.Value.Entity;
            // Armour and other blocks drawn by the grid itself: the triangle's cube says which block.
            if (entity is MyCubeGrid)
                entity = (hit.Value.UserObject as MyCube)?.CubeBlock?.FatBlock;
            // A subpart (a door leaf, a piston head) belongs to its block.
            while (entity != null && !(entity is MyCubeBlock))
                entity = entity.Parent;
            return entity as MyTerminalBlock;
        }

        // One line of the menu.
        private abstract class Row
        {
            public string Label;
            public Func<bool> Enabled = () => true;
            public virtual bool IsBar => false;
            public abstract string Value { get; }
            // 0..1 along the bar, for bar rows.
            public virtual float Fraction => 0;
            public virtual void SetFraction(float f) { }
            // Left/Right or the wheel: steps of 1% of the bar for bars (times the modifier), a flip for switches.
            public virtual void Nudge(int direction, float scale) { }
            // A click on the row (not on a bar), Enter or Space.
            public virtual void Use() { }
        }

        private sealed class BoolRow : Row
        {
            private readonly MyTerminalBlock block;
            private readonly ITerminalProperty<bool> property;
            private readonly string on, off;

            public BoolRow(MyTerminalBlock block, ITerminalProperty<bool> property, string on, string off)
            {
                this.block = block;
                this.property = property;
                this.on = on;
                this.off = off;
            }

            public override string Value => property.GetValue(block) ? on : off;
            public override void Nudge(int direction, float scale) => Use();
            public override void Use() => property.SetValue(block, !property.GetValue(block));
        }

        // A slider. The bar follows the slider's own normalizer, so log sliders move like they do in the terminal.
        private sealed class FloatRow : Row
        {
            private readonly MyTerminalBlock block;
            private readonly ITerminalProperty<float> property;
            private readonly Delegate normalizer, denormalizer;
            private readonly Action<IMyTerminalBlock, StringBuilder> writer;
            private readonly StringBuilder text = new StringBuilder();

            public FloatRow(MyTerminalBlock block, ITerminalProperty<float> property)
            {
                this.block = block;
                this.property = property;
                var type = property.GetType();
                normalizer = type.GetField("Normalizer")?.GetValue(property) as Delegate;
                denormalizer = type.GetField("Denormalizer")?.GetValue(property) as Delegate;
                if (type.GetField("Writer")?.GetValue(property) != null)
                    writer = (property as IMyTerminalControlSlider)?.Writer;
            }

            public override bool IsBar => true;

            public override string Value
            {
                get
                {
                    text.Clear();
                    try
                    {
                        writer?.Invoke(block, text);
                    }
                    catch (Exception)
                    {
                        text.Clear();
                    }
                    return text.Length > 0 ? text.ToString() : property.GetValue(block).ToString("0.##");
                }
            }

            public override float Fraction => MathHelper.Clamp(Normalize(property.GetValue(block)), 0, 1);

            public override void SetFraction(float f)
            {
                float value = Denormalize(MathHelper.Clamp(f, 0, 1));
                if (value != property.GetValue(block))
                    property.SetValue(block, value);
            }

            public override void Nudge(int direction, float scale) => SetFraction(Fraction + direction * 0.01f * scale);

            private float Normalize(float value)
            {
                if (normalizer != null)
                    return (float)normalizer.DynamicInvoke(block, value);
                float min = property.GetMinimum(block), max = property.GetMaximum(block);
                return max > min ? (value - min) / (max - min) : 0;
            }

            private float Denormalize(float f)
            {
                if (denormalizer != null)
                    return (float)denormalizer.DynamicInvoke(block, f);
                float min = property.GetMinimum(block), max = property.GetMaximum(block);
                return min + f * (max - min);
            }
        }

        // One of a colour's R, G, B, as a 0..255 bar.
        private sealed class ColorRow : Row
        {
            private readonly MyTerminalBlock block;
            private readonly ITerminalProperty<Color> property;
            private readonly int channel;

            public ColorRow(MyTerminalBlock block, ITerminalProperty<Color> property, int channel)
            {
                this.block = block;
                this.property = property;
                this.channel = channel;
            }

            public override bool IsBar => true;
            public override string Value => Get().ToString();
            public override float Fraction => Get() / 255f;
            public override void SetFraction(float f) => Set((int)Math.Round(MathHelper.Clamp(f, 0, 1) * 255));
            public override void Nudge(int direction, float scale) => Set(Get() + direction * Math.Max(1, (int)scale));

            private int Get()
            {
                var c = property.GetValue(block);
                return channel == 0 ? c.R : channel == 1 ? c.G : c.B;
            }

            private void Set(int v)
            {
                var c = property.GetValue(block);
                byte b = (byte)MathHelper.Clamp(v, 0, 255);
                if (channel == 0) c.R = b; else if (channel == 1) c.G = b; else c.B = b;
                if (c != property.GetValue(block))
                    property.SetValue(block, c);
            }
        }

        private sealed class ActionRow : Row
        {
            private readonly Action run;

            public ActionRow(Action run) => this.run = run;

            public override string Value => "Run";
            public override void Use() => run();
        }

        // The block's terminal, as rows: its visible controls in terminal order, then the actions no control owns.
        private static List<Row> Rows(MyTerminalBlock block)
        {
            var rows = new List<Row>();
            var owned = new HashSet<string>();
            var controls = MyTerminalControls.Static?.GetControls(block) ?? new List<ITerminalControl>();
            foreach (var control in controls)
            {
                foreach (var action in control.Actions ?? new ITerminalAction[0])
                    owned.Add(action.Id);
                if (!control.IsVisible(block))
                    continue;
                var mod = control as IMyTerminalControl;
                Func<bool> enabled = () => mod?.Enabled?.Invoke(block) ?? true;
                string title = Title((control as IMyTerminalControlTitleTooltip)?.Title, control.Id);
                switch (control)
                {
                    case IMyTerminalControlOnOffSwitch s:
                        rows.Add(new BoolRow(block, (ITerminalProperty<bool>)s, Title(s.OnText, "On"), Title(s.OffText, "Off")) { Label = title, Enabled = enabled });
                        break;
                    case IMyTerminalControlCheckbox c:
                        rows.Add(new BoolRow(block, (ITerminalProperty<bool>)c, "Yes", "No") { Label = title, Enabled = enabled });
                        break;
                    case IMyTerminalControlSlider s:
                        rows.Add(new FloatRow(block, (ITerminalProperty<float>)s) { Label = title, Enabled = enabled });
                        break;
                    case IMyTerminalControlColor c:
                        for (int channel = 0; channel < 3; channel++)
                            rows.Add(new ColorRow(block, (ITerminalProperty<Color>)c, channel) { Label = $"{title} {"RGB"[channel]}", Enabled = enabled });
                        break;
                    case IMyTerminalControlButton b:
                        var press = b.Action;
                        rows.Add(new ActionRow(() => press?.Invoke(block)) { Label = title, Enabled = enabled });
                        break;
                }
            }
            var actions = MyTerminalControls.Static?.GetActions(block) ?? new List<ITerminalAction>();
            foreach (var action in actions)
            {
                if (owned.Contains(action.Id))
                    continue;
                var mod = action as IMyTerminalAction;
                rows.Add(new ActionRow(() => action.Apply(block))
                {
                    Label = action.Name?.ToString() ?? action.Id,
                    Enabled = () => mod?.Enabled?.Invoke(block) ?? true,
                });
            }
            return rows;
        }

        private static string Title(MyStringId? id, string fallback)
        {
            if (id == null || id.Value == MyStringId.NullOrEmpty)
                return fallback;
            string text = MyTexts.GetString(id.Value);
            return string.IsNullOrWhiteSpace(text) ? fallback : text;
        }

        // Focused like cursor mode's own screen, so the camera keeps still; cursor mode gets focus back when it closes.
        private sealed class MenuScreen : MyGuiScreenBase
        {
            private const string Blank = "Textures\\GUI\\Blank.dds", Font = "White";
            private const int VisibleRows = 16;
            private const double Reach = 10;
            private static readonly Color Back = new Color(12, 20, 28, 225), Head = new Color(36, 64, 84, 240),
                Hover = new Color(60, 110, 140, 160), Track = new Color(255, 255, 255, 40), Fill = new Color(110, 180, 220, 220),
                Text = new Color(220, 230, 235), Dim = new Color(120, 130, 135);

            private readonly MyTerminalBlock block;
            private readonly List<Row> rows;
            private readonly Vector2 clickedAt;
            private readonly CursorMode.LeaveKeys leave = new CursorMode.LeaveKeys();
            private int selected, first;
            private Row dragging;
            // Laid out each frame in fullscreen pixels.
            private Vector2 topLeft, size;
            private float rowHeight, scale;

            public MenuScreen(MyTerminalBlock block, Vector2 cursor)
                : base(new Vector2(0.5f, 0.5f), null, null)
            {
                this.block = block;
                clickedAt = cursor;
                rows = Rows(block);
                EnabledBackgroundFade = false;
                CanHideOthers = false;
                CanBeHidden = false;
                DrawMouseCursor = true;
                m_closeOnEsc = false;
            }

            public override string GetFriendlyName() => "SEMcpBlockMenu";

            // Not base.HandleInput: we have no GUI controls, and Escape is handled here.
            public override void HandleInput(bool receivedFocusInThisUpdate)
            {
                var input = MyInput.Static;
                var camera = MySector.MainCamera;
                if (input == null || camera == null || !CursorMode.Active || block.Closed || !block.InScene
                    || Vector3D.Distance(camera.Position, block.WorldMatrix.Translation) > Reach || !block.HasLocalPlayerAccess())
                {
                    Close();
                    return;
                }
                if (leave.Update(input))
                {
                    Close();
                    CursorMode.Leave();
                    return;
                }
                if (input.IsNewKeyPressed(MyKeys.Escape))
                {
                    Close();
                    return;
                }
                Layout();
                var mouse = Mouse();
                int hover = RowAt(mouse);
                if (hover >= 0 && dragging == null)
                    selected = hover;
                bool inside = new RectangleF(topLeft, size).Contains(mouse);
                float step = input.IsAnyShiftKeyPressed() ? 10 : input.IsAnyCtrlKeyPressed() ? 0.1f : 1;

                if (dragging != null)
                {
                    if (input.IsMousePressed(MyMouseButtonsEnum.Left))
                        dragging.SetFraction(BarFraction(mouse.X));
                    else
                        dragging = null;
                }
                else if (input.IsNewMousePressed(MyMouseButtonsEnum.Left) || input.IsNewMousePressed(MyMouseButtonsEnum.Right)
                    || input.IsNewMousePressed(MyMouseButtonsEnum.Middle))
                {
                    if (!inside)
                    {
                        Close();
                        return;
                    }
                    if (hover >= 0 && input.IsNewMousePressed(MyMouseButtonsEnum.Left) && rows[hover].Enabled())
                    {
                        var row = rows[hover];
                        if (row.IsBar && mouse.X >= BarLeft)
                        {
                            dragging = row;
                            row.SetFraction(BarFraction(mouse.X));
                        }
                        else
                        {
                            row.Use();
                        }
                    }
                }

                int wheel = input.DeltaMouseScrollWheelValue();
                if (wheel != 0 && inside)
                {
                    int direction = Math.Sign(wheel);
                    if (hover >= 0 && rows[hover].IsBar && rows[hover].Enabled())
                        rows[hover].Nudge(direction, step);
                    else
                        first = MathHelper.Clamp(first - direction, 0, Math.Max(0, rows.Count - VisibleRows));
                }

                if (input.IsNewKeyPressed(MyKeys.Up))
                    Select(selected - 1);
                if (input.IsNewKeyPressed(MyKeys.Down))
                    Select(selected + 1);
                if (selected >= 0 && selected < rows.Count && rows[selected].Enabled())
                {
                    if (input.IsNewKeyPressed(MyKeys.Left))
                        rows[selected].Nudge(-1, step);
                    if (input.IsNewKeyPressed(MyKeys.Right))
                        rows[selected].Nudge(1, step);
                    if (input.IsNewKeyPressed(MyKeys.Enter) || input.IsNewKeyPressed(MyKeys.Space))
                    {
                        if (rows[selected].IsBar)
                            rows[selected].Nudge(1, step);
                        else
                            rows[selected].Use();
                    }
                }
            }

            private void Select(int index)
            {
                if (rows.Count == 0)
                    return;
                selected = MathHelper.Clamp(index, 0, rows.Count - 1);
                if (selected < first)
                    first = selected;
                if (selected >= first + VisibleRows)
                    first = selected - VisibleRows + 1;
            }

            private void Close()
            {
                dragging = null;
                CloseScreen();
                if (open == this)
                    open = null;
            }

            // Beside the block on screen (or where it was clicked, if the block is behind the camera), kept on screen.
            private void Layout()
            {
                var full = MyGuiManager.GetFullscreenRectangle();
                scale = full.Height / 1080f;
                rowHeight = 28 * scale;
                size = new Vector2(440 * scale, rowHeight * (Math.Min(rows.Count, VisibleRows) + 1) + 6 * scale);
                var anchor = Project(block.PositionComp.WorldAABB.Center, full) ?? clickedAt;
                var at = anchor + new Vector2(48 * scale, -size.Y / 3);
                if (at.X + size.X > full.Right - 8)
                    at.X = anchor.X - 48 * scale - size.X;
                at.X = MathHelper.Clamp(at.X, full.Left + 8, full.Right - 8 - size.X);
                at.Y = MathHelper.Clamp(at.Y, full.Top + 8, full.Bottom - 8 - size.Y);
                topLeft = at;
            }

            private float BarLeft => topLeft.X + size.X * 0.5f;
            private float BarRight => topLeft.X + size.X - 10 * scale;
            private float BarFraction(float x) => (x - BarLeft) / (BarRight - BarLeft);

            private int RowAt(Vector2 p)
            {
                if (p.X < topLeft.X || p.X > topLeft.X + size.X)
                    return -1;
                int i = (int)Math.Floor((p.Y - topLeft.Y - rowHeight) / rowHeight);
                if (i < 0 || i >= VisibleRows)
                    return -1;
                i += first;
                return i < rows.Count ? i : -1;
            }

            public override bool Draw()
            {
                if (MySector.MainCamera == null || block.Closed)
                    return true;
                Layout();
                float pad = 10 * scale, textScale = 0.75f * scale;
                Box(topLeft, size, Back);
                Box(topLeft, new Vector2(size.X, rowHeight), Head);
                string more = rows.Count > VisibleRows ? $"  {first + 1}-{Math.Min(rows.Count, first + VisibleRows)}/{rows.Count}" : "";
                Label(block.CustomName + more, new Vector2(topLeft.X + pad, topLeft.Y + rowHeight / 2), textScale, Color.White, false, size.X - 2 * pad);
                if (rows.Count == 0)
                {
                    Label("Nothing to set", new Vector2(topLeft.X + pad, topLeft.Y + rowHeight * 1.5f), textScale, Dim, false, size.X - 2 * pad);
                    return true;
                }
                for (int i = first; i < Math.Min(rows.Count, first + VisibleRows); i++)
                {
                    var row = rows[i];
                    float y = topLeft.Y + rowHeight * (1 + i - first) + 3 * scale;
                    if (i == selected)
                        Box(new Vector2(topLeft.X, y), new Vector2(size.X, rowHeight), Hover);
                    bool enabled;
                    string value;
                    try
                    {
                        enabled = row.Enabled();
                        value = row.Value;
                    }
                    catch (Exception)
                    {
                        enabled = false;
                        value = "?";
                    }
                    var colour = enabled ? Text : Dim;
                    float middle = y + rowHeight / 2;
                    Label(row.Label, new Vector2(topLeft.X + pad, middle), textScale, colour, false, size.X * 0.5f - 2 * pad);
                    if (row.IsBar)
                    {
                        float f = 0;
                        try { f = row.Fraction; } catch (Exception) { }
                        var barTop = new Vector2(BarLeft, y + 4 * scale);
                        var barSize = new Vector2(BarRight - BarLeft, rowHeight - 8 * scale);
                        Box(barTop, barSize, Track);
                        Box(barTop, new Vector2(barSize.X * f, barSize.Y), enabled ? Fill : Dim);
                        Label(value, new Vector2((BarLeft + BarRight) / 2, middle), textScale * 0.9f, Color.White, true, barSize.X);
                    }
                    else
                    {
                        Label(value, new Vector2(topLeft.X + size.X - pad, middle), textScale, colour, false, size.X * 0.5f - pad, right: true);
                    }
                }
                return true;
            }

            private static Vector2 Mouse()
            {
                var full = MyGuiManager.GetFullscreenRectangle();
                var area = MyInput.Static.GetMouseAreaSize();
                var p = MyInput.Static.GetMousePosition();
                return new Vector2(p.X * full.Width / area.X, p.Y * full.Height / area.Y);
            }

            private static Vector2? Project(Vector3D world, Rectangle screen)
            {
                var camera = MySector.MainCamera.WorldMatrix;
                if (Vector3D.Dot(world - camera.Translation, camera.Forward) <= 0.1)
                    return null;
                var ndc = MySector.MainCamera.WorldToScreen(ref world);
                return new Vector2(screen.X + (float)(ndc.X + 1) / 2 * screen.Width, screen.Y + (float)(1 - ndc.Y) / 2 * screen.Height);
            }

            private static void Box(Vector2 tl, Vector2 size, Color color)
            {
                if (size.X <= 0 || size.Y <= 0)
                    return;
                MyGuiManager.DrawSpriteBatch(Blank, new Rectangle((int)tl.X, (int)tl.Y, Math.Max(1, (int)size.X), Math.Max(1, (int)size.Y)), color, true, true);
            }

            // Vertically centred on px; maxWidth in pixels.
            private static void Label(string text, Vector2 px, float textScale, Color color, bool centre, float maxWidth, bool right = false)
            {
                var align = centre ? MyGuiDrawAlignEnum.HORISONTAL_CENTER_AND_VERTICAL_CENTER
                    : right ? MyGuiDrawAlignEnum.HORISONTAL_RIGHT_AND_VERTICAL_CENTER : MyGuiDrawAlignEnum.HORISONTAL_LEFT_AND_VERTICAL_CENTER;
                float width = MyGuiManager.GetNormalizedSizeFromScreenSize(new Vector2(maxWidth, 0)).X;
                MyGuiManager.DrawString(Font, text ?? "", MyGuiManager.GetNormalizedCoordinateFromScreenCoordinate_FULLSCREEN(px), textScale, color, align, false, width, true);
            }
        }
    }
}
