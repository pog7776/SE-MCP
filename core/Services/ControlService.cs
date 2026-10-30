using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using Sandbox.ModAPI.Interfaces;
using VRageMath;
using IngameTerminalBlock = Sandbox.ModAPI.Ingame.IMyTerminalBlock;

namespace SEMcp.Core.Services
{
    // Changes blocks the way a player would in the terminal: terminal actions and properties, which the game syncs in multiplayer.
    internal sealed class ControlService
    {
        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public ControlService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("block.action", Action);
            dispatcher.Register("block.set_property", SetProperty);
            dispatcher.Register("block.set_name", SetName);
            dispatcher.Register("block.rename_many", RenameMany);
            dispatcher.Register("block.set_custom_data", SetCustomData);
            dispatcher.Register("notify", Notify);
        }

        private void Log(string message) => dispatcher.Context.Log(message);

        // Shooting through generic actions/properties obeys the same switch as weapon.fire.
        private void RequireWeapons(IMyTerminalBlock block, string id)
        {
            if (block is IMyUserControllableGun && id.StartsWith("Shoot", StringComparison.OrdinalIgnoreCase) && !dispatcher.Context.AllowWeapons)
                throw new RpcException(ErrorKind.FeatureDisabled, "Firing weapons is turned off (allowWeapons in config.json)");
        }

        // One block (blockId) or every usable block in a group (gridId + group).
        private List<IMyTerminalBlock> Targets(JsonObject p, out List<string> skipped)
        {
            skipped = new List<string>();
            if (p["blockId"] != null)
                return new List<IMyTerminalBlock> { access.Block(p.Id("blockId")) };

            string groupName = p.String("group");
            if (groupName == null || p["gridId"] == null)
                throw new RpcException(ErrorKind.InvalidParams, "Give blockId, or gridId + group");

            var grid = access.Grid(p.Id("gridId"));
            var group = BlockService.FindGroup(grid, groupName)
                ?? throw new RpcException(ErrorKind.NotFound, $"No block group '{groupName}' on '{Dto.GridName(grid)}'");
            var members = new List<IngameTerminalBlock>();
            group.GetBlocks(members);

            var targets = new List<IMyTerminalBlock>();
            foreach (var block in members.OfType<IMyTerminalBlock>())
            {
                if (access.CanUse(block))
                    targets.Add(block);
                else
                    skipped.Add($"{block.CustomName}: no access");
            }
            return targets;
        }

        private object Action(JsonObject p)
        {
            dispatcher.RequireWritable();
            string actionId = p.String("action", required: true);
            var targets = Targets(p, out var skipped);

            var applied = new List<string>();
            foreach (var block in targets)
            {
                RequireWeapons(block, actionId);
                var action = block.GetActionWithName(actionId);
                if (action == null)
                {
                    if (targets.Count == 1)
                        throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' has no action '{actionId}'. Available: {ActionIds(block)}");
                    skipped.Add($"{block.CustomName}: no action '{actionId}'");
                    continue;
                }
                if (!action.IsEnabled(block))
                {
                    skipped.Add($"{block.CustomName}: action '{actionId}' is not available right now");
                    continue;
                }
                action.Apply(block);
                applied.Add(block.CustomName);
                Log($"action {actionId} on '{block.CustomName}' ({block.EntityId})");
            }

            return new { action = actionId, applied, skipped };
        }

        private static string ActionIds(IMyTerminalBlock block)
        {
            var actions = new List<ITerminalAction>();
            block.GetActions(actions);
            return string.Join(", ", actions.Select(a => a.Id));
        }

        private object SetProperty(JsonObject p)
        {
            dispatcher.RequireWritable();
            string propertyId = p.String("property", required: true);
            var value = p["value"] ?? throw new RpcException(ErrorKind.InvalidParams, "Missing 'value'");
            var targets = Targets(p, out var skipped);

