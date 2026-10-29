using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.Json.Nodes;
using Sandbox.Common.ObjectBuilders;
using Sandbox.Game.Entities;
using Sandbox.Game.Entities.Cube;
using Sandbox.Game.GameSystems;
using Sandbox.Game.Screens.Helpers;
using Sandbox.Game.World;
using SEMcp.Core;
using VRage.Game;
using VRage.Game.ModAPI;

namespace SEMcp.Host
{
    // Toolbars (ship controllers, button panels, timers, sensors, ... and the player's own) and block groups.
    // Both are game internals: ModAPI can't edit either. Changes go through the same calls the game's own screens
    // use, which sync in multiplayer (toolbar item changes and MyCubeGrid.ModifyGroup are server requests).
    internal static class ToolbarMethods
    {
        private const int SlotsPerPage = 9;

        public static void Register(Dispatcher dispatcher)
        {
            var access = new Access(dispatcher);
            dispatcher.Register("toolbar.get", p => Get(access, dispatcher, p));
            dispatcher.Register("toolbar.set", p => Set(access, dispatcher, p));
            dispatcher.Register("group.set", p => SetGroup(access, dispatcher, p));
        }

        // The toolbar of a block (any block with a Toolbar property), or the player's own when no blockId is given.
        private static MyToolbar Toolbar(Access access, Dispatcher dispatcher, JsonObject p, out string owner)
        {
            dispatcher.RequireSession();
            if (p["blockId"] == null)
            {
                var player = MySession.Static.LocalHumanPlayer
                    ?? throw new RpcException(ErrorKind.NotFound, "No local player");
                owner = "player";
                // The one the HUD shows; the session's per-player copy is what gets saved and isn't redrawn.
                return MyToolbarComponent.CharacterToolbar
                    ?? MySession.Static.Toolbars.TryGetPlayerToolbar(player.Id)
                    ?? throw new RpcException(ErrorKind.NotFound, "The player has no toolbar");
            }
            var block = access.Block(p.Id("blockId"));
            owner = block.CustomName;
            var property = block.GetType().GetProperty("Toolbar", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            return property?.GetValue(block) as MyToolbar
                ?? throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' has no toolbar");
        }

        private static int Index(JsonObject p)
        {
            if (p["index"] != null)
                return (int)p.Number("index", 0);
            int page = (int)p.Number("page", 1), slot = (int)p.Number("slot", 0);
            if (slot < 1 || slot > SlotsPerPage || page < 1)
                throw new RpcException(ErrorKind.InvalidParams, "Give page (1-9) and slot (1-9), or index");
            return (page - 1) * SlotsPerPage + slot - 1;
        }

        private static object Describe(MyToolbarItem item, int index)
        {
            var entry = new Dictionary<string, object>
            {
                ["index"] = index,
                ["page"] = index / SlotsPerPage + 1,
                ["slot"] = index % SlotsPerPage + 1,
                ["text"] = item.DisplayName?.ToString(),
            };
            switch (item)
            {
                case MyToolbarItemTerminalBlock b:
                    entry["kind"] = "block";
                    entry["blockId"] = b.BlockEntityId.ToString();
                    entry["target"] = b.GetBlockName();
                    break;
                case MyToolbarItemTerminalGroup g:
                    entry["kind"] = "group";
                    entry["target"] = ((MyObjectBuilder_ToolbarItemTerminalGroup)g.GetObjectBuilder()).GroupName;
                    break;
                default:
                    entry["kind"] = item.GetType().Name.Replace("MyToolbarItem", "").ToLowerInvariant();
                    break;
            }
            if (item is MyToolbarItemActions a)
            {
                entry["action"] = a.ActionId;
                entry["actionName"] = a.GetCurrentAction()?.Name?.ToString();
            }
            return entry;
        }

        private static object Get(Access access, Dispatcher dispatcher, JsonObject p)
        {
            var toolbar = Toolbar(access, dispatcher, p, out string owner);
            var slots = new List<object>();
            for (int i = 0; i < toolbar.ItemCount; i++)
            {
                var item = toolbar.GetItemAtIndex(i);
                if (item != null)
                    slots.Add(Describe(item, i));
            }
            return new
            {
                owner,
                type = toolbar.ToolbarType.ToString(),
                pages = toolbar.ItemCount / SlotsPerPage,
                currentPage = toolbar.CurrentPage + 1,
                slots,
            };
        }

        // Put a block or group action in a slot, or clear it.
        private static object Set(Access access, Dispatcher dispatcher, JsonObject p)
        {
            dispatcher.RequireWritable();
            var toolbar = Toolbar(access, dispatcher, p, out string owner);
            int index = Index(p);
            if (!toolbar.IsValidIndex(index))
                throw new RpcException(ErrorKind.InvalidParams, $"Slot index {index} is outside this toolbar ({toolbar.ItemCount} slots)");
            var before = toolbar.GetItemAtIndex(index);
            object beforeInfo = before == null ? null : Describe(before, index);

            if (p.Bool("clear", false))
            {
                toolbar.SetItemAtIndex(index, null);
                dispatcher.Context.Log($"toolbar.set '{owner}' slot {index}: cleared");
                return new { owner, before = beforeInfo, after = (object)null };
            }

            // A tool, weapon or block by its in-game name: hand tools and weapons first, then blocks (large grid first).
            if (p.String("itemName") is string itemName)
            {
                var all = Sandbox.Definitions.MyDefinitionManager.Static.GetAllDefinitions()
                    .Where(d => d.Public && string.Equals(d.DisplayNameText, itemName.Trim(), StringComparison.OrdinalIgnoreCase))
                    .ToList();
                var pick = all.OfType<Sandbox.Definitions.MyWeaponItemDefinition>().Cast<MyDefinitionBase>().FirstOrDefault()
                    ?? all.OfType<Sandbox.Definitions.MyCubeBlockDefinition>().OrderBy(d => d.CubeSize == MyCubeSize.Large ? 0 : 1).Cast<MyDefinitionBase>().FirstOrDefault()
                    ?? all.FirstOrDefault()
                    ?? throw new RpcException(ErrorKind.NotFound, $"Nothing called '{itemName}' (use the in-game name, e.g. 'Elite Grinder')");
                p["definitionId"] = pick.Id.ToString().Replace("MyObjectBuilder_", "");
            }

            // A block to build or a tool/weapon (the player's toolbar), by definition id "TypeId/SubtypeId".
            if (p.String("definitionId") is string defText)
            {
                if (!MyDefinitionId.TryParse(defText.StartsWith("MyObjectBuilder_") ? defText : "MyObjectBuilder_" + defText, out var defId)
                    || !Sandbox.Definitions.MyDefinitionManager.Static.TryGetDefinition(defId, out VRage.Game.MyDefinitionBase definition))
                    throw new RpcException(ErrorKind.NotFound, $"No definition '{defText}'");
                var defItem = MyToolbarItemFactory.CreateToolbarItem(MyToolbarItemFactory.ObjectBuilderFromDefinition(definition))
                    ?? throw new RpcException(ErrorKind.InvalidParams, $"'{definition.DisplayNameText}' can't go on a toolbar");
                if (!defItem.AllowedInToolbarType(toolbar.ToolbarType))
                    throw new RpcException(ErrorKind.InvalidParams, $"'{definition.DisplayNameText}' isn't allowed on a {toolbar.ToolbarType} toolbar");
                toolbar.SetItemAtIndex(index, defItem);
                dispatcher.Context.Log($"toolbar.set '{owner}' slot {index}: {defText}");
                var placed = toolbar.GetItemAtIndex(index);
                return new { owner, before = beforeInfo, after = placed == null ? null : Describe(placed, index) };
            }

            string actionId = p.String("action", required: true);
            MyObjectBuilder_ToolbarItem builder;
            if (p["targetBlockId"] != null)
            {
                var target = access.Block(p.Id("targetBlockId"));
                builder = MyToolbarItemFactory.TerminalBlockObjectBuilderFromBlock((MyTerminalBlock)target);
            }
            else
            {
                string groupName = p.String("group") ?? throw new RpcException(ErrorKind.InvalidParams, "Give targetBlockId or group");
                var grid = (MyCubeGrid)access.Grid(p.Id("gridId"));
                var group = grid.GridSystems.TerminalSystem.BlockGroups
                    .FirstOrDefault(g => string.Equals(g.Name.ToString(), groupName, StringComparison.OrdinalIgnoreCase))
                    ?? throw new RpcException(ErrorKind.NotFound, $"No block group '{groupName}' on '{grid.DisplayName}'");
                builder = MyToolbarItemFactory.TerminalGroupObjectBuilderFromGroup(group);
                if (toolbar.Owner != null)
                    ((MyObjectBuilder_ToolbarItemTerminalGroup)builder).BlockEntityId = toolbar.Owner.EntityId;
            }
            if (builder is MyObjectBuilder_ToolbarItemTerminalBlock tb) tb._Action = actionId;
            else ((MyObjectBuilder_ToolbarItemTerminalGroup)builder)._Action = actionId;

            var item = MyToolbarItemFactory.CreateToolbarItem(builder) as MyToolbarItemActions
                ?? throw new RpcException(ErrorKind.GameError, "Couldn't create the toolbar item");
            if (!item.AllowedInToolbarType(toolbar.ToolbarType))
                throw new RpcException(ErrorKind.InvalidParams,
                    $"Block and group actions can't go on a {toolbar.ToolbarType} toolbar (the game only allows them on ship controllers, panels, timers and the like)");
            var possible = item.PossibleActions(toolbar.ToolbarType);
            if (!possible.Any(a => a.Id == actionId))
                throw new RpcException(ErrorKind.InvalidParams,
                    $"'{actionId}' isn't an action available here. Available: {string.Join(", ", possible.Select(a => a.Id))}");
            toolbar.SetItemAtIndex(index, item);
            toolbar.UpdateItem(index);
            dispatcher.Context.Log($"toolbar.set '{owner}' slot {index}: {actionId}");
            var after = toolbar.GetItemAtIndex(index)
                ?? throw new RpcException(ErrorKind.GameError, "The game didn't accept that item in this slot");
            return new { owner, before = beforeInfo, after = Describe(after, index) };
        }

        // MyBlockGroup keeps its members in an internal set.
        private static readonly FieldInfo BlocksField = typeof(MyBlockGroup).GetField("Blocks", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);

        private static HashSet<MyTerminalBlock> Members(MyBlockGroup group) => (HashSet<MyTerminalBlock>)BlocksField.GetValue(group);

        private static readonly FieldInfo NameField = typeof(MyBlockGroup).GetField("Name", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);

        // An empty group (internal constructor) with this name.
        private static MyBlockGroup NewGroup(string name)
        {
            var group = (MyBlockGroup)Activator.CreateInstance(typeof(MyBlockGroup), nonPublic: true);
            NameField.SetValue(group, new StringBuilder(name));
            return group;
        }

        // Create, change or delete a block group, as the terminal screen does (MyCubeGrid.ModifyGroup: an empty group
        // is deleted). mode: replace (default), add, remove, delete; rename via newName.
        private static object SetGroup(Access access, Dispatcher dispatcher, JsonObject p)
        {
            dispatcher.RequireWritable();
            var grid = (MyCubeGrid)access.Grid(p.Id("gridId"));
            string name = p.String("name", required: true);
            string mode = p.String("mode") ?? "replace";
            var existing = grid.GridSystems.TerminalSystem.BlockGroups
                .FirstOrDefault(g => string.Equals(g.Name.ToString(), name, StringComparison.OrdinalIgnoreCase));
            var members = new HashSet<MyTerminalBlock>(existing != null ? Members(existing) : Enumerable.Empty<MyTerminalBlock>());
            int before = members.Count;

            var given = (p["blockIds"] as JsonArray ?? new JsonArray())
                .Select(id => (MyTerminalBlock)access.Block(long.Parse(id.ToString()))).ToList();
            foreach (var b in given)
                if (!((IMyCubeGrid)grid).IsInSameLogicalGroupAs(b.CubeGrid))
                    throw new RpcException(ErrorKind.InvalidParams, $"'{b.CustomName}' isn't on this ship");

            switch (mode)
            {
                case "replace": members = new HashSet<MyTerminalBlock>(given); break;
                case "add": members.UnionWith(given); break;
                case "remove": members.ExceptWith(given); break;
                case "delete": members.Clear(); break;
                default: throw new RpcException(ErrorKind.InvalidParams, "mode must be replace, add, remove or delete");
            }
            if (existing == null && members.Count == 0)
                throw new RpcException(ErrorKind.NotFound, $"No block group '{name}' on '{grid.DisplayName}'");

            string newName = p.String("newName");
            string finalName = newName ?? existing?.Name.ToString() ?? name;
            if (newName != null && existing != null)
                grid.ModifyGroup(NewGroup(existing.Name.ToString()));  // drop the old name
            var group = NewGroup(finalName);
            Members(group).UnionWith(members);
            grid.ModifyGroup(group);
            dispatcher.Context.Log($"group.set '{finalName}' {mode}: {before} -> {members.Count} blocks");
            return new
            {
                name = finalName,
                created = existing == null,
                deleted = members.Count == 0,
                before,
                blocks = members.OrderBy(b => b.CustomName.ToString()).Select(b => new { id = b.EntityId.ToString(), name = b.CustomName.ToString() }).ToList(),
            };
        }
    }
}
