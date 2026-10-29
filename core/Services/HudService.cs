using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using SpaceEngineers.Game.ModAPI;
using VRage.Game.ModAPI;

namespace SEMcp.Core.Services
{
    // One-call "HUD" for a ship or station: every system's on/working state plus the key numbers, for the ship
    // itself only (its mechanical group: rotors, pistons, hinges), with ships docked to it listed separately.
    internal sealed class HudService
    {
        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public HudService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("ship.hud", Hud);
        }

        // count / on (Enabled) / working (powered, built, on).
        private static object Count<T>(List<T> blocks) where T : IMyFunctionalBlock => new
        {
            count = blocks.Count,
            on = blocks.Count(b => b.Enabled),
            working = blocks.Count(b => b.IsWorking),
        };

        private object Hud(JsonObject p)
        {
            var grid = access.Grid(p.Id("gridId"));
            var ship = Access.ShipOf(grid);
            var grids = grid.GetGridGroup(GridLinkTypeEnum.Mechanical)?.GetGrids(new List<IMyCubeGrid>()) ?? new List<IMyCubeGrid> { grid };
            var blocks = grids.SelectMany(g => g.GetFatBlocks<IMyTerminalBlock>()).Where(access.CanUse).ToList();
            List<T> Of<T>() where T : class => blocks.OfType<T>().ToList();

            var thrusters = Of<IMyThrust>();
            var gyros = Of<IMyGyro>();
            var batteries = Of<IMyBatteryBlock>();
            var reactors = Of<IMyReactor>();
            var tanks = Of<IMyGasTank>();
            var solar = Of<IMySolarPanel>();
            var controllers = Of<IMyShipController>();
            var remotes = Of<IMyRemoteControl>();
            var connectors = Of<IMyShipConnector>();
            var gear = Of<SpaceEngineers.Game.ModAPI.Ingame.IMyLandingGear>();
            var antennas = Of<IMyRadioAntenna>();
            var beacons = Of<IMyBeacon>();
            var weapons = Of<IMyUserControllableGun>();
            var cargo = Of<IMyCargoContainer>();

            var main = controllers.FirstOrDefault(c => c.IsMainCockpit) ?? controllers.FirstOrDefault(c => c.IsUnderControl) ?? controllers.FirstOrDefault();
            var pilot = controllers.FirstOrDefault(c => c.IsUnderControl);

            double uranium = reactors.Sum(r => r.GetInventory(0) != null ? (double)r.GetInventory(0).CurrentMass : 0);
            var h2 = tanks.Where(t => t.BlockDefinition.SubtypeName.Contains("Hydrogen")).ToList();
            var o2 = tanks.Except(h2).ToList();
            double cargoVol = 0, cargoMax = 0;
            foreach (var c in cargo)
                foreach (var inv in Dto.Inventories(c))
                {
                    cargoVol += (double)inv.CurrentVolume;
                    cargoMax += (double)inv.MaxVolume;
                }

            var slims = new List<IMySlimBlock>();
            foreach (var g in grids)
                g.GetBlocks(slims);
            int damaged = slims.Count(s => s.CurrentDamage > 0 || s.BuildLevelRatio < 1);

            var docked = connectors.Where(c => c.Status == Sandbox.ModAPI.Ingame.MyShipConnectorStatus.Connected && c.OtherConnector != null)
                .Select(c => new { connector = c.CustomName, to = c.OtherConnector.CustomName, ship = Dto.GridName(Access.ShipOf(c.OtherConnector.CubeGrid)), shipId = Dto.Id(Access.ShipOf(c.OtherConnector.CubeGrid).EntityId) })
                .ToList();