            var changed = new List<object>();
            foreach (var block in targets)
            {
                RequireWeapons(block, propertyId);
                var property = block.GetProperty(propertyId);
                if (property == null)
                {
                    if (targets.Count == 1)
                        throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' has no property '{propertyId}'. Available: {PropertyIds(block)}");
                    skipped.Add($"{block.CustomName}: no property '{propertyId}'");
                    continue;
                }

                object before = Read(block, property);
                Write(block, property, value);
                object after = Read(block, property);
                changed.Add(new { block = block.CustomName, before, after });
                Log($"set {propertyId} on '{block.CustomName}' ({block.EntityId}): {before} -> {after}");
            }

            return new { property = propertyId, changed, skipped };
        }

        private static string PropertyIds(IMyTerminalBlock block)
        {
            var properties = new List<ITerminalProperty>();
            block.GetProperties(properties);
            return string.Join(", ", properties.Select(prop => $"{prop.Id} ({prop.TypeName})"));
        }

        private static object Read(IMyTerminalBlock block, ITerminalProperty property)
        {
            switch (property.TypeName)
            {
                case "Boolean": return property.As<bool>().GetValue(block);
                case "Single": return Math.Round(property.As<float>().GetValue(block), 3);
                case "Int64": return property.As<long>().GetValue(block);
                case "StringBuilder": return property.As<StringBuilder>().GetValue(block)?.ToString();
                case "String": return property.As<string>().GetValue(block);
                case "Color":
                    var c = property.As<Color>().GetValue(block);
                    return $"#{c.R:X2}{c.G:X2}{c.B:X2}{c.A:X2}";
                default: return null;
            }
        }

        private static void Write(IMyTerminalBlock block, ITerminalProperty property, JsonNode value)
        {
            try
            {
                switch (property.TypeName)
                {
                    case "Boolean":
                        property.As<bool>().SetValue(block, ToBool(value));
                        return;
                    case "Single":
                        var single = property.As<float>();
                        float number = (float)ToDouble(value);
                        // The terminal slider clamps too; do it here so the reply shows the real value.
                        float min = single.GetMinimum(block), max = single.GetMaximum(block);
                        if (min < max)
                            number = MathHelper.Clamp(number, min, max);
                        single.SetValue(block, number);
                        return;
                    case "Int64":
                        property.As<long>().SetValue(block, (long)ToDouble(value));
                        return;
                    case "StringBuilder":
                        property.As<StringBuilder>().SetValue(block, new StringBuilder(ToText(value)));
                        return;
                    case "String":
                        property.As<string>().SetValue(block, ToText(value));
                        return;
                    case "Color":
                        property.As<Color>().SetValue(block, ToColor(value));
                        return;
                    default:
                        throw new RpcException(ErrorKind.InvalidParams, $"Property '{property.Id}' has type {property.TypeName}, which can't be set yet");
                }
            }
            catch (FormatException e)
            {
                throw new RpcException(ErrorKind.InvalidParams, $"Bad value for '{property.Id}' ({property.TypeName}): {e.Message}");
            }
        }

        private static bool ToBool(JsonNode value)
        {
            if (value.GetValueKind() == JsonValueKind.True) return true;
            if (value.GetValueKind() == JsonValueKind.False) return false;
            switch (ToText(value).Trim().ToLowerInvariant())
            {
                case "true": case "on": case "yes": case "1": return true;
                case "false": case "off": case "no": case "0": return false;
                default: throw new FormatException("expected true/false or on/off");
            }
        }

        private static double ToDouble(JsonNode value)
        {
            if (value.GetValueKind() == JsonValueKind.Number)
                return value.GetValue<double>();
            if (double.TryParse(ToText(value), NumberStyles.Float, CultureInfo.InvariantCulture, out double parsed))
                return parsed;
            throw new FormatException("expected a number");
        }

