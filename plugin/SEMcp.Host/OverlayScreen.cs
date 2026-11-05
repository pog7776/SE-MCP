using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using Sandbox.Game.Entities;
using Sandbox.Game.Entities.Character;
using Sandbox.Game.Gui;
using Sandbox.Game.Weapons;
using Sandbox.Game.World;
using Sandbox.Graphics;
using Sandbox.Graphics.GUI;
using SEMcp.Core;
using VRage.Game;
using VRage.Game.Entity;
using VRage.Game.ModAPI;
using VRage.Game.Utils;
using VRage.Utils;
using VRageMath;
using VRageRender;

namespace SEMcp.Host
{
    // One element the assistant drew on the player's HUD. Positions are fractions of the full screen
    // (0,0 top-left, 1,1 bottom-right). Text may hold {path} placeholders filled from the bound method's result.
    internal sealed class OverlayElement
    {
        public string Id;
        public string Kind; // text, rect, bar, line, arrow, circle, icon, marker, track, route
        public float X, Y, W, H, X2, Y2;
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
        public string Style = "box"; // marker/track: box (brackets round the thing), point
        public bool Offscreen = true; // marker/track: an edge arrow when the thing is off screen
        public string Texture;
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

        // track: what to follow, refreshed twice a second.
        public HashSet<string> TrackWhat = new HashSet<string>();
        public double Range = 3000;
        public bool IncomingOnly;
        public int MaxTargets = 30;
        public List<long> Tracked = new List<long>();
        public DateTime NextScan;

        // route: waypoints the player is guided along. Leg is the one they're heading for; reaching it (within
        // ArriveRadius, twice that for turns on the way) moves on. With an anchor, the last point follows it.
        public List<Vector3D> Route;
        public int Leg;
        public double ArriveRadius = 3;
        public string Name;
        public string Note; // what the AI meant to do on arrival; carried in the arrived event
    }

    // A screen on top of the game HUD that draws our elements. It never takes focus or input, hides with the HUD
    // (Tab / screenshots without HUD) and when another screen (terminal, menu) has focus.
    internal sealed class OverlayScreen : MyGuiScreenBase
    {
        private const string Blank = "Textures\\GUI\\Blank.dds";
        private static readonly Regex Placeholder = new Regex(@"\{([A-Za-z0-9_.\[\]]+)\}");

        private readonly Func<string, JsonObject, JsonNode> call;
        private readonly Access access;
        private readonly double maxRange;
        public readonly Dictionary<string, OverlayElement> Elements = new Dictionary<string, OverlayElement>();

        public OverlayScreen(Func<string, JsonObject, JsonNode> call, Access access, double maxRange)
            : base(Vector2.Zero)
        {
            this.call = call;
            this.access = access;
            this.maxRange = maxRange;
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
            {
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
                if (e.Kind == "route" && e.Route != null)
                {
                    var here = access.Origin;
                    while (e.Leg < e.Route.Count && Vector3D.Distance(here, RoutePoint(e, e.Leg)) <= (e.Leg == e.Route.Count - 1 ? e.ArriveRadius : 2 * e.ArriveRadius))
                        e.Leg++;
                }
                if (e.Kind == "track" && now >= e.NextScan)
                {
                    e.NextScan = now.AddSeconds(0.5);
                    try
                    {
                        e.Tracked = Scan(e);
                    }
                    catch (Exception ex)
                    {
                        e.BindError = ex.Message;
                    }
                }
            }
            foreach (var e in Elements.Values.Where(e => e.Kind == "route" && e.Route != null && e.Leg >= e.Route.Count).ToList())
            {
                Elements.Remove(e.Id);
                OverlayMethods.RouteArrived(e);
            }
            return base.Update(hasFocus);
        }

