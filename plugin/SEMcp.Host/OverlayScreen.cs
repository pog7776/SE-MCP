using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Sandbox.Game.Entities;
using Sandbox.Game.Gui;
using Sandbox.Game.World;
using Sandbox.Graphics;
using Sandbox.Graphics.GUI;
using SEMcp.Core;
using VRage.Game.Entity;
using VRage.Game.Utils;
using VRage.Utils;
using VRageMath;

namespace SEMcp.Host
{
    // One element the assistant drew on the player's HUD. Positions are fractions of the full screen
    // (0,0 top-left, 1,1 bottom-right). Text may hold {path} placeholders filled from the bound method's result.
    internal sealed class OverlayElement
    {
        public string Id;
        public string Kind; // text, rect, bar, marker
        public float X, Y, W, H;
        public string Text;
        public Color Color = Color.White;
        public Color? Background;
        public Color? LowColor;
        public double? LowBelow;
        public float Scale = 0.8f;
        public string Align = "left";
        public string Font = "White";
        public bool Fill = true;
        public int Thickness = 2;
        public double Value, Max = 100;
        public string ValuePath, MaxPath;
        public Vector3D? Anchor;
        public long AnchorEntity;
        public string BindMethod;
        public JsonObject BindParams;
        public double BindEvery = 1;
        public DateTime NextBind;
        public JsonNode Bound;
        public string BindError;
        public DateTime? Expires;
    }

    // A screen on top of the game HUD that draws our elements. It never takes focus or input, hides with the HUD
    // (Tab / screenshots without HUD) and when another screen (terminal, menu) has focus.
    internal sealed class OverlayScreen : MyGuiScreenBase
    {
        private const string Blank = "Textures\\GUI\\Blank.dds";
        private static readonly Regex Placeholder = new Regex(@"\{([A-Za-z0-9_.\[\]]+)\}");

        private readonly Func<string, JsonObject, JsonNode> call;
        public readonly Dictionary<string, OverlayElement> Elements = new Dictionary<string, OverlayElement>();

        public OverlayScreen(Func<string, JsonObject, JsonNode> call)
            : base(Vector2.Zero)
        {
            this.call = call;
            CanBeHidden = false;
            CanHideOthers = false;
            CanHaveFocus = false;
            m_drawEvenWithoutFocus = true;
            m_closeOnEsc = false;
        }

        public override string GetFriendlyName() => "SEMcpOverlay";

        public bool IsOpen => State != MyGuiScreenState.CLOSED && State != MyGuiScreenState.CLOSING;

        public override bool Update(bool hasFocus)
        {
            var now = DateTime.UtcNow;
            foreach (var e in Elements.Values.Where(e => e.Expires < now).ToList())
                Elements.Remove(e.Id);
            foreach (var e in Elements.Values)
                if (e.BindMethod != null && now >= e.NextBind)
                {
                    e.NextBind = now.AddSeconds(e.BindEvery);
                    try
                    {
                        e.Bound = call(e.BindMethod, e.BindParams);
                        e.BindError = null;
                    }
                    catch (Exception ex)
                    {
                        e.BindError = ex.Message;
                    }
                }
            return base.Update(hasFocus);
        }

        public override bool Draw()
        {
            if (!MyHud.IsVisible || MySession.Static == null || !(MyScreenManager.GetScreenWithFocus() is MyGuiScreenGamePlay))
                return true;
            var screen = MyGuiManager.GetFullscreenRectangle();
            foreach (var e in Elements.Values)
            {
                try
                {
                    DrawElement(e, screen);
                }
                catch (Exception)
                {
                    // A bad element shouldn't stop the rest from drawing.
                }
            }
            return true;
        }

        private void DrawElement(OverlayElement e, Rectangle screen)
        {
            switch (e.Kind)
            {
                case "text":
                {
                    string text = Format(e, e.Text);
                    var pos = Px(screen, e.X, e.Y);
                    if (e.Background is Color bg)
                    {
                        var size = MyGuiManager.GetScreenSizeFromNormalizedSize(MyGuiManager.MeasureString(e.Font, text, e.Scale));
                        float left = e.Align == "right" ? pos.X - size.X : e.Align == "center" ? pos.X - size.X / 2 : pos.X;
                        Box(new Vector2(left - 6, pos.Y - 3), size + new Vector2(12, 6), bg);
                    }
                    Text(text, pos, e.Scale, ColorFor(e, Number(e, e.ValuePath, e.Value)), e.Font, e.Align, false);
                    break;
                }
                case "rect":
                {
                    var tl = Px(screen, e.X, e.Y);
                    var size = new Vector2(e.W * screen.Width, e.H * screen.Height);
                    if (e.Fill)
                        Box(tl, size, e.Color);
                    else
                        Outline(tl, size, e.Color, e.Thickness);
                    break;
                }
                case "bar":
                {
                    var tl = Px(screen, e.X, e.Y);
                    var size = new Vector2(e.W * screen.Width, e.H * screen.Height);
                    double value = Number(e, e.ValuePath, e.Value);
                    double max = Number(e, e.MaxPath, e.Max);
                    float fraction = max > 0 ? (float)MathHelper.Clamp(value / max, 0, 1) : 0;
                    double pct = max > 0 ? 100 * value / max : 0;
                    Box(tl, size, e.Background ?? new Color(0, 0, 0, 140));
                    bool vertical = size.Y > size.X;
                    var color = ColorFor(e, pct);
                    if (vertical)
                        Box(new Vector2(tl.X, tl.Y + size.Y * (1 - fraction)), new Vector2(size.X, size.Y * fraction), color);
                    else
                        Box(tl, new Vector2(size.X * fraction, size.Y), color);
                    Outline(tl, size, new Color(color, 0.9f), 1);
                    if (!string.IsNullOrEmpty(e.Text))
                        Text(Format(e, e.Text, pct), new Vector2(tl.X + 6, tl.Y + size.Y / 2), e.Scale, Color.White, e.Font, "left", true);
                    break;
                }
                case "marker":
                {
                    var world = AnchorPosition(e);
                    if (world == null || MySector.MainCamera == null)
                        return;
                    var camera = MySector.MainCamera.WorldMatrix;
                    var w = world.Value;
                    if (Vector3D.Dot(w - camera.Translation, camera.Forward) <= 0)
                        return;
                    var ndc = MySector.MainCamera.WorldToScreen(ref w);
                    var pos = new Vector2(screen.X + (float)(ndc.X + 1) / 2 * screen.Width, screen.Y + (float)(1 - ndc.Y) / 2 * screen.Height);
                    float r = Math.Max(4, e.W > 0 ? e.W * screen.Height : 10);
                    Outline(pos - new Vector2(r), new Vector2(2 * r), e.Color, e.Thickness);
                    Box(pos - new Vector2(1.5f), new Vector2(3), e.Color);
                    double distance = Vector3D.Distance(camera.Translation, w);
                    string label = Format(e, e.Text ?? "", null, distance);
                    if (label.Length > 0)
                        Text(label, new Vector2(pos.X, pos.Y - r - 4), e.Scale, e.Color, e.Font, "center", false, bottom: true);
                    break;
                }
            }
        }