            return new
            {
                id = Dto.Id(ship.EntityId),
                name = Dto.GridName(ship),
                size = Dto.Size(ship),
                isStatic = grid.IsStatic,
                grids = grids.Count,
                blocks = slims.Count,
                mass = Math.Round(grids.Sum(g => (double)(g.Physics?.Mass ?? 0)), 0),
                speed = Dto.Speed(grid),
                distance = Math.Round(access.Distance(grid), 0),
                pilot = pilot?.CustomName,
                dampeners = main?.DampenersOverride,
                handbrake = main?.HandBrake,
                autopilot = remotes.Where(r => r.IsAutoPilotEnabled).Select(r => r.CustomName).ToList(),
                thrusters = Count(thrusters),
                gyros = new
                {
                    count = gyros.Count, on = gyros.Count(g => g.Enabled), working = gyros.Count(g => g.IsWorking),
                    overriding = gyros.Count(g => g.GyroOverride),
                },
                thrustOverrides = thrusters.Count(t => t.ThrustOverridePercentage > 0),
                batteries = new
                {
                    count = batteries.Count, working = batteries.Count(b => b.IsWorking),
                    storedMWh = Math.Round(batteries.Sum(b => b.CurrentStoredPower), 2),
                    maxMWh = Math.Round(batteries.Sum(b => b.MaxStoredPower), 2),
                    inMW = Math.Round(batteries.Sum(b => b.CurrentInput), 2),
                    outMW = Math.Round(batteries.Sum(b => b.CurrentOutput), 2),
                    recharge = batteries.Count(b => b.ChargeMode == Sandbox.ModAPI.Ingame.ChargeMode.Recharge),
                    discharge = batteries.Count(b => b.ChargeMode == Sandbox.ModAPI.Ingame.ChargeMode.Discharge),
                },
                reactors = new
                {
                    count = reactors.Count, working = reactors.Count(r => r.IsWorking),
                    outMW = Math.Round(reactors.Sum(r => r.CurrentOutput), 2),
                    maxMW = Math.Round(reactors.Sum(r => r.MaxOutput), 2),
                    uraniumKg = Math.Round(uranium, 1),
                },
                solar = new { count = solar.Count, outMW = Math.Round(solar.Sum(s => s.CurrentOutput), 3) },
                hydrogen = h2.Count == 0 ? null : new
                {
                    count = h2.Count, on = h2.Count(t => t.Enabled),
                    fill = Math.Round(100 * h2.Sum(t => t.FilledRatio * t.Capacity) / Math.Max(1, h2.Sum(t => t.Capacity)), 1),
                    stockpile = h2.Count(t => t.Stockpile),
                },
                oxygen = o2.Count == 0 ? null : new
                {
                    count = o2.Count, on = o2.Count(t => t.Enabled),
                    fill = Math.Round(100 * o2.Sum(t => t.FilledRatio * t.Capacity) / Math.Max(1, o2.Sum(t => t.Capacity)), 1),
                    stockpile = o2.Count(t => t.Stockpile),
                },
                cargo = new { containers = cargo.Count, fill = Dto.Percent(cargoVol, cargoMax) },
                connectors = new
                {
                    count = connectors.Count,
                    connected = connectors.Count(c => c.Status == Sandbox.ModAPI.Ingame.MyShipConnectorStatus.Connected),
                    connectable = connectors.Count(c => c.Status == Sandbox.ModAPI.Ingame.MyShipConnectorStatus.Connectable),
                },
                docked,
                gearLocked = gear.Count(g => g.IsLocked),
                gear = gear.Count,
                antennas = antennas.Select(a => new { name = a.CustomName, on = a.Enabled, working = a.IsWorking, broadcasting = a.IsBroadcasting, radius = Math.Round(a.Radius, 0) }).ToList(),
                beacons = beacons.Select(b => new { name = b.CustomName, on = b.Enabled, working = b.IsWorking, radius = Math.Round(b.Radius, 0) }).ToList(),
                weapons = new
                {
                    count = weapons.Count, working = weapons.Count(w => w.IsWorking),
                    turrets = weapons.Count(w => w is IMyLargeTurretBase),
                    targeting = weapons.OfType<IMyLargeTurretBase>().Count(t => t.HasTarget),
                    shooting = weapons.Count(w => w.IsShooting),
                },
                damaged,
                subgrids = Subgrids(ship, grids),
            };
        }

        // Every grid in the ship's mechanical group except the main one, and the rotor/hinge/piston joint that holds
        // it (with the joint's angle or extension and lock state): the moving parts of the ship.
        private object Subgrids(IMyCubeGrid main, List<IMyCubeGrid> grids)
        {
            var joints = grids.SelectMany(g => g.GetFatBlocks<IMyMechanicalConnectionBlock>()).ToList();
            return grids.Where(g => g.EntityId != main.EntityId).OrderByDescending(Access.BlockCount).Select(g =>
            {
                var joint = joints.FirstOrDefault(j => j.TopGrid?.EntityId == g.EntityId);
                object holder = null;
                if (joint != null)
                {
                    string kind = joint is IMyPistonBase ? "piston" : joint.BlockDefinition.SubtypeName.Contains("Hinge") ? "hinge" : "rotor";
                    holder = new
                    {
                        name = joint.CustomName,
                        kind,
                        onGrid = Dto.GridName(joint.CubeGrid),
                        working = joint.IsWorking,
                        angleDeg = joint is IMyMotorStator rotor ? Math.Round(VRageMath.MathHelper.ToDegrees(rotor.Angle), 1) : (double?)null,
                        locked = joint is IMyMotorStator r2 ? r2.RotorLock : (bool?)null,
                        extensionM = joint is IMyPistonBase piston ? Math.Round(piston.CurrentPosition, 2) : (double?)null,
                        maxM = joint is IMyPistonBase p2 ? Math.Round(p2.HighestPosition, 2) : (double?)null,
                    };
                }
                return new
                {
                    id = Dto.Id(g.EntityId),
                    name = Dto.GridName(g),
                    size = Dto.Size(g),
                    blocks = Access.BlockCount(g),
                    joint = holder,
                };
            }).ToList();
        }
    }
}
