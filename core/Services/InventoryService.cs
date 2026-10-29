using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using VRage.Game.ModAPI;
using VRage.ModAPI;
using VRageMath;
using IngameInventoryItem = VRage.Game.ModAPI.Ingame.MyInventoryItem;

namespace SEMcp.Core.Services
{
    internal sealed class InventoryService
    {
        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public InventoryService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("inventory.get", Get);
            dispatcher.Register("inventory.layout", Layout);
            dispatcher.Register("inventory.transfer", Transfer);
        }

        // The game's interaction distance (MyConstants.DEFAULT_INTERACTIVE_DISTANCE): how close the player must be
        // to a block to open its inventory by hand.
        private const double ReachMeters = 5;

        // Every inventory on a ship, with what's needed to plan moves: block kind, tags text, fill, items, and which
        // inventories are joined by conveyors (same conveyorGroup = items can travel between them, size permitting).
        private object Layout(JsonObject p)
        {
            var grid = access.Grid(p.Id("gridId"));
            var inventories = new List<(IMyTerminalBlock block, int index, IMyInventory inventory)>();
            foreach (var block in Access.Construct(grid).SelectMany(g => g.GetFatBlocks<IMyTerminalBlock>()))
            {
                if (!block.HasInventory || !access.CanUse(block))
                    continue;
                for (int i = 0; i < block.InventoryCount; i++)
                    inventories.Add((block, i, block.GetInventory(i)));
            }

            // Conveyor groups: compare against one representative per group, not every pair.
            var representatives = new List<IMyInventory>();
            var groupOf = new Dictionary<IMyInventory, int>();
            foreach (var (_, _, inventory) in inventories)
            {
                int group = representatives.FindIndex(r => r.IsConnectedTo(inventory));
                if (group < 0)
                {
                    group = representatives.Count;
                    representatives.Add(inventory);
                }
                groupOf[inventory] = group;
            }

            return new
            {
                gridName = Dto.GridName(grid),
                shipId = Dto.Id(Access.ShipOf(grid).EntityId),
                inventories = inventories.Select(x => new
                {
                    blockId = Dto.Id(x.block.EntityId),
                    name = x.block.CustomName,
                    type = Dto.ShortType(x.block.BlockDefinition.TypeIdString),
                    subtype = x.block.BlockDefinition.SubtypeId,
                    gridName = Dto.GridName(x.block.CubeGrid),
                    // Ships docked by connectors share conveyors but are separate ships.
                    shipId = Dto.Id(Access.ShipOf(x.block.CubeGrid).EntityId),
                    shipName = Dto.GridName(Access.ShipOf(x.block.CubeGrid)),
                    // Tags like [Ore] may be in the name or CustomData; only send CustomData when it has some.
                    customData = x.block.CustomData != null && x.block.CustomData.Contains("[") ? x.block.CustomData : null,
                    index = x.index,
                    // Production blocks: inventory 0 is input, 1 is output.
                    role = x.block is IMyProductionBlock ? (x.index == 0 ? "input" : "output") : null,
                    working = (x.block as IMyFunctionalBlock)?.IsWorking ?? true,
                    conveyorGroup = groupOf[x.inventory],
                    volumeL = Dto.Liters(x.inventory.CurrentVolume),
                    maxVolumeL = Dto.Liters(x.inventory.MaxVolume),
                    items = Dto.Items(x.inventory),
                }).ToList(),
            };
        }

        private (IMyInventory inventory, string name, bool isSuit, IMyTerminalBlock block) Endpoint(JsonObject p, string prefix)
        {
            if (p.Bool(prefix + "Character", false))
            {
                var character = access.Player.Character
                    ?? throw new RpcException(ErrorKind.NotFound, "The player has no character (dead or spectating?)");
                return (character.GetInventory(0), "your suit", true, null);
            }
            if (p[prefix + "BlockId"] == null)
                throw new RpcException(ErrorKind.InvalidParams, $"Give {prefix}BlockId or {prefix}Character: true");
            var block = access.Block(p.Id(prefix + "BlockId"));
            int index = (int)p.Number(prefix + "Index", 0);
            if (!block.HasInventory || index < 0 || index >= block.InventoryCount)
                throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' has no inventory {index}");
            return (block.GetInventory(index), block.CustomName, false, block);
        }

        // Conveyor check between two blocks for one item: the host's pathfinding (same as the inventory screen), or
        // the ModAPI equivalent when both have inventories.
        private bool CanConvey(IMyEntity from, IMyEntity to, VRage.Game.ModAPI.Ingame.MyItemType item)
        {
            var answer = dispatcher.Context.CanConvey(from, to, item);
            if (answer.HasValue)
                return answer.Value;
            return from.HasInventory && to.HasInventory && from.GetInventory(0).CanTransferItemTo(to.GetInventory(0), item);
        }