        public override bool Draw()
        {
            if (!MyHud.IsVisible || MySession.Static == null || MySector.MainCamera == null || !(MyScreenManager.GetScreenWithFocus() is MyGuiScreenGamePlay))
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
                case "line":
                case "arrow":
                {
                    // From (x, y) to (x2, y2), or to an anchor in the world (clamped to the screen edge when off screen).
                    var from = Px(screen, e.X, e.Y);
                    var to = Px(screen, e.X2, e.Y2);
                    var world = AnchorPosition(e);
                    if (world != null)
                        to = Project(world.Value, screen, out bool onScreen) ?? EdgePoint(world.Value, screen, 40);
                    Line(from, to, e.Color, e.Thickness);
                    if (e.Kind == "arrow")
                        ArrowHead(to, to - from, e.Color, e.Thickness, 8 + 4 * e.Thickness);
                    if (!string.IsNullOrEmpty(e.Text))
                        Text(Format(e, e.Text, null, world != null ? Vector3D.Distance(MySector.MainCamera.Position, world.Value) : (double?)null),
                            (from + to) / 2 - new Vector2(0, 6), e.Scale, e.Color, e.Font, "center", false, bottom: true);
                    break;
                }
                case "circle":
                {
                    var center = Px(screen, e.X, e.Y);
                    var world = AnchorPosition(e);
                    if (world != null)
                    {
                        var p = Project(world.Value, screen, out _);
                        if (p == null)
                            return;
                        center = p.Value;
                    }
                    Circle(center, Math.Max(2, e.W * screen.Height), e.Color, e.Thickness);
                    if (!string.IsNullOrEmpty(e.Text))
                        Text(Format(e, e.Text), center + new Vector2(0, e.W * screen.Height + 4), e.Scale, e.Color, e.Font, "center", false);
                    break;
                }
                case "icon":
                {
                    float size = Math.Max(4, (e.W > 0 ? e.W : 0.04f) * screen.Height);
                    var center = Px(screen, e.X, e.Y);
                    var world = AnchorPosition(e);
                    if (world != null)
                    {
                        var p = Project(world.Value, screen, out _);
                        if (p == null)
                            return;
                        center = p.Value;
                    }
                    MyGuiManager.DrawSpriteBatch(e.Texture, new Rectangle((int)(center.X - size / 2), (int)(center.Y - size / 2), (int)size, (int)size), e.Color, true, true);
                    if (!string.IsNullOrEmpty(e.Text))
                        Text(Format(e, e.Text), center + new Vector2(size / 2 + 6, 0), e.Scale, e.Color, e.Font, "left", true);
                    break;
                }
                case "marker":
                {
                    if (e.AnchorEntity != 0)
                    {
                        if (MyEntities.TryGetEntityById(e.AnchorEntity, out MyEntity entity))
                            Target(entity, e, screen, Format(e, e.Text ?? "", null, Vector3D.Distance(MySector.MainCamera.Position, entity.PositionComp.WorldAABB.Center)));
                    }
                    else if (e.Anchor != null)
                        TargetPoint(e.Anchor.Value, e, screen, Format(e, e.Text ?? "", null, Vector3D.Distance(MySector.MainCamera.Position, e.Anchor.Value)), Math.Max(4, e.W > 0 ? e.W * screen.Height : 10));
                    break;
                }
                case "track":
                {
                    foreach (long id in e.Tracked)
                        if (MyEntities.TryGetEntityById(id, out MyEntity entity) && !entity.MarkedForClose)
                            Target(entity, e, screen, TrackLabel(e, entity));
                    break;
                }
                case "route":
                {
                    if (e.Route == null || e.Leg >= e.Route.Count)
                        return;
                    // The way ahead: faint lines and dots through the coming waypoints, the next one marked.
                    double remaining = Vector3D.Distance(MySector.MainCamera.Position, RoutePoint(e, e.Leg));
                    Vector2? previous = null;
                    for (int i = e.Leg; i < e.Route.Count; i++)
                    {
                        if (i > e.Leg)
                            remaining += Vector3D.Distance(RoutePoint(e, i - 1), RoutePoint(e, i));
                        var p = Project(RoutePoint(e, i), screen, out _);
                        if (p != null && previous != null)
                            Line(previous.Value, p.Value, new Color(e.Color, 0.5f), 1);
                        if (p != null && i > e.Leg)
                            Box(p.Value - new Vector2(3), new Vector2(6), new Color(e.Color, 0.6f));
                        previous = p;
                    }
                    string template = (e.Text ?? "{name} {dist}")
                        .Replace("{name}", e.Name ?? "")
                        .Replace("{step}", $"{e.Leg + 1}/{e.Route.Count}");
                    TargetPoint(RoutePoint(e, e.Leg), e, screen, Format(e, template, null, remaining).Trim(), Math.Max(4, e.W > 0 ? e.W * screen.Height : 10));
                    break;
                }
            }
        }

