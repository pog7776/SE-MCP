using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using VRage.Game.ModAPI;
using VRage.ModAPI;
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
