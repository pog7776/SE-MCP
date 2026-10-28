using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using VRage.Game.ModAPI;
using IngameProductionItem = Sandbox.ModAPI.Ingame.MyProductionItem;

namespace SEMcp.Core.Services
{
    internal sealed class ProductionService
    {
        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public ProductionService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("production.get", Get);
        }

        // Assemblers, refineries and other production blocks on a ship: what they're doing and what's queued.
        private object Get(JsonObject p)
        {
            var grid = access.Grid(p.Id("gridId"));
            var grids = p.Bool("includeSubgrids", true) ? Access.Construct(grid) : new List<IMyCubeGrid> { grid };
            var blocks = grids.SelectMany(g => g.GetFatBlocks<IMyProductionBlock>())
                .Where(access.CanUse)
                .OrderBy(b => b.CustomName, StringComparer.OrdinalIgnoreCase);

            return new
            {
                blocks = blocks.Select(block =>
                {
                    var queue = new List<IngameProductionItem>();
                    block.GetQueue(queue);
                    var assembler = block as IMyAssembler;
                    return new
                    {
                        id = Dto.Id(block.EntityId),
                        name = block.CustomName,
                        type = Dto.ShortType(block.BlockDefinition.TypeIdString),
                        displayType = block.DefinitionDisplayNameText,
                        enabled = block.Enabled,
                        working = block.IsWorking,
                        producing = block.IsProducing,
                        mode = assembler?.Mode.ToString(),
                        repeating = assembler?.Repeating,
                        cooperative = assembler?.CooperativeMode,
                        queue = queue.Select(item => new
                        {
                            blueprint = item.BlueprintId.SubtypeName,
                            amount = Math.Round((double)item.Amount, 2),
                        }).ToList(),
                        input = Fill(block.InputInventory),
                        output = Fill(block.OutputInventory),
                    };
                }).ToList(),
            };
        }

        private static object Fill(VRage.Game.ModAPI.Ingame.IMyInventory inventory) => inventory == null ? null : new
        {
            fill = Dto.Percent((double)inventory.CurrentVolume, (double)inventory.MaxVolume),
            items = inventory.ItemCount,
        };
    }
}