        private Vector3D? AnchorPosition(OverlayElement e)
        {
            if (e.AnchorEntity != 0)
                return MyEntities.TryGetEntityById(e.AnchorEntity, out MyEntity entity) ? entity.PositionComp.WorldAABB.Center : (Vector3D?)null;
            return e.Anchor;
        }

        private static Color ColorFor(OverlayElement e, double value) =>
            e.LowBelow is double below && e.LowColor is Color low && !double.IsNaN(value) && value < below ? low : e.Color;

        // {path} reads the bound result ("hydrogen.fill", "batteries.storedMWh"); {pct} the bar's percentage,
        // {dist} a marker's distance. Unreadable paths show "?".
        private string Format(OverlayElement e, string text, double? pct = null, double? dist = null)
        {
            if (string.IsNullOrEmpty(text) || text.IndexOf('{') < 0)
                return text ?? "";
            if (e.BindError != null && e.Bound == null)
                return text + " (" + e.BindError + ")";
            return Placeholder.Replace(text, m =>
            {
                string path = m.Groups[1].Value;
                if (path == "pct" && pct != null)
                    return pct.Value.ToString("0", CultureInfo.InvariantCulture);
                if (path == "dist" && dist != null)
                    return dist.Value >= 1000 ? (dist.Value / 1000).ToString("0.0", CultureInfo.InvariantCulture) + " km" : dist.Value.ToString("0", CultureInfo.InvariantCulture) + " m";
                var node = Read(e.Bound, path);
                if (node is JsonValue v && v.TryGetValue(out double d))
                    return d.ToString(Math.Abs(d) >= 100 || d == Math.Floor(d) ? "0" : "0.##", CultureInfo.InvariantCulture);
                return node?.ToString() ?? "?";
            });
        }

        private static double Number(OverlayElement e, string path, double fallback)
        {
            if (path == null)
                return fallback;
            return Read(e.Bound, path) is JsonValue v && v.TryGetValue(out double d) ? d : double.NaN;
        }

        // "a.b[0].c" into a JSON tree.
        public static JsonNode Read(JsonNode node, string path)
        {
            foreach (string part in path.Replace("[", ".").Replace("]", "").Split(new[] { '.' }, StringSplitOptions.RemoveEmptyEntries))
            {
                if (node is JsonObject o)
                    node = o[part];
                else if (node is JsonArray a && int.TryParse(part, out int i) && i >= 0 && i < a.Count)
                    node = a[i];
                else
                    return null;
            }
            return node;
        }

        private static Vector2 Px(Rectangle screen, float x, float y) => new Vector2(screen.X + x * screen.Width, screen.Y + y * screen.Height);

        private static void Box(Vector2 topLeft, Vector2 size, Color color)
        {
            if (size.X <= 0 || size.Y <= 0)
                return;
            MyGuiManager.DrawSpriteBatch(Blank, new Rectangle((int)topLeft.X, (int)topLeft.Y, Math.Max(1, (int)size.X), Math.Max(1, (int)size.Y)), color, true, true);
        }

        private static void Outline(Vector2 tl, Vector2 size, Color color, int t)
        {
            Box(tl, new Vector2(size.X, t), color);
            Box(new Vector2(tl.X, tl.Y + size.Y - t), new Vector2(size.X, t), color);
            Box(tl, new Vector2(t, size.Y), color);
            Box(new Vector2(tl.X + size.X - t, tl.Y), new Vector2(t, size.Y), color);
        }

        private static void Text(string text, Vector2 px, float scale, Color color, string font, string align, bool middle, bool bottom = false)
        {
            var vertical = middle ? "CENTER" : bottom ? "BOTTOM" : "TOP";
            var horizontal = align == "right" ? "RIGHT" : align == "center" ? "CENTER" : "LEFT";
            var drawAlign = (MyGuiDrawAlignEnum)Enum.Parse(typeof(MyGuiDrawAlignEnum), $"HORISONTAL_{horizontal}_AND_VERTICAL_{vertical}");
            var normalized = MyGuiManager.GetNormalizedCoordinateFromScreenCoordinate_FULLSCREEN(px);
            MyGuiManager.DrawString(font, text, normalized, scale, color, drawAlign, false, float.PositiveInfinity, true);
        }
    }
}