        private static Vector3D RoutePoint(OverlayElement e, int i)
        {
            if (i == e.Route.Count - 1 && e.AnchorEntity != 0 && MyEntities.TryGetEntityById(e.AnchorEntity, out MyEntity entity))
                return entity.PositionComp.WorldAABB.Center;
            return e.Route[i];
        }

        // --- Things in the world. ---

        private Vector3D? AnchorPosition(OverlayElement e)
        {
            if (e.AnchorEntity != 0)
                return MyEntities.TryGetEntityById(e.AnchorEntity, out MyEntity entity) ? entity.PositionComp.WorldAABB.Center : (Vector3D?)null;
            return e.Anchor;
        }

        // Brackets round an entity's on-screen bounds (or a point marker), or an edge arrow when it's off screen.
        private void Target(MyEntity entity, OverlayElement e, Rectangle screen, string label)
        {
            var center = entity.PositionComp.WorldAABB.Center;
            if (e.Style == "point")
            {
                TargetPoint(center, e, screen, label, Math.Max(4, e.W > 0 ? e.W * screen.Height : 10));
                return;
            }
            var local = entity.PositionComp.LocalAABB;
            var world = entity.PositionComp.WorldMatrixRef;
            Vector2 min = new Vector2(float.MaxValue), max = new Vector2(float.MinValue);
            bool visible = true;
            foreach (var corner in new BoundingBoxD(local.Min, local.Max).GetCorners())
            {
                var p = Project(Vector3D.Transform(corner, world), screen, out bool onScreen);
                if (p == null)
                {
                    visible = false;
                    break;
                }
                min = Vector2.Min(min, p.Value);
                max = Vector2.Max(max, p.Value);
            }
            if (!visible || max.X < screen.Left || min.X > screen.Right || max.Y < screen.Top || min.Y > screen.Bottom)
            {
                if (e.Offscreen)
                    EdgeArrow(center, screen, e.Color, label, e);
                return;
            }
            // Keep tiny or far things visible: at least 16 px.
            var mid = (min + max) / 2;
            var half = Vector2.Max((max - min) / 2 + new Vector2(4), new Vector2(8));
            Brackets(mid - half, half * 2, e.Color, e.Thickness);
            if (label.Length > 0)
                Text(label, new Vector2(mid.X, mid.Y - half.Y - 4), e.Scale, e.Color, e.Font, "center", false, bottom: true);
        }

        private void TargetPoint(Vector3D world, OverlayElement e, Rectangle screen, string label, float r)
        {
            var pos = Project(world, screen, out bool onScreen);
            if (pos == null || !onScreen)
            {
                if (e.Offscreen)
                    EdgeArrow(world, screen, e.Color, label, e);
                return;
            }
            Outline(pos.Value - new Vector2(r), new Vector2(2 * r), e.Color, e.Thickness);
            Box(pos.Value - new Vector2(1.5f), new Vector2(3), e.Color);
            if (label.Length > 0)
                Text(label, new Vector2(pos.Value.X, pos.Value.Y - r - 4), e.Scale, e.Color, e.Font, "center", false, bottom: true);
        }

        // Screen pixel of a world point; null when it's behind the camera. onScreen: inside the screen rectangle.
        private static Vector2? Project(Vector3D world, Rectangle screen, out bool onScreen)
        {
            onScreen = false;
            var camera = MySector.MainCamera.WorldMatrix;
            if (Vector3D.Dot(world - camera.Translation, camera.Forward) <= 0.1)
                return null;
            var ndc = MySector.MainCamera.WorldToScreen(ref world);
            onScreen = Math.Abs(ndc.X) <= 1 && Math.Abs(ndc.Y) <= 1;
            return new Vector2(screen.X + (float)(ndc.X + 1) / 2 * screen.Width, screen.Y + (float)(1 - ndc.Y) / 2 * screen.Height);
        }

