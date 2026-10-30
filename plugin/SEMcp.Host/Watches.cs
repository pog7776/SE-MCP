using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using Sandbox.Game.World;
using SEMcp.Core;

namespace SEMcp.Host
{
    // Watches: the standard way for the assistant to ask for a callback. Each one re-reads a read-only method
    // (the same list the HUD overlay can bind to), picks a value by path and tests it. When the test becomes true
    // it publishes a "watch" event on the hub; it re-arms once the test is false again.
    // Kept in watches.json next to config.json, so they survive reloads and restarts.
    internal sealed class Watches
    {
        private const double FrameBudgetMs = 1;
        private static readonly string[] Ops = { "<", "<=", ">", ">=", "==", "!=", "changed" };

        private sealed class Watch
        {
            public string Id;
            public string Label;
            public string Method;
            public JsonObject Params;
            public string Path;
            public string Op;
            public JsonNode Value;
            public string Note;
            public double Every = 2;
            public double Cooldown = 120;
            public bool Once;

            // Runtime state, not saved.
            public double DueAt;
            public bool Fired;
            public double LastFiredAt = double.NegativeInfinity;
            public JsonNode LastValue;
            public bool HasLastValue;
            public string Error;
            public int FireCount;

            public JsonObject Save() => new JsonObject
            {
                ["id"] = Id, ["label"] = Label, ["method"] = Method, ["params"] = Params?.DeepClone(), ["path"] = Path,
                ["op"] = Op, ["value"] = Value?.DeepClone(), ["note"] = Note, ["every"] = Every, ["cooldown"] = Cooldown,
                ["once"] = Once,
            };

            public JsonObject Describe()
            {
                var o = Save();
                o["current"] = LastValue?.DeepClone();
                o["active"] = Fired;
                o["fired"] = FireCount;
                o["error"] = Error;
                return o;
            }
        }

        private readonly EventHub hub;
        private readonly Dispatcher dispatcher;
        private readonly Action<string> log;
        private readonly Dictionary<string, Watch> watches = new Dictionary<string, Watch>(StringComparer.OrdinalIgnoreCase);
        private readonly Stopwatch clock = Stopwatch.StartNew();

        public Watches(EventHub hub, Dispatcher dispatcher, Action<string> log)
        {
            this.hub = hub;
            this.dispatcher = dispatcher;
            this.log = log;
        }

        public static string FilePath => System.IO.Path.Combine(System.IO.Path.GetDirectoryName(Config.FilePath), "watches.json");

        public void Register()
        {
            dispatcher.Register("watch.set", Set);
            dispatcher.Register("watch.remove", Remove);
            dispatcher.Register("watch.list", p => List());
        }

        // The suit basics, so the AI speaks up about them without being asked first.
        private static IEnumerable<JsonObject> Defaults()
        {
            JsonObject Low(string id, string label, string path, double below, string note) => new JsonObject
            {
                ["id"] = id, ["label"] = label, ["method"] = "character.status", ["path"] = path, ["op"] = "<", ["value"] = below,
                ["note"] = note,
            };
            yield return Low("suit-oxygen", "Suit oxygen low", "oxygen", 20, "Warn the player; suggest a refill or an air vent if one is near.");
            yield return Low("suit-hydrogen", "Suit hydrogen low", "hydrogen", 15, "Warn the player, especially if they are flying with the jetpack.");
            yield return Low("suit-energy", "Suit energy low", "energy", 10, "Warn the player; suggest a medical room, survival kit or seat to recharge.");
            yield return Low("suit-health", "Health low", "health", 40, "Tell the player they are hurt and where the nearest medical room is, if you can find one.");
        }

        public void Load()
        {
            try
            {
                var list = File.Exists(FilePath)
                    ? JsonNode.Parse(File.ReadAllText(FilePath)) as JsonArray
                    : new JsonArray(Defaults().Cast<JsonNode>().ToArray());
                foreach (var node in list ?? new JsonArray())
                {
                    try
                    {
                        var w = Parse(node as JsonObject, null);
                        watches[w.Id] = w;
                    }
                    catch (Exception e)
                    {
                        log($"Skipped a watch in watches.json: {e.Message}");
                    }
                }
                if (!File.Exists(FilePath))
                    Save();
            }
            catch (Exception e)
            {
                log($"Couldn't read watches.json: {e.Message}");
            }
        }

