using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.Definitions;
using Sandbox.ModAPI;
using VRage.Game;
using VRage.Game.ModAPI;

namespace SEMcp.Core.Services
{
    // Build planning: what blocks cost (from their definitions) and what a projector still needs.
    internal sealed class BuildService
    {
        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public BuildService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("blockdefs.find", FindBlocks);
            dispatcher.Register("projector.get", Projector);
        }

        private static string Key(MyDefinitionId id) => $"{Dto.ShortType(id.TypeId.ToString())}/{id.SubtypeName}";

        private static string Name(MyDefinitionBase d) => (d.DisplayNameText ?? d.Id.SubtypeName).Split('\n')[0].Trim();

        private static object Components(MyCubeBlockDefinition d, int count = 1) =>
            (d.Components ?? new MyCubeBlockDefinition.Component[0])
                .GroupBy(c => c.Definition.Id.SubtypeName)
                .Select(g => new { type = "Component", subtype = g.Key, count = g.Sum(c => c.Count) * count })
                .ToList();

        private static object Block(MyCubeBlockDefinition d, int count = 1) => new
        {
            id = Key(d.Id),
            name = Name(d),
            size = d.CubeSize == MyCubeSize.Large ? "large" : "small",
            pcu = d.PCU,
            count,
            components = Components(d, count),
        };

        // Block definitions whose name has every word of the query (e.g. "refinery", "large cargo"). Exact names
        // first, then shorter ones; armor variants and hidden blocks sink to the bottom.
        private object FindBlocks(JsonObject p)
        {
            string query = (p.String("query", required: true) ?? "").Trim().ToLowerInvariant();
            string size = p.String("size");
            var words = query.Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
            var matches = MyDefinitionManager.Static.GetAllDefinitions()
                .OfType<MyCubeBlockDefinition>()
                .Where(d => d.Public && d.Components != null && d.Components.Length > 0)
                .Where(d => size == null || (size == "large") == (d.CubeSize == MyCubeSize.Large))
                .Select(d => (d, name: Name(d).ToLowerInvariant()))
                .Where(x => words.All(w => x.name.Contains(w) || x.d.Id.SubtypeName.ToLowerInvariant().Contains(w)))
                .OrderBy(x => x.name == query ? 0 : 1)
                // Words found in the display name beat words only in the internal id ("LargeBlockSmallContainer").
                .ThenBy(x => words.All(w => x.name.Contains(w)) ? 0 : 1)
                .ThenBy(x => x.d.GuiVisible ? 0 : 1)
                .ThenBy(x => x.name.Length)
                .ThenBy(x => x.d.CubeSize == MyCubeSize.Large ? 0 : 1)
                .Take((int)p.Number("limit", 30))
                .Select(x => Block(x.d))
                .ToList();
            return new { blocks = matches };
        }

        // What a projector shows and what's still needed to finish it: blocks not placed yet (by type, with their
        // full component cost) plus blocks already placed but not finished on the projector's ship.
        private object Projector(JsonObject p)
        {
            var block = access.Block(p.Id("blockId"));
            var projector = block as IMyProjector
                ?? throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' is not a projector");
            if (!projector.IsProjecting)
                return new { name = projector.CustomName, projecting = false, working = projector.IsWorking };

            var remaining = projector.RemainingBlocksPerType ?? new Dictionary<MyDefinitionBase, int>();
            var unfinished = new Dictionary<string, int>();
            int unfinishedBlocks = 0;
            foreach (var grid in Access.Construct(projector.CubeGrid))
            {
                var slims = new List<IMySlimBlock>();
                grid.GetBlocks(slims);
                foreach (var slim in slims.Where(s => !s.IsFullIntegrity))
                {
                    var missing = Dto.MissingComponents(slim);
                    if (missing.Count == 0)
                        continue;
                    unfinishedBlocks++;
                    foreach (var kv in missing)
                        unfinished[kv.Key] = (unfinished.TryGetValue(kv.Key, out int n) ? n : 0) + kv.Value;
                }
            }

            return new
            {
                name = projector.CustomName,
                projecting = true,
                working = projector.IsWorking,
                projectedGrid = projector.ProjectedGrid?.CustomName,
                totalBlocks = projector.TotalBlocks,
                remainingBlocks = projector.RemainingBlocks,
                remainingArmorBlocks = projector.RemainingArmorBlocks,
                buildableNow = projector.BuildableBlocksCount,
                remaining = remaining
                    .Where(kv => kv.Value > 0)
                    .Select(kv => kv.Key is MyCubeBlockDefinition d ? Block(d, kv.Value) : (object)new { id = Key(kv.Key.Id), name = Name(kv.Key), count = kv.Value })
                    .ToList(),
                // Already welded in place but not complete (includes damage), on the projector's ship.
                unfinishedBlocks,
                unfinishedComponents = unfinished.Select(kv => new { type = "Component", subtype = kv.Key, count = kv.Value }).ToList(),
            };
        }
    }
}