        private static string ToText(JsonNode value) =>
            value.GetValueKind() == JsonValueKind.String ? value.GetValue<string>() : value.ToJsonString();

        // "#RRGGBB", "#RRGGBBAA" or {r, g, b, a?} with 0-255 channels.
        internal static Color ToColor(JsonNode value)
        {
            if (value is JsonObject o)
            {
                int Channel(string name, int fallback) => o[name] != null ? (int)ToDouble(o[name]) : fallback;
                return new Color(Channel("r", 0), Channel("g", 0), Channel("b", 0), Channel("a", 255));
            }

            string hex = ToText(value).Trim().TrimStart('#');
            if (hex.Length != 6 && hex.Length != 8)
                throw new FormatException("expected #RRGGBB, #RRGGBBAA or {r,g,b}");
            byte Byte(int i) => byte.Parse(hex.Substring(i, 2), NumberStyles.HexNumber);
            return new Color(Byte(0), Byte(2), Byte(4), hex.Length == 8 ? Byte(6) : (byte)255);
        }

        private object SetName(JsonObject p)
        {
            dispatcher.RequireWritable();
            var block = access.Block(p.Id("blockId"));
            string name = p.String("value", required: true);
            if (string.IsNullOrWhiteSpace(name))
                throw new RpcException(ErrorKind.InvalidParams, "Name can't be empty");

            string before = block.CustomName;
            block.CustomName = name;
            Log($"rename ({block.EntityId}): '{before}' -> '{name}'");
            return new { before, after = block.CustomName };
        }

        // Many renames in one game-thread call, by id, so a bulk rename is one round trip and never fuzzy.
        private object RenameMany(JsonObject p)
        {
            dispatcher.RequireWritable();
            var list = p["renames"] as JsonArray ?? throw new RpcException(ErrorKind.InvalidParams, "Give renames: [{blockId, name}]");
            var renamed = new List<object>();
            var failed = new List<object>();
            foreach (var node in list)
            {
                var item = node as JsonObject;
                string id = item?["blockId"]?.ToString();
                try
                {
                    if (item == null)
                        throw new RpcException(ErrorKind.InvalidParams, "Each rename must be {blockId, name}");
                    var block = access.Block(item.Id("blockId"));
                    string name = item.String("name", required: true);
                    if (string.IsNullOrWhiteSpace(name))
                        throw new RpcException(ErrorKind.InvalidParams, "Name can't be empty");
                    string before = block.CustomName;
                    block.CustomName = name;
                    renamed.Add(new { id = block.EntityId.ToString(), before, after = block.CustomName });
                }
                catch (RpcException e)
                {
                    failed.Add(new { id, error = e.Message });
                }
            }
            Log($"rename_many: {renamed.Count} renamed, {failed.Count} failed");
            return new { renamed, failed };
        }

        private object SetCustomData(JsonObject p)
        {
            dispatcher.RequireWritable();
            var block = access.Block(p.Id("blockId"));
            string data = p.String("value") ?? "";
            string before = block.CustomData;
            block.CustomData = data;
            Log($"custom data on '{block.CustomName}' ({block.EntityId}): {before.Length} -> {data.Length} chars");
            return new { block = block.CustomName, beforeLength = before.Length, afterLength = data.Length };
        }

        // Talk to the player in-game: a HUD notification (default) or a chat line.
        private object Notify(JsonObject p)
        {
            dispatcher.RequireSession();
            dispatcher.RequireWritable();
            string text = p.String("text", required: true);
            string kind = p.String("kind") ?? "hud";
            if (kind == "chat")
            {
                MyAPIGateway.Utilities.ShowMessage(p.String("sender") ?? "Claude", text);
            }
            else
            {
                int duration = (int)Math.Max(500, Math.Min(p.Number("durationMs", 5000), 60000));
                MyAPIGateway.Utilities.ShowNotification(text, duration, p.String("font") ?? "White");
            }
            return new { shown = true, kind };
        }
    }
}
