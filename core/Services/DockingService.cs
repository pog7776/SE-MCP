using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using SpaceEngineers.Game.ModAPI;
using VRage.Game.ModAPI;

namespace SEMcp.Core.Services
{
    // Connectors, landing gear and merge blocks: what's docked where, and connecting/locking like the terminal.
    // The approach itself (lining up a connector) is driven from the Python side with the remote control autopilot.
    internal sealed class DockingService
    {
        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public DockingService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("dock.get", Get);
            dispatcher.Register("dock.connector", Connector);
            dispatcher.Register("dock.gear", Gear);
        }

        private static double[] Vec(VRageMath.Vector3D v, int digits = 2) => SensingService.Vec(v, digits);

        private object Get(JsonObject p)
        {
            var grid = access.Grid(p.Id("gridId"));
            // Docking equipment on the ship itself (its mechanical group): not on ships already docked to it.
            var ship = Access.ShipOf(grid);
            var grids = grid.GetGridGroup(GridLinkTypeEnum.Mechanical)?.GetGrids(new List<IMyCubeGrid>()) ?? new List<IMyCubeGrid> { grid };
            var blocks = grids.SelectMany(g => g.GetFatBlocks<IMyTerminalBlock>()).Where(access.CanUse).ToList();

            return new
            {
                gridName = Dto.GridName(ship),
                isStatic = grid.IsStatic,
                connectors = blocks.OfType<IMyShipConnector>().OrderBy(c => c.CustomName).Select(c => new
                {
                    id = Dto.Id(c.EntityId),
                    name = c.CustomName,
                    gridName = Dto.GridName(c.CubeGrid),
                    size = Dto.Size(c.CubeGrid),
                    working = c.IsWorking,
                    status = c.Status.ToString(),
                    other = c.OtherConnector == null ? null : new
                    {
                        id = Dto.Id(c.OtherConnector.EntityId),
                        name = c.OtherConnector.CustomName,
                        gridName = Dto.GridName(Access.ShipOf(c.OtherConnector.CubeGrid)),
                        gridId = Dto.Id(c.OtherConnector.CubeGrid.EntityId),
                    },
                    position = Vec(c.GetPosition()),
                    // A connector joins along its forward axis: the face that touches the other connector.
                    forward = Vec(c.WorldMatrix.Forward, 4),
                    up = Vec(c.WorldMatrix.Up, 4),
                    parking = c.IsParkingEnabled,
                    collectAll = c.CollectAll,
                    throwOut = c.ThrowOut,
                }).ToList(),
                landingGear = blocks.OfType<IMyLandingGear>().OrderBy(g => g.CustomName).Select(g => new
                {
                    id = Dto.Id(g.EntityId),
                    name = g.CustomName,
                    working = g.IsWorking,
                    locked = g.IsLocked,
                    mode = g.LockMode.ToString(),
                    autoLock = g.AutoLock,
                    position = Vec(g.GetPosition()),
                    forward = Vec(g.WorldMatrix.Forward, 4),
                    up = Vec(g.WorldMatrix.Up, 4),
                }).ToList(),
                mergeBlocks = blocks.OfType<IMyShipMergeBlock>().OrderBy(m => m.CustomName).Select(m => new
                {
                    id = Dto.Id(m.EntityId),
                    name = m.CustomName,
                    working = m.IsWorking,
                    enabled = m.Enabled,
                    connected = m.IsConnected,
                }).ToList(),
            };
        }

        // connect / disconnect / toggle, as the terminal's buttons.
        private object Connector(JsonObject p)
        {
            dispatcher.RequireWritable();
            var c = access.Block(p.Id("blockId")) as IMyShipConnector
                ?? throw new RpcException(ErrorKind.InvalidParams, "Not a connector");
            string action = p.String("action", required: true);
            string before = c.Status.ToString();
            switch (action)
            {
                case "connect":
                    if (c.Status == Sandbox.ModAPI.Ingame.MyShipConnectorStatus.Unconnected)
                        throw new RpcException(ErrorKind.InvalidParams, $"'{c.CustomName}' has nothing in reach to connect to (status Unconnected)");
                    c.Connect();
                    break;
                case "disconnect": c.Disconnect(); break;
                case "toggle": c.ToggleConnect(); break;
                default: throw new RpcException(ErrorKind.InvalidParams, "action must be connect, disconnect or toggle");
            }
            dispatcher.Context.Log($"dock.connector '{c.CustomName}' {action}");
            return new { block = c.CustomName, before, after = c.Status.ToString() };
        }

        // lock / unlock / autoLock on/off for landing gear (blockId, or every gear on gridId).
        private object Gear(JsonObject p)
        {
            dispatcher.RequireWritable();
            IEnumerable<IMyLandingGear> gears = p["blockId"] != null
                ? new[] { access.Block(p.Id("blockId")) as IMyLandingGear ?? throw new RpcException(ErrorKind.InvalidParams, "Not a landing gear") }
                : (access.Grid(p.Id("gridId")).GetGridGroup(GridLinkTypeEnum.Mechanical)?.GetGrids(new List<IMyCubeGrid>()) ?? new List<IMyCubeGrid>())
                    .SelectMany(g => g.GetFatBlocks<IMyLandingGear>()).Where(access.CanUse).ToList();
            string action = p.String("action", required: true);
            var changed = new List<object>();
            foreach (var g in gears)
            {
                switch (action)
                {
                    case "lock": g.Lock(); break;
                    case "unlock": g.Unlock(); break;
                    case "autolock_on": g.AutoLock = true; break;
                    case "autolock_off": g.AutoLock = false; break;
                    default: throw new RpcException(ErrorKind.InvalidParams, "action must be lock, unlock, autolock_on or autolock_off");
                }
                changed.Add(new { name = g.CustomName, mode = g.LockMode.ToString(), autoLock = g.AutoLock });
            }
            dispatcher.Context.Log($"dock.gear {action} on {changed.Count} gear");
            return new { changed };
        }
    }
}