        // Where the screen edge meets the direction from the screen centre towards a world point.
        private static Vector2 EdgePoint(Vector3D world, Rectangle screen, float margin)
        {
            var camera = MySector.MainCamera.WorldMatrix;
            var d = world - camera.Translation;
            var dir = new Vector2((float)Vector3D.Dot(d, camera.Right), -(float)Vector3D.Dot(d, camera.Up));
            if (dir.LengthSquared() < 1e-6f)
                dir = new Vector2(0, 1);
            dir.Normalize();
            var center = new Vector2(screen.X + screen.Width / 2f, screen.Y + screen.Height / 2f);
            float k = Math.Min((screen.Width / 2f - margin) / Math.Max(1e-3f, Math.Abs(dir.X)), (screen.Height / 2f - margin) / Math.Max(1e-3f, Math.Abs(dir.Y)));
            return center + dir * k;
        }

        private static void EdgeArrow(Vector3D world, Rectangle screen, Color color, string label, OverlayElement e)
        {
            var tip = EdgePoint(world, screen, 30);
            var center = new Vector2(screen.X + screen.Width / 2f, screen.Y + screen.Height / 2f);
            var dir = Vector2.Normalize(tip - center);
            ArrowHead(tip, dir, color, Math.Max(2, e.Thickness), 22);
            if (label.Length > 0)
            {
                var at = tip - dir * 34;
                string align = dir.X > 0.5f ? "right" : dir.X < -0.5f ? "left" : "center";
                Text(label, at, e.Scale, color, e.Font, align, true);
            }
        }

        // --- Trackers. ---

        private List<long> Scan(OverlayElement e)
        {
            var origin = access.Origin;
            double range = Math.Min(e.Range, maxRange);
            var controlled = MySession.Static.ControlledEntity?.Entity;
            var ownTop = controlled?.GetTopMostParent();
            var own = new HashSet<long>();
            if (ownTop is IMyCubeGrid ownGrid)
                foreach (var g in Access.Construct(ownGrid))
                    own.Add(g.EntityId);
            if (ownTop != null)
                own.Add(ownTop.EntityId);
            var myVelocity = ownTop?.Physics?.LinearVelocity ?? Vector3.Zero;

            var found = new List<(double distance, long id)>();
            foreach (var entity in MyEntities.GetEntities())
            {
                if (entity.MarkedForClose || own.Contains(entity.EntityId) || !Matches(e.TrackWhat, entity))
                    continue;
                var center = entity.PositionComp.WorldAABB.Center;
                double distance = Vector3D.Distance(center, origin);
                if (distance > range)
                    continue;
                if (e.IncomingOnly)
                {
                    var relative = Velocity(entity) - myVelocity;
                    if (relative.Length() < 1 || Vector3D.Dot(relative, origin - center) <= 0)
                        continue;
                }
                found.Add((distance, entity.EntityId));
            }
            return found.OrderBy(f => f.distance).Take(e.MaxTargets).Select(f => f.id).ToList();
        }

        private bool Matches(HashSet<string> what, MyEntity entity)
        {
            switch (entity)
            {
                case MyMeteor _:
                    return what.Contains("meteors");
                case MyMissile _:
                    return what.Contains("missiles");
                case MyCubeGrid grid:
                {
                    // One box per ship: skip subgrids (rotor/piston tops) and tiny debris unless asked for all grids.
                    if (grid.Physics == null || Access.ShipOf(grid).EntityId != grid.EntityId)
                        return false;
                    if (what.Contains("grids"))
                        return true;
                    return what.Contains(Group(access.Relation(grid)));
                }
                case MyCharacter character:
                {
                    if (character.IsDead || character == MySession.Static.LocalCharacter)
                        return false;
                    if (what.Contains("players"))
                        return true;
                    long identity = character.GetPlayerIdentityId();
                    var relation = identity == access.IdentityId ? MyRelationsBetweenPlayerAndBlock.Owner : access.Player.GetRelationTo(identity);
                    return what.Contains(Group(relation));
                }
            }
            return false;
        }

        // enemies / neutrals / friends (faction, allies, our own) / unowned.
        private static string Group(MyRelationsBetweenPlayerAndBlock relation)
        {
            switch (relation)
            {
                case MyRelationsBetweenPlayerAndBlock.Enemies: return "enemies";
                case MyRelationsBetweenPlayerAndBlock.Neutral: return "neutrals";
                case MyRelationsBetweenPlayerAndBlock.NoOwnership: return "unowned";
                default: return "friends";
            }
        }

        private static Vector3 Velocity(MyEntity entity) =>
            entity is MyMissile missile ? missile.LinearVelocity : entity.Physics?.LinearVelocity ?? Vector3.Zero;

