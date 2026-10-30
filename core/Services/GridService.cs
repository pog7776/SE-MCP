using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using VRage.Game.ModAPI;
using VRage.ModAPI;

namespace SEMcp.Core.Services
{
    internal sealed class GridService
    {
        private const int DamagedListLimit = 15;

        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public GridService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("grids.list", List);
            dispatcher.Register("grid.get", Get);
        }

        // Grids near the player. Usable grids get a full summary; with includeAll, others get name/distance only.
        private object List(JsonObject p)
        {
            dispatcher.RequireSession();
            double range = Math.Min(p.Number("range", dispatcher.Context.MaxRangeMeters), dispatcher.Context.MaxRangeMeters);
            bool includeAll = p.Bool("includeAll", false);

            var entities = new HashSet<IMyEntity>();
            MyAPIGateway.Entities.GetEntities(entities, e => e is IMyCubeGrid grid && grid.Physics != null);

            var result = new List<object>();
            foreach (IMyCubeGrid grid in entities.Cast<IMyCubeGrid>().OrderBy(access.Distance))
            {
                double distance = access.Distance(grid);
                if (distance > range)
                    continue;

                var relation = access.Relation(grid);
                if (Access.IsUsable(relation))
                    result.Add(Summary(grid));
                else if (includeAll)
                    result.Add(new
                    {
                        id = Dto.Id(grid.EntityId),
                        name = Dto.GridName(grid),
                        size = Dto.Size(grid),
                        isStatic = grid.IsStatic,
                        relation = Dto.Relation(relation),
                        distance = Math.Round(distance),
                        accessible = false,
                    });
            }
            return new { range, grids = result };
        }

        private object Summary(IMyCubeGrid grid)
        {
            var construct = Access.Construct(grid);
            var docked = grid.GetGridGroup(GridLinkTypeEnum.Physical)?.GetGrids(new List<IMyCubeGrid>()) ?? new List<IMyCubeGrid>();
            return new
            {
                id = Dto.Id(grid.EntityId),
                name = Dto.GridName(grid),
                size = Dto.Size(grid),
                isStatic = grid.IsStatic,
                blocks = Access.BlockCount(grid),
                relation = Dto.Relation(access.Relation(grid)),
                position = Dto.Vector(grid.WorldAABB.Center),
                distance = Math.Round(access.Distance(grid)),
                speed = Dto.Speed(grid),
                // Smallest entity id in each group, so grids of one ship (or one docked cluster) share a key.
                constructId = GroupKey(construct, grid),
                dockedGroupId = GroupKey(docked, grid),
                accessible = true,
            };
        }

        private static string GroupKey(List<IMyCubeGrid> group, IMyCubeGrid fallback) =>
            Dto.Id(group.Count > 0 ? group.Min(g => g.EntityId) : fallback.EntityId);

        // A ship's status, summed over the grid and its subgrids (rotors/pistons/hinges).
        private object Get(JsonObject p)
        {
            var grid = access.Grid(p.Id("gridId"));
            bool includeSubgrids = p.Bool("includeSubgrids", true);
            var grids = includeSubgrids ? Access.Construct(grid) : new List<IMyCubeGrid> { grid };

            var terminal = grids.SelectMany(g => g.GetFatBlocks<IMyTerminalBlock>()).ToList();
            var slims = new List<IMySlimBlock>();
            foreach (var g in grids)
                g.GetBlocks(slims);

            var docked = (grid.GetGridGroup(GridLinkTypeEnum.Physical)?.GetGrids(new List<IMyCubeGrid>()) ?? new List<IMyCubeGrid>())
                .Where(g => !grids.Contains(g))
                .Select(g => new { id = Dto.Id(g.EntityId), name = Dto.GridName(g), size = Dto.Size(g) })
                .ToList();

            var damaged = slims.Where(s => !s.IsFullIntegrity || s.CurrentDamage > 0).ToList();

