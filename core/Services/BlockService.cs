using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using Sandbox.ModAPI.Interfaces;
using VRage.Game.ModAPI;
using VRageMath;
using IngameBlockGroup = Sandbox.ModAPI.Ingame.IMyBlockGroup;
using IngameTerminalBlock = Sandbox.ModAPI.Ingame.IMyTerminalBlock;

namespace SEMcp.Core.Services
{
    internal sealed class BlockService
    {
        private const int DefaultLimit = 200;

        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public BlockService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("blocks.list", List);
            dispatcher.Register("block.get", Get);
            dispatcher.Register("groups.list", Groups);
        }

        private object List(JsonObject p)
        {
            var grid = access.Grid(p.Id("gridId"));
            string type = p.String("type");
            string nameContains = p.String("nameContains");
            string group = p.String("group");
            int limit = (int)p.Number("limit", DefaultLimit);
            int offset = (int)p.Number("offset", 0);

            IEnumerable<IMyTerminalBlock> blocks;
            if (group != null)
            {
                var found = FindGroup(grid, group)
                    ?? throw new RpcException(ErrorKind.NotFound, $"No block group '{group}' on '{Dto.GridName(grid)}'");
                var members = new List<IngameTerminalBlock>();
                found.GetBlocks(members);
                blocks = members.OfType<IMyTerminalBlock>();
            }
            else
            {
                var grids = p.Bool("includeSubgrids", true) ? Access.Construct(grid) : new List<IMyCubeGrid> { grid };
                blocks = grids.SelectMany(g => g.GetFatBlocks<IMyTerminalBlock>());
            }

            if (type != null)
                blocks = blocks.Where(b => MatchesType(b, type));
            if (nameContains != null)
                blocks = blocks.Where(b => b.CustomName?.IndexOf(nameContains, StringComparison.OrdinalIgnoreCase) >= 0);

            var all = blocks.OrderBy(b => b.CustomName, StringComparer.OrdinalIgnoreCase).ToList();
            return new
            {
                total = all.Count,
                offset,
                blocks = all.Skip(offset).Take(limit).Select(b => Dto.BlockSummary(b, access)).ToList(),
            };
        }

        // Matches the short type id ("Reactor"), subtype ("LargeBlockSmallGenerator") or display type ("Small Reactor").
        private static bool MatchesType(IMyTerminalBlock block, string type)
        {
            bool Has(string text) => text?.IndexOf(type, StringComparison.OrdinalIgnoreCase) >= 0;
            return Has(Dto.ShortType(block.BlockDefinition.TypeIdString))
                || Has(block.BlockDefinition.SubtypeName)
                || Has(block.DefinitionDisplayNameText);
        }

        private object Get(JsonObject p)
        {
            var block = access.Block(p.Id("blockId"));
            var properties = new List<ITerminalProperty>();
            block.GetProperties(properties);
            var actions = new List<ITerminalAction>();
            block.GetActions(actions);

            return new
            {
                block = Dto.BlockSummary(block, access),
                gridName = Dto.GridName(block.CubeGrid),
                detailedInfo = block.DetailedInfo,
                customInfo = block.CustomInfo,
                customData = block.CustomData,
                showInTerminal = block.ShowInTerminal,
                // Where it is in the world (centre of its cells) and how it's mounted, for aiming at it.
                position = SensingService.Vec(block.WorldAABB.Center),
                forward = SensingService.Vec(block.WorldMatrix.Forward, 4),
                up = SensingService.Vec(block.WorldMatrix.Up, 4),
                right = SensingService.Vec(block.WorldMatrix.Right, 4),
                cell = new[] { block.Position.X, block.Position.Y, block.Position.Z },
                cellSize = block.CubeGrid.GridSize,
                gridVelocity = SensingService.Vec(block.CubeGrid.Physics?.LinearVelocity ?? Vector3D.Zero),
                missingComponents = block.SlimBlock != null ? Dto.MissingComponents(block.SlimBlock) : null,
                properties = properties.Select(prop => new { id = prop.Id, type = prop.TypeName, value = ReadProperty(block, prop) }).ToList(),
                actions = actions.Select(a => new { id = a.Id, name = a.Name?.ToString() }).ToList(),
                inventories = Dto.Inventories(block).Select((inv, i) => Dto.Inventory(inv, i)).ToList(),
            };
        }

        private static object ReadProperty(IMyTerminalBlock block, ITerminalProperty prop)
        {
            try
            {
                switch (prop.TypeName)
                {
                    case "Boolean": return prop.As<bool>().GetValue(block);
                    case "Single": return Math.Round(prop.As<float>().GetValue(block), 3);
                    case "Int64": return prop.As<long>().GetValue(block);
                    case "StringBuilder": return prop.As<StringBuilder>().GetValue(block)?.ToString();
                    case "String": return prop.As<string>().GetValue(block);
                    case "Color":
                        var c = prop.As<Color>().GetValue(block);
                        return $"#{c.R:X2}{c.G:X2}{c.B:X2}{c.A:X2}";
                    default: return null;
                }
            }
            catch (Exception)
            {
                return null;
            }
        }

        private object Groups(JsonObject p)
        {
            var grid = access.Grid(p.Id("gridId"));
            var groups = new List<IngameBlockGroup>();
            MyAPIGateway.TerminalActionsHelper.GetTerminalSystemForGrid(grid)?.GetBlockGroups(groups);

            return new
            {
                groups = groups.Select(g =>
                {
                    var members = new List<IngameTerminalBlock>();
                    g.GetBlocks(members);
                    return new
                    {
                        name = g.Name,
                        blocks = members.Select(b => new { id = Dto.Id(b.EntityId), name = b.CustomName }).ToList(),
                    };
                }).OrderBy(g => g.name, StringComparer.OrdinalIgnoreCase).ToList(),
            };
        }

        public static IngameBlockGroup FindGroup(IMyCubeGrid grid, string name)
        {
            var groups = new List<IngameBlockGroup>();
            MyAPIGateway.TerminalActionsHelper.GetTerminalSystemForGrid(grid)?.GetBlockGroups(groups);
            return groups.FirstOrDefault(g => string.Equals(g.Name, name, StringComparison.OrdinalIgnoreCase));
        }
    }
}