        // {name}, {dist}, {speed} m/s, {eta} (time to reach us at the current closing speed).
        private string TrackLabel(OverlayElement e, MyEntity entity)
        {
            string template = e.Text ?? "{name} {dist}";
            var center = entity.PositionComp.WorldAABB.Center;
            var origin = MySector.MainCamera.Position;
            double distance = Vector3D.Distance(center, origin);
            var myVelocity = MySession.Static.ControlledEntity?.Entity?.GetTopMostParent()?.Physics?.LinearVelocity ?? Vector3.Zero;
            var relative = Velocity(entity) - myVelocity;
            double closing = distance > 0 ? Vector3D.Dot(relative, (origin - center) / distance) : 0;
            string name = entity is MyCubeGrid g ? g.DisplayName : entity is MyCharacter c ? c.DisplayNameText : entity is MyMeteor ? "Meteor" : entity is MyMissile ? "Missile" : entity.DisplayName;
            return Placeholder.Replace(template, m =>
            {
                switch (m.Groups[1].Value)
                {
                    case "name": return name ?? "?";
                    case "dist": return Distance(distance);
                    case "speed": return Velocity(entity).Length().ToString("0", CultureInfo.InvariantCulture) + " m/s";
                    case "eta": return closing > 1 ? (distance / closing).ToString("0", CultureInfo.InvariantCulture) + " s" : "";
                    default: return m.Value;
                }
            }).Trim();
        }

        // --- Text and values. ---

        private static Color ColorFor(OverlayElement e, double value) =>
            e.LowBelow is double below && e.LowColor is Color low && !double.IsNaN(value) && value < below ? low : e.Color;

        private static string Distance(double d) =>
            d >= 1000 ? (d / 1000).ToString("0.0", CultureInfo.InvariantCulture) + " km" : d.ToString("0", CultureInfo.InvariantCulture) + " m";

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
                    return Distance(dist.Value);
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

        // --- Drawing primitives (screen pixels). ---

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

        // Corner brackets: a target box that doesn't hide what's inside.
        private static void Brackets(Vector2 tl, Vector2 size, Color color, int t)
        {
            float l = Math.Max(6, Math.Min(size.X, size.Y) / 4);
            var br = tl + size;
            Box(tl, new Vector2(l, t), color);
            Box(tl, new Vector2(t, l), color);
            Box(new Vector2(br.X - l, tl.Y), new Vector2(l, t), color);
            Box(new Vector2(br.X - t, tl.Y), new Vector2(t, l), color);
            Box(new Vector2(tl.X, br.Y - t), new Vector2(l, t), color);
            Box(new Vector2(tl.X, br.Y - l), new Vector2(t, l), color);
            Box(new Vector2(br.X - l, br.Y - t), new Vector2(l, t), color);
            Box(new Vector2(br.X - t, br.Y - l), new Vector2(t, l), color);
        }

        // A rotated strip of the blank texture from a to b.
        private static void Line(Vector2 a, Vector2 b, Color color, int t)
        {
            var d = b - a;
            float length = d.Length();
            if (length < 0.5f)
                return;
            var right = d / length;
            var origin = a;
            var destination = new RectangleF(a.X, a.Y - t / 2f, length, t);
            MyRenderProxy.DrawSpriteExt(Blank, ref destination, null, color, ref right, ref origin, true, true);
        }

        private static void ArrowHead(Vector2 tip, Vector2 direction, Color color, int t, float size)
        {
            if (direction.LengthSquared() < 1e-6f)
                return;
            direction.Normalize();
            var side = new Vector2(-direction.Y, direction.X);
            Line(tip, tip - direction * size + side * size * 0.6f, color, t);
            Line(tip, tip - direction * size - side * size * 0.6f, color, t);
        }

        private static void Circle(Vector2 center, float r, Color color, int t)
        {
            int segments = (int)MathHelper.Clamp(r / 3, 16, 64);
            var previous = center + new Vector2(r, 0);
            for (int i = 1; i <= segments; i++)
            {
                double angle = 2 * Math.PI * i / segments;
                var next = center + new Vector2((float)Math.Cos(angle) * r, (float)Math.Sin(angle) * r);
                Line(previous, next, color, t);
                previous = next;
            }
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