        // How the suit reaches a block, like the inventory screen: through an access point (the seat the player is
        // in, or a block within arm's reach), then along conveyors big enough for the item. Returns a description of
        // the route, or throws with what's in the way.
        private string SuitRoute(IMyTerminalBlock target, VRage.Game.ModAPI.Ingame.MyItemType item)
        {
            var player = access.Player;
            if (player.Controller?.ControlledEntity?.Entity is IMyCubeBlock seat)
            {
                if (seat == target)
                    return $"from your seat";
                if (CanConvey(seat, target, item))
                    return $"through conveyors from {((IMyTerminalBlock)seat).CustomName}";
                throw new RpcException(ErrorKind.OutOfRange,
                    $"From {((IMyTerminalBlock)seat).CustomName} there's no conveyor path to '{target.CustomName}' for {item.SubtypeId} " +
                    "(not connected, or the conveyors are too small for it). Get out and walk to it, or use a seat on that network.");
            }

            var character = player.Character
                ?? throw new RpcException(ErrorKind.NotFound, "The player has no character (dead or spectating?)");
            Vector3D position = character.GetPosition();
            if (target.WorldAABB.Distance(position) <= ReachMeters)
                return "by hand";

            // Any block within reach on that ship can be the access point, if the item can travel from it.
            // Nearest first, so the route names the block the player would actually use.
            var accessPoint = Access.Construct(target.CubeGrid)
                .SelectMany(g => g.GetFatBlocks<IMyTerminalBlock>())
                .Select(b => (block: b, distance: b.WorldAABB.Distance(position)))
                .Where(x => x.distance <= ReachMeters && access.CanUse(x.block))
                .OrderBy(x => x.distance)
                .FirstOrDefault(x => CanConvey(x.block, target, item));
            if (accessPoint.block != null)
                return $"through conveyors from {accessPoint.block.CustomName} next to you";
            double distance = target.WorldAABB.Distance(position);
            throw new RpcException(ErrorKind.OutOfRange,
                $"You can't reach '{target.CustomName}' from where you are ({distance:F0} m away, and nothing within arm's reach " +
                $"connects to it by conveyors for {item.SubtypeId}). Walk to it or to a conveyor block on its network, or sit in a seat on that network.");
        }

        // Moves one item type between two inventories with the inventory screen's rules: block to block along
        // conveyors big enough for the item; the suit through an access point (see SuitRoute). The move itself is
        // the screen's player request when the host provides it, so it works as a multiplayer client too.
        // Reports what actually moved and why the rest didn't.
        private object Transfer(JsonObject p)
        {
            dispatcher.RequireWritable();
            bool isServer = MyAPIGateway.Multiplayer == null || MyAPIGateway.Multiplayer.IsServer;
            var byUser = dispatcher.Context.TransferByUser;
            if (!isServer && byUser == null)
                throw new RpcException(ErrorKind.FeatureDisabled, "Moving items as a multiplayer client needs the host's transfer hook");

            var from = Endpoint(p, "from");
            var to = Endpoint(p, "to");
            if (from.inventory == to.inventory)
                throw new RpcException(ErrorKind.InvalidParams, "Source and destination are the same inventory");
            if (from.isSuit && to.isSuit)
                throw new RpcException(ErrorKind.InvalidParams, "Both ends are your suit");
            string typeName = p.String("type", required: true);
            string subtype = p.String("subtype", required: true);
            var itemType = new VRage.Game.ModAPI.Ingame.MyItemType("MyObjectBuilder_" + typeName, subtype);
            double available = (double)from.inventory.GetItemAmount(itemType);
            double requested = p["amount"] == null ? available : Math.Min(p.Number("amount", 0), available);
            string label = $"{subtype} {typeName}";

            string reason = null, route = null;
            if (available <= 0)
                reason = $"There is no {label} in {from.name}";
            else if (from.isSuit)
                route = SuitRoute(to.block, itemType);
            else if (to.isSuit)
                route = SuitRoute(from.block, itemType);
            else if (!CanConvey(from.block, to.block, itemType))
                reason = $"{label} can't travel from {from.name} to {to.name} by conveyor (not connected, or a conveyor on the way is too small for it)";
            else
                route = "through conveyors";

            if (reason == null && !to.inventory.CanItemsBeAdded((VRage.MyFixedPoint)Math.Min(requested, 1), itemType))
                reason = $"{to.name} is full or doesn't accept {label}";

