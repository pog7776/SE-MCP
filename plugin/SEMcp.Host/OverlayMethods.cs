using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.Graphics.GUI;
using SEMcp.Core;
using VRageMath;

namespace SEMcp.Host
{
    // HUD overlay: text, boxes, bars and world markers the assistant draws for the local player only.
    // Elements can be bound to a read-only method (ship.hud...) that is re-read every few seconds, so a fuel bar
    // stays live without the assistant polling.
    internal static class OverlayMethods
    {
        // Read-only methods an element may bind to.
        private static readonly HashSet<string> Bindable = new HashSet<string>
        {
            "ship.hud", "grid.get", "grid.motion", "character.status", "player.context", "inventory.get", "production.get",
            "dock.get", "weapons.get", "sun.info", "environment.get", "sensors.get", "damage.get", "radio.antennas", "block.get",
        };

        private static readonly string[] Kinds = { "text", "rect", "bar", "line", "arrow", "circle", "icon", "marker", "track" };

        // What a tracker can follow.
        private static readonly string[] Trackable = { "meteors", "missiles", "enemies", "neutrals", "friends", "unowned", "grids", "players" };

        // Short icon names for game textures; any "Textures\..." path also works.
        private const string Hud = "Textures\\GUI\\Icons\\HUD 2017\\";
        private static readonly Dictionary<string, string> Icons = new Dictionary<string, string>
        {
            ["battery"] = Hud + "Battery.png", ["energy"] = Hud + "EnergyIcon.png", ["hydrogen"] = Hud + "HydrogenIcon.png",
            ["oxygen"] = Hud + "OxygenIcon.png", ["health"] = Hud + "HealthIcon.png", ["health-low"] = Hud + "HealthLow.png",
            ["jetpack"] = Hud + "Jetpack.png", ["dampeners"] = Hud + "DampenersOn.png", ["handbrake"] = Hud + "Handbrake.png",
            ["light"] = Hud + "Light.png", ["mass"] = Hud + "Mass.png", ["radiation"] = Hud + "RadiationIcon.png",
            ["suffocating"] = Hud + "Suffocating.png", ["temperature"] = Hud + "OusideTemp.png", ["food"] = Hud + "FoodIcon.png",
            ["gravity"] = Hud + "GravityIndicator.png", ["broadcast"] = Hud + "BroadcastingOn.png", ["power"] = Hud + "GridPowerOn.png",
            ["speed"] = Hud + "Speedometer.png", ["inventory"] = Hud + "InventoryCapacity.png", ["chat"] = Hud + "Chat.png",
            ["lock"] = "Textures\\GUI\\Icons\\Lock.png", ["camera"] = "Textures\\GUI\\Icons\\Camera.dds",
            ["enemy"] = "Textures\\HUD\\marker_enemy.dds", ["friendly"] = "Textures\\HUD\\marker_friendly.dds",
            ["neutral"] = "Textures\\HUD\\marker_neutral.dds", ["gps"] = "Textures\\HUD\\marker_gps.dds",
            ["alert"] = "Textures\\HUD\\marker_alert.dds", ["self"] = "Textures\\HUD\\marker_self.dds",
            ["square"] = "Textures\\GUI\\Blank.dds",
        };

        private static OverlayScreen screen;
        private static Dispatcher dispatcher;

        public static void Register(Dispatcher d)
        {
            dispatcher = d;
            var access = new Access(d);
            d.Register("overlay.set", p => Set(access, p));
            d.Register("overlay.remove", Remove);
            d.Register("overlay.clear", p => Clear());
            d.Register("overlay.list", p => List());
        }

        // Re-adds the screen if the game closed it (world unload/reload) while elements remain.
        private static OverlayScreen Screen()
        {
            if (screen == null || !screen.IsOpen)
            {
                var old = screen?.Elements;
                screen = new OverlayScreen((method, parameters) => Rpc.ToNode(dispatcher.Dispatch(method, parameters)),
                    new Access(dispatcher), dispatcher.Context.MaxRangeMeters);
                if (old != null)
                    foreach (var pair in old)
                        screen.Elements[pair.Key] = pair.Value;
                MyGuiSandbox.AddScreen(screen);
            }
            return screen;
        }

