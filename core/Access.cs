using System.Collections.Generic;
using System.Linq;
using Sandbox.ModAPI;
using VRage.Game;
using VRage.Game.ModAPI;
using VRageMath;

namespace SEMcp.Core
{
    // "Act as the player": what the local player may see and use, and how far away it may be.
    public sealed class Access
    {
        private readonly Dispatcher dispatcher;

        public Access(Dispatcher dispatcher)
        {
            this.dispatcher = dispatcher;
        }

        public IMyPlayer Player
        {
            get
            {
                dispatcher.RequireSession();
                return MyAPIGateway.Session.Player
                    ?? throw new RpcException(ErrorKind.NoSession, "No local player (dedicated server?)");
            }
        }

        public long IdentityId => Player.IdentityId;

        // Character position, or the controlled block's when seated.
        public Vector3D Origin => Player.GetPosition();

        public double Distance(IMyCubeGrid grid) => grid.WorldAABB.Distance(Origin);

        // Grid relation from its majority owners. Unowned grids count as "nobody".
        public MyRelationsBetweenPlayerAndBlock Relation(IMyCubeGrid grid)
        {
            List<long> owners = grid.BigOwners;
            if (owners == null || owners.Count == 0)
                return MyRelationsBetweenPlayerAndBlock.NoOwnership;

            var player = Player;
            var relations = owners.Select(owner => owner == player.IdentityId
                ? MyRelationsBetweenPlayerAndBlock.Owner
                : player.GetRelationTo(owner)).ToList();
            if (relations.Contains(MyRelationsBetweenPlayerAndBlock.Owner))
                return MyRelationsBetweenPlayerAndBlock.Owner;
            if (relations.Contains(MyRelationsBetweenPlayerAndBlock.FactionShare))
                return MyRelationsBetweenPlayerAndBlock.FactionShare;
            return relations[0];
        }

        public static bool IsUsable(MyRelationsBetweenPlayerAndBlock relation) =>
            relation == MyRelationsBetweenPlayerAndBlock.Owner
            || relation == MyRelationsBetweenPlayerAndBlock.FactionShare
            || relation == MyRelationsBetweenPlayerAndBlock.NoOwnership;

        public bool CanUse(IMyCubeGrid grid) => IsUsable(Relation(grid));

        public bool CanUse(Sandbox.ModAPI.IMyTerminalBlock block) => block.HasPlayerAccess(IdentityId);

        public bool InRange(IMyCubeGrid grid) => Distance(grid) <= dispatcher.Context.MaxRangeMeters;

        // Looks up a grid the player may use; throws not_found / access_denied / out_of_range.
        public IMyCubeGrid Grid(long id)
        {
            dispatcher.RequireSession();
            if (!(MyAPIGateway.Entities.GetEntityById(id) is IMyCubeGrid grid) || grid.Physics == null)
                throw new RpcException(ErrorKind.NotFound, $"No grid with id {id}");
            if (!CanUse(grid))
                throw new RpcException(ErrorKind.AccessDenied, $"Grid '{grid.CustomName}' belongs to someone else");
            if (!InRange(grid))
                throw new RpcException(ErrorKind.OutOfRange, $"Grid '{grid.CustomName}' is {Distance(grid):F0} m away (max {dispatcher.Context.MaxRangeMeters:F0} m)");
            return grid;
        }

        // Looks up a terminal block the player may use.
        public Sandbox.ModAPI.IMyTerminalBlock Block(long id)
        {
            dispatcher.RequireSession();
            if (!(MyAPIGateway.Entities.GetEntityById(id) is Sandbox.ModAPI.IMyTerminalBlock block))
                throw new RpcException(ErrorKind.NotFound, $"No terminal block with id {id}");
            Grid(block.CubeGrid.EntityId);
            if (!CanUse(block))
                throw new RpcException(ErrorKind.AccessDenied, $"No access to block '{block.CustomName}'");
            return block;
        }

        // The grid plus everything attached by rotors, pistons and hinges: what a player calls "the ship".
        // Mechanical, not Logical: Logical also takes in ships docked by connectors.
        public static List<IMyCubeGrid> Construct(IMyCubeGrid grid) => Group(grid, GridLinkTypeEnum.Mechanical);

        // The construct plus ships docked to it by connectors (one terminal and conveyor system).
        public static List<IMyCubeGrid> WithDocked(IMyCubeGrid grid) => Group(grid, GridLinkTypeEnum.Logical);

        private static List<IMyCubeGrid> Group(IMyCubeGrid grid, GridLinkTypeEnum link)
        {
            var grids = grid.GetGridGroup(link)?.GetGrids(new List<IMyCubeGrid>());
            if (grids == null || grids.Count == 0)
                return new List<IMyCubeGrid> { grid };
            // Main grid first, then the rest by size.
            grids.Remove(grid);
            grids.Sort((a, b) => BlockCount(b).CompareTo(BlockCount(a)));
            grids.Insert(0, grid);
            return grids;
        }

        // "Which ship is this grid part of": grids joined by rotors, pistons and hinges, but not ships docked by
        // connectors. Identified by its biggest grid, so it reads like the ship's name.
        public static IMyCubeGrid ShipOf(IMyCubeGrid grid)
        {
            var grids = grid.GetGridGroup(GridLinkTypeEnum.Mechanical)?.GetGrids(new List<IMyCubeGrid>());
            if (grids == null || grids.Count == 0)
                return grid;
            return grids.OrderByDescending(BlockCount).ThenBy(g => g.EntityId).First();
        }

        public static int BlockCount(IMyCubeGrid grid)
        {
            var blocks = new List<IMySlimBlock>();
            grid.GetBlocks(blocks);
            return blocks.Count;
        }
    }
}