            double moved = 0;
            bool pending = false;
            if (reason == null)
            {
                var items = new List<IngameInventoryItem>();
                // Item stacks can be split; keep moving the first matching stack until done or nothing moves.
                for (int guard = 0; guard < 64 && requested - moved > 1e-6; guard++)
                {
                    items.Clear();
                    from.inventory.GetItems(items);
                    int stack = items.FindIndex(i => i.Type == itemType);
                    if (stack < 0)
                        break;
                    double before = (double)from.inventory.GetItemAmount(itemType);
                    var chunk = (VRage.MyFixedPoint)Math.Min((double)items[stack].Amount, requested - moved);
                    if (byUser != null)
                        byUser((IMyInventory)from.inventory, (IMyInventory)to.inventory, items[stack].ItemId, chunk);
                    else
                        ((IMyInventory)from.inventory).TransferItemTo(to.inventory, stack, null, true, chunk, false);
                    if (!isServer)
                    {
                        // The server carries it out a moment later; one request per call is enough to report.
                        pending = true;
                        moved = (double)chunk;
                        break;
                    }
                    double step = before - (double)from.inventory.GetItemAmount(itemType);
                    if (step <= 1e-6)
                    {
                        reason = $"{to.name} is full or the game refused to move more {label}";
                        break;
                    }
                    moved += step;
                }
            }

            if (moved > 0)
                dispatcher.Context.Log($"moved {moved:0.##} {label}: {from.name} -> {to.name} ({route})");
            return new
            {
                item = new { type = typeName, subtype },
                from = from.name,
                to = to.name,
                route,
                requested = Math.Round(requested, 2),
                moved = Math.Round(moved, 2),
                // Multiplayer client: the request was sent, and the server's result shows up a moment later.
                pending,
                leftInSource = Math.Round((double)from.inventory.GetItemAmount(itemType), 2),
                reason,
            };
        }

        // Inventory of one block, a whole ship (gridId, with subgrids), or the player's suit (character: true).
        private object Get(JsonObject p)
        {
            var sources = new List<(IMyEntity owner, string name)>();
            if (p.Bool("character", false))
            {
                var character = access.Player.Character
                    ?? throw new RpcException(ErrorKind.NotFound, "The player has no character (dead or spectating?)");
                sources.Add((character, "Suit"));
            }
            else if (p["blockId"] != null)
            {
                var block = access.Block(p.Id("blockId"));
                sources.Add((block, block.CustomName));
            }
            else if (p["gridId"] != null)
            {
                var grid = access.Grid(p.Id("gridId"));
                var grids = p.Bool("includeSubgrids", true) ? Access.Construct(grid) : new List<IMyCubeGrid> { grid };
                foreach (var block in grids.SelectMany(g => g.GetFatBlocks<IMyTerminalBlock>()))
                {
                    if (block.HasInventory && access.CanUse(block))
                        sources.Add((block, block.CustomName));
                }
            }
            else
            {
                throw new RpcException(ErrorKind.InvalidParams, "Give one of gridId, blockId or character: true");
            }

            var totals = new Dictionary<(string type, string subtype), double>();
            var inventories = new List<object>();
            double volume = 0, maxVolume = 0;

            foreach (var (owner, name) in sources)
            {
                int index = 0;
                foreach (var inventory in Dto.Inventories(owner))
                {
                    volume += (double)inventory.CurrentVolume;
                    maxVolume += (double)inventory.MaxVolume;

                    var items = new List<IngameInventoryItem>();
                    inventory.GetItems(items);
                    foreach (var item in items)
                    {
                        var key = (Dto.ShortType(item.Type.TypeId), item.Type.SubtypeId);
                        totals.TryGetValue(key, out double amount);
                        totals[key] = amount + (double)item.Amount;
                    }

                    // Skip empty inventories in ship-wide listings to keep the output small.
                    if (items.Count > 0 || sources.Count == 1)
                        inventories.Add(new { blockId = Dto.Id(owner.EntityId), name, inventory = Dto.Inventory(inventory, index) });
                    index++;
                }
            }

            return new
            {
                volumeL = Math.Round(volume * 1000),
                maxVolumeL = Math.Round(maxVolume * 1000),
                fill = Dto.Percent(volume, maxVolume),
                totals = totals
                    .OrderBy(t => t.Key.type).ThenByDescending(t => t.Value)
                    .Select(t => new { type = t.Key.type, subtype = t.Key.subtype, amount = Math.Round(t.Value, 2) })
                    .ToList(),
                inventories,
            };
        }
    }
}