        private static object Set(Access access, JsonObject p)
        {
            dispatcher.RequireSession();
            var list = p["elements"] as JsonArray ?? throw new RpcException(ErrorKind.InvalidParams, "Give elements: [{id, kind, ...}]");
            var s = Screen();
            var set = new List<string>();
            foreach (var node in list)
            {
                var o = node as JsonObject ?? throw new RpcException(ErrorKind.InvalidParams, "Each element is an object");
                string id = o.String("id", required: true);
                s.Elements.TryGetValue(id, out var e);
                e = e ?? new OverlayElement { Id = id };
                Apply(access, e, o);
                if (!Kinds.Contains(e.Kind))
                    throw new RpcException(ErrorKind.InvalidParams, $"Element '{id}': kind must be one of {string.Join(", ", Kinds)}");
                if (e.Kind == "icon" && e.Texture == null)
                    throw new RpcException(ErrorKind.InvalidParams, $"Element '{id}': an icon needs icon (a name or Textures\\... path)");
                if (e.Kind == "track" && e.TrackWhat.Count == 0)
                    throw new RpcException(ErrorKind.InvalidParams, $"Element '{id}': a tracker needs track ({string.Join(", ", Trackable)})");
                if (e.Kind == "marker" && e.AnchorEntity == 0 && e.Anchor == null)
                    throw new RpcException(ErrorKind.InvalidParams, $"Element '{id}': a marker needs blockId, gridId or position");
                s.Elements[id] = e;
                set.Add(id);
            }
            return new { set, total = s.Elements.Count };
        }

        // Only the fields given change, so an existing element can be moved or recoloured by id.
        private static void Apply(Access access, OverlayElement e, JsonObject o)
        {
            float F(string name, float current) => o[name] != null ? (float)o.Number(name, current) : current;
            e.Kind = o.String("kind") ?? e.Kind;
            e.X = MathHelper.Clamp(F("x", e.X), 0, 1);
            e.Y = MathHelper.Clamp(F("y", e.Y), 0, 1);
            e.W = MathHelper.Clamp(F("w", e.W), 0, 1);
            e.H = MathHelper.Clamp(F("h", e.H), 0, 1);
            e.X2 = MathHelper.Clamp(F("x2", e.X2), 0, 1);
            e.Y2 = MathHelper.Clamp(F("y2", e.Y2), 0, 1);
            e.Style = o.String("style") ?? e.Style;
            e.Offscreen = o.Bool("offscreen", e.Offscreen);
            if (o["icon"] != null)
            {
                string icon = o.String("icon");
                e.Texture = Icons.TryGetValue(icon.ToLowerInvariant(), out string path) ? path
                    : icon.StartsWith("Textures\\", StringComparison.OrdinalIgnoreCase) || icon.StartsWith("Textures/", StringComparison.OrdinalIgnoreCase) ? icon.Replace('/', '\\')
                    : throw new RpcException(ErrorKind.InvalidParams, $"Unknown icon '{icon}'. Names: {string.Join(", ", Icons.Keys)}, or a Textures\\... path");
            }
            if (o["track"] != null)
            {
                var what = o["track"] is JsonArray a ? a.Select(n => n.ToString().ToLowerInvariant()).ToList() : new List<string> { o.String("track").ToLowerInvariant() };
                var unknown = what.Where(w => !Trackable.Contains(w)).ToList();
                if (unknown.Count > 0)
                    throw new RpcException(ErrorKind.InvalidParams, $"Can't track {string.Join(", ", unknown)}. Choose from: {string.Join(", ", Trackable)}");
                e.TrackWhat = new HashSet<string>(what);
                e.NextScan = DateTime.MinValue;
            }
            e.Range = MathHelper.Clamp(o.Number("range", e.Range), 1, 100000);
            e.IncomingOnly = o.Bool("incoming", e.IncomingOnly);
            e.MaxTargets = (int)MathHelper.Clamp(o.Number("maxTargets", e.MaxTargets), 1, 200);
            if (o.ContainsKey("text"))
                e.Text = o.String("text");
            if (o["color"] != null)
                e.Color = ColorParam.Parse(o["color"]);
            if (o.ContainsKey("background"))
                e.Background = o["background"] == null ? (Color?)null : ColorParam.Parse(o["background"]);
            if (o["lowColor"] != null)
                e.LowColor = ColorParam.Parse(o["lowColor"]);
            if (o.ContainsKey("lowBelow"))
                e.LowBelow = o["lowBelow"] == null ? (double?)null : o.Number("lowBelow", 0);
            e.Scale = MathHelper.Clamp(F("scale", e.Scale), 0.2f, 4);
            e.Align = o.String("align") ?? e.Align;
            e.Font = o.String("font") ?? e.Font;
            e.Fill = o.Bool("fill", e.Fill);
            e.Thickness = (int)MathHelper.Clamp(F("thickness", e.Thickness), 1, 20);
            e.Value = o.Number("value", e.Value);
            e.Max = o.Number("max", e.Max);
            if (o.ContainsKey("valuePath"))
                e.ValuePath = o.String("valuePath");
            if (o.ContainsKey("maxPath"))
                e.MaxPath = o.String("maxPath");
            if (o["ttl"] != null)
                e.Expires = o.Number("ttl", 0) > 0 ? DateTime.UtcNow.AddSeconds(o.Number("ttl", 0)) : (DateTime?)null;

            if (o["position"] is JsonArray pos && pos.Count == 3)
            {
                e.Anchor = new Vector3D((double)pos[0], (double)pos[1], (double)pos[2]);
                e.AnchorEntity = 0;
            }
            else if (o["blockId"] != null)
            {
                e.AnchorEntity = access.Block(o.Id("blockId")).EntityId;
                e.Anchor = null;
            }
            else if (o["gridId"] != null)
            {
                e.AnchorEntity = access.Grid(o.Id("gridId")).EntityId;
                e.Anchor = null;
            }

            if (o.ContainsKey("bind"))
            {
                if (o["bind"] is JsonObject bind)
                {
                    string method = bind.String("method", required: true);
                    if (!Bindable.Contains(method))
                        throw new RpcException(ErrorKind.InvalidParams, $"Can't bind to '{method}'. Bindable: {string.Join(", ", Bindable)}");
                    e.BindMethod = method;
                    e.BindParams = bind["params"]?.DeepClone() as JsonObject ?? new JsonObject();
                    e.BindEvery = MathHelper.Clamp(bind.Number("every", 1), 0.25, 60);
                    // Fail now rather than drawing "?" forever.
                    e.Bound = Rpc.ToNode(dispatcher.Dispatch(method, e.BindParams));
                    e.BindError = null;
                    e.NextBind = DateTime.UtcNow.AddSeconds(e.BindEvery);
                }
                else
                {
                    e.BindMethod = null;
                    e.Bound = null;
                }
            }
        }