        private void Save()
        {
            var list = new JsonArray(watches.Values.Select(w => (JsonNode)w.Save()).ToArray());
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(FilePath));
            File.WriteAllText(FilePath, list.ToJsonString(new JsonSerializerOptions { WriteIndented = true }));
        }

        // Builds a watch from params; fields not given keep their values from `existing` (upsert).
        private static Watch Parse(JsonObject p, Watch existing)
        {
            if (p == null)
                throw new RpcException(ErrorKind.InvalidParams, "A watch must be an object");
            var w = new Watch
            {
                Id = p.String("id") ?? existing?.Id ?? throw new RpcException(ErrorKind.InvalidParams, "Give the watch an id"),
                Label = p.String("label") ?? existing?.Label,
                Method = p.String("method") ?? existing?.Method,
                Params = (p["params"] as JsonObject)?.DeepClone() as JsonObject ?? existing?.Params,
                Path = p.String("path") ?? existing?.Path,
                Op = p.String("op") ?? existing?.Op ?? "changed",
                Value = p.ContainsKey("value") ? p["value"]?.DeepClone() : existing?.Value?.DeepClone(),
                Note = p.String("note") ?? existing?.Note,
                Every = Math.Max(0.5, p.Number("every", existing?.Every ?? 2)),
                Cooldown = Math.Max(0, p.Number("cooldown", existing?.Cooldown ?? 120)),
                Once = p.Bool("once", existing?.Once ?? false),
            };
            if (string.IsNullOrEmpty(w.Method) || !OverlayMethods.Bindable.Contains(w.Method))
                throw new RpcException(ErrorKind.InvalidParams,
                    $"method must be one of: {string.Join(", ", OverlayMethods.Bindable.OrderBy(m => m))}");
            if (string.IsNullOrEmpty(w.Path))
                throw new RpcException(ErrorKind.InvalidParams, "Give a path into the method's result, e.g. 'hydrogen.fill'");
            if (!Ops.Contains(w.Op))
                throw new RpcException(ErrorKind.InvalidParams, $"op must be one of: {string.Join(" ", Ops)}");
            if (w.Op != "changed" && w.Value == null)
                throw new RpcException(ErrorKind.InvalidParams, $"op '{w.Op}' needs a value");
            return w;
        }

        private object Set(JsonObject p)
        {
            watches.TryGetValue(p.String("id") ?? "", out var existing);
            var w = Parse(p, existing);
            if (existing != null)
            {
                // Keep the edge state so editing a note doesn't re-fire a watch that is already active.
                w.Fired = existing.Fired;
                w.LastFiredAt = existing.LastFiredAt;
                w.FireCount = existing.FireCount;
            }
            // Read it once now, so a bad method, params or path shows up here rather than silently later.
            var current = Read(w, out string error);
            if (error != null && !error.StartsWith("No world"))
                throw new RpcException(ErrorKind.InvalidParams, error);
            watches[w.Id] = w;
            Save();
            return new { watch = w.Describe(), current, replaced = existing != null };
        }

        private object Remove(JsonObject p)
        {
            string id = p.String("id", required: true);
            if (id == "*")
            {
                int count = watches.Count;
                watches.Clear();
                Save();
                return new { removed = count };
            }
            bool removed = watches.Remove(id);
            if (removed)
                Save();
            return new { removed = removed ? 1 : 0 };
        }

        private object List() => new { watches = watches.Values.Select(w => w.Describe()).ToArray(), file = FilePath };

        private JsonNode Read(Watch w, out string error)
        {
            error = null;
            if (MySession.Static?.Ready != true)
            {
                error = "No world is loaded";
                return null;
            }
            try
            {
                var result = Rpc.ToNode(dispatcher.Dispatch(w.Method, (JsonObject)w.Params?.DeepClone() ?? new JsonObject()));
                var value = Walk(result, w.Path);
                if (value == null)
                    error = $"'{w.Path}' is missing or empty in the {w.Method} result";
                return value;
            }
            catch (RpcException e)
            {
                error = e.Message;
            }
            catch (Exception e)
            {
                error = e.Message;
            }
            return null;
        }