            return new
            {
                summary = Summary(grid),
                grids = grids.Select(g => new { id = Dto.Id(g.EntityId), name = Dto.GridName(g), size = Dto.Size(g), blocks = Access.BlockCount(g) }).ToList(),
                docked,
                mass = Math.Round(grid.Physics?.Mass ?? 0),
                power = Power(terminal),
                gas = Gas(terminal),
                cargo = Cargo(terminal),
                damage = new
                {
                    count = damaged.Count,
                    blocks = damaged
                        .OrderBy(s => s.Integrity / Math.Max(s.MaxIntegrity, 1))
                        .Take(DamagedListLimit)
                        .Select(s => new
                        {
                            id = s.FatBlock != null ? Dto.Id(s.FatBlock.EntityId) : null,
                            name = (s.FatBlock as IMyTerminalBlock)?.CustomName ?? s.BlockDefinition.DisplayNameText,
                            integrity = Dto.Integrity(s),
                            missing = Dto.MissingComponents(s),
                        })
                        .ToList(),
                },
                functionalCount = terminal.Count(b => b.IsFunctional),
                terminalBlocks = terminal.Count,
            };
        }

        private static object Power(List<IMyTerminalBlock> blocks)
        {
            var batteries = blocks.OfType<IMyBatteryBlock>().ToList();
            var producers = blocks.OfType<IMyPowerProducer>().Where(b => !(b is IMyBatteryBlock)).ToList();
            return new
            {
                batteries = new
                {
                    count = batteries.Count,
                    storedMWh = Math.Round(batteries.Sum(b => b.CurrentStoredPower), 2),
                    maxMWh = Math.Round(batteries.Sum(b => b.MaxStoredPower), 2),
                    fill = Dto.Percent(batteries.Sum(b => b.CurrentStoredPower), batteries.Sum(b => b.MaxStoredPower)),
                    inputMW = Math.Round(batteries.Sum(b => b.CurrentInput), 2),
                    outputMW = Math.Round(batteries.Sum(b => b.CurrentOutput), 2),
                },
                producers = producers
                    .GroupBy(b => Dto.ShortType(b.BlockDefinition.TypeIdString))
                    .Select(g => new
                    {
                        type = g.Key,
                        count = g.Count(),
                        working = g.Count(b => b.IsWorking),
                        outputMW = Math.Round(g.Sum(b => b.CurrentOutput), 2),
                        maxMW = Math.Round(g.Sum(b => b.MaxOutput), 2),
                    })
                    .ToList(),
            };
        }

        private static object Gas(List<IMyTerminalBlock> blocks)
        {
            var tanks = blocks.OfType<IMyGasTank>().ToList();
            object Summarize(IEnumerable<IMyGasTank> group)
            {
                var list = group.ToList();
                double capacity = list.Sum(t => (double)t.Capacity);
                double stored = list.Sum(t => t.Capacity * t.FilledRatio);
                return new { count = list.Count, fill = Dto.Percent(stored, capacity), storedL = Math.Round(stored), capacityL = Math.Round(capacity) };
            }

            bool IsHydrogen(IMyGasTank t) => t.BlockDefinition.SubtypeName.IndexOf("Hydrogen", StringComparison.OrdinalIgnoreCase) >= 0;
            return new
            {
                hydrogen = Summarize(tanks.Where(IsHydrogen)),
                oxygen = Summarize(tanks.Where(t => !IsHydrogen(t))),
            };
        }

        private static object Cargo(List<IMyTerminalBlock> blocks)
        {
            var containers = blocks.OfType<IMyCargoContainer>().SelectMany(Dto.Inventories).ToList();
            double volume = containers.Sum(i => (double)i.CurrentVolume);
            double max = containers.Sum(i => (double)i.MaxVolume);
            return new
            {
                containers = containers.Count,
                fill = Dto.Percent(volume, max),
                volumeL = Math.Round(volume * 1000),
                maxVolumeL = Math.Round(max * 1000),
            };
        }
    }
}