        private static object Remove(JsonObject p)
        {
            var ids = (p["ids"] as JsonArray ?? throw new RpcException(ErrorKind.InvalidParams, "Give ids")).Select(n => n.ToString()).ToList();
            int removed = screen == null ? 0 : ids.Count(id => screen.Elements.Remove(id));
            return new { removed, total = screen?.Elements.Count ?? 0 };
        }

        private static object Clear()
        {
            int removed = screen?.Elements.Count ?? 0;
            Close();
            return new { removed };
        }

        private static object List() => new
        {
            elements = (screen?.Elements.Values ?? Enumerable.Empty<OverlayElement>()).Select(e => new
            {
                id = e.Id,
                kind = e.Kind,
                x = e.X, y = e.Y, w = e.W, h = e.H,
                text = e.Text,
                bind = e.BindMethod,
                bindError = e.BindError,
                anchored = e.AnchorEntity != 0 || e.Anchor != null,
                track = e.Kind == "track" ? e.TrackWhat.ToList() : null,
                tracking = e.Kind == "track" ? e.Tracked.Count : (int?)null,
                expiresIn = e.Expires is DateTime t ? Math.Round((t - DateTime.UtcNow).TotalSeconds) : (double?)null,
            }).ToList(),
        };

        // Host stop (hot reload, world unload): the screen's type belongs to this assembly, so take it off the stack.
        public static void Close()
        {
            try
            {
                if (screen != null && screen.IsOpen)
                    screen.CloseScreen();
            }
            catch (Exception)
            {
            }
            screen = null;
        }
    }
}