        // "hydrogen.fill", "panels.0.exposure", "docked.length"
        public static JsonNode Walk(JsonNode node, string path)
        {
            foreach (string part in path.Split('.'))
            {
                if (node is JsonObject o)
                {
                    var key = o.Select(kv => kv.Key).FirstOrDefault(k => string.Equals(k, part, StringComparison.OrdinalIgnoreCase));
                    node = key == null ? null : o[key];
                }
                else if (node is JsonArray a)
                {
                    if (part == "length" || part == "count")
                        node = a.Count;
                    else
                        node = int.TryParse(part, out int i) && i >= 0 && i < a.Count ? a[i] : null;
                }
                else
                {
                    return null;
                }
                if (node == null)
                    return null;
            }
            return node;
        }

        public static bool Test(JsonNode value, string op, JsonNode target)
        {
            if (TryNumber(value, out double a) && TryNumber(target, out double b))
            {
                switch (op)
                {
                    case "<": return a < b;
                    case "<=": return a <= b;
                    case ">": return a > b;
                    case ">=": return a >= b;
                    case "==": return Math.Abs(a - b) < 1e-9;
                    case "!=": return Math.Abs(a - b) >= 1e-9;
                }
            }
            string sa = Text(value), sb = Text(target);
            switch (op)
            {
                case "==": return string.Equals(sa, sb, StringComparison.OrdinalIgnoreCase);
                case "!=": return !string.Equals(sa, sb, StringComparison.OrdinalIgnoreCase);
            }
            return false;
        }

        private static bool TryNumber(JsonNode node, out double number)
        {
            number = 0;
            if (node is JsonValue v)
            {
                if (v.TryGetValue(out double d)) { number = d; return true; }
                if (v.TryGetValue(out long l)) { number = l; return true; }
                if (v.TryGetValue(out int i)) { number = i; return true; }
                if (v.TryGetValue(out string s))
                    return double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out number);
            }
            return false;
        }

        private static string Text(JsonNode node)
        {
            if (node == null)
                return "null";
            if (node is JsonValue v && v.TryGetValue(out string s))
                return s;
            return node.ToJsonString();
        }

        private static string Show(JsonNode node)
        {
            if (TryNumber(node, out double d) && !(node is JsonValue v && v.TryGetValue(out string _)))
                return Math.Round(d, 2).ToString(CultureInfo.InvariantCulture);
            return Text(node);
        }

        // Game thread, every frame. Evaluates the watches that are due, within a small time budget.
        public void Tick()
        {
            if (watches.Count == 0 || MySession.Static?.Ready != true)
                return;
            double now = clock.Elapsed.TotalSeconds;
            var budget = Stopwatch.StartNew();
            foreach (var w in watches.Values.Where(x => x.DueAt <= now).OrderBy(x => x.DueAt).ToList())
            {
                w.DueAt = now + w.Every;
                Evaluate(w, now);
                if (budget.Elapsed.TotalMilliseconds >= FrameBudgetMs)
                    break;
            }
        }

        private void Evaluate(Watch w, double now)
        {
            var value = Read(w, out string error);
            w.Error = error;
            if (error != null)
                return;

            bool hit;
            if (w.Op == "changed")
            {
                hit = w.HasLastValue && Text(value) != Text(w.LastValue);
                w.Fired = false;
            }
            else
            {
                hit = Test(value, w.Op, w.Value);
            }
            var previous = w.LastValue;
            w.LastValue = value?.DeepClone();
            w.HasLastValue = true;

            if (!hit)
            {
                w.Fired = false;
                return;
            }
            if (w.Fired || now - w.LastFiredAt < w.Cooldown)
                return;

            w.Fired = true;
            w.LastFiredAt = now;
            w.FireCount++;
            string label = w.Label ?? w.Id;
            string text = w.Op == "changed"
                ? $"{label}: {w.Path} changed from {Show(previous)} to {Show(value)}"
                : $"{label}: {w.Path} is {Show(value)} ({w.Op} {Show(w.Value)})";
            hub.Publish("watch", w.Method, text, new JsonObject
            {
                ["watch"] = w.Id, ["label"] = w.Label, ["method"] = w.Method, ["params"] = w.Params?.DeepClone(), ["path"] = w.Path,
                ["op"] = w.Op, ["threshold"] = w.Value?.DeepClone(), ["value"] = value?.DeepClone(),
                ["previous"] = previous?.DeepClone(), ["note"] = w.Note,
            });
            if (w.Once)
            {
                watches.Remove(w.Id);
                Save();
            }
        }
    }
}
