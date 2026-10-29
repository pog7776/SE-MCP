using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using VRageMath;

namespace SEMcp.Core.Services
{
    // The ship's own radio equipment: antennas, beacons and laser antennas. What they hear is radio.signals (host).
    internal sealed class RadioService
    {
        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public RadioService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("radio.antennas", Antennas);
            dispatcher.Register("radio.set", Set);
            dispatcher.Register("laser.connect", LaserConnect);
            dispatcher.Register("laser.idle", LaserIdle);
            dispatcher.Register("los.check", LineOfSight);
            dispatcher.Register("surface.scan", SurfaceScan);
            dispatcher.Register("block.position", BlockPosition);
        }

        // A height map: rays straight down (along -normal) on a grid of points in the plane through origin spanned
        // by u and v. Each cell is the height of the first thing hit above origin along normal, or null for nothing
        // within the depth. One call instead of thousands of los.check round trips. Physics only (grids), no voxels.
        private object SurfaceScan(JsonObject p)
        {
            dispatcher.RequireSession();
            var origin = Point(p, "origin") ?? throw new RpcException(ErrorKind.InvalidParams, "origin is required");
            var n = Vector3D.Normalize(Point(p, "normal") ?? throw new RpcException(ErrorKind.InvalidParams, "normal is required"));
            var u = Vector3D.Normalize(Point(p, "u") ?? throw new RpcException(ErrorKind.InvalidParams, "u is required"));
            var v = Vector3D.Normalize(Point(p, "v") ?? throw new RpcException(ErrorKind.InvalidParams, "v is required"));
            double u0 = p.Number("u0", -10), u1 = p.Number("u1", 10), v0 = p.Number("v0", -10), v1 = p.Number("v1", 10);
            double step = Math.Max(0.25, p.Number("step", 1)), top = p.Number("top", 20), depth = p.Number("depth", 10);
            int nu = (int)Math.Floor((u1 - u0) / step) + 1, nv = (int)Math.Floor((v1 - v0) / step) + 1;
            if ((long)nu * nv > 20000)
                throw new RpcException(ErrorKind.InvalidParams, $"{nu}x{nv} cells is too many (max 20000): use a bigger step or a smaller area");
            var ignore = new HashSet<long>();
            if (p["ignoreId"] != null && MyAPIGateway.Entities.GetEntityById(p.Id("ignoreId")) is VRage.Game.ModAPI.IMyCubeGrid ig)
                foreach (var g in ig.GetGridGroup(VRage.Game.ModAPI.GridLinkTypeEnum.Mechanical)?.GetGrids(new List<VRage.Game.ModAPI.IMyCubeGrid>()) ?? new List<VRage.Game.ModAPI.IMyCubeGrid> { ig })
                    ignore.Add(g.EntityId);
            int layer = (int)p.Number("layer", 30);
            var hits = new List<VRage.Game.ModAPI.IHitInfo>();
            var rows = new List<double?[]>();
            var hazards = new List<string>();  // "i,j,code" for hazardous blocks only, so the reply stays small
            for (int i = 0; i < nu; i++)
            {
                var row = new double?[nv];
                for (int j = 0; j < nv; j++)
                {
                    var q = origin + u * (u0 + i * step) + v * (v0 + j * step);
                    hits.Clear();
                    MyAPIGateway.Physics.CastRay(q + n * top, q - n * depth, hits, layer);
                    var hit = hits.FirstOrDefault(h => h.HitEntity == null || !ignore.Contains(h.HitEntity.EntityId));
                    row[j] = hit == null ? (double?)null : Math.Round(Vector3D.Dot(hit.Position - origin, n), 2);
                    if (hit?.HitEntity is VRage.Game.ModAPI.IMyCubeGrid grid)
                    {
                        var slim = grid.GetCubeBlock(grid.WorldToGridInteger(hit.Position - n * 0.05));
                        string code = slim == null ? null : Hazard(slim);
                        if (code != null)
                            hazards.Add($"{i},{j},{code}");
                    }
                }
                rows.Add(row);
            }
            return new { u0, v0, step, nu, nv, heights = rows, hazards };
        }

        // Blocks that are bad to land on or next to: thrusters (exhaust), tools and weapons, moving doors and joints.
        private static string Hazard(VRage.Game.ModAPI.IMySlimBlock slim)
        {
            switch (slim.FatBlock)
            {
                case IMyThrust _: return "T";
                case IMyUserControllableGun _: return "X";
                case IMyShipDrill _: return "D";
                case IMyShipGrinder _: return "G";
                case IMyShipWelder _: return "W";
                case IMyDoor _: return "H";
                case IMyMechanicalConnectionBlock _: return "M";
                case IMyMotorRotor _: return "M";
                case IMyPistonTop _: return "M";
                default: return null;
            }
        }

        // Where a block is and how it's turned (any terminal block).
        private object BlockPosition(JsonObject p)
        {
            var b = access.Block(p.Id("blockId"));
            return new
            {
                id = Dto.Id(b.EntityId),
                name = b.CustomName,
                gridId = Dto.Id(b.CubeGrid.EntityId),
                position = SensingService.Vec(b.GetPosition(), 2),
                forward = SensingService.Vec(b.WorldMatrix.Forward, 4),
                up = SensingService.Vec(b.WorldMatrix.Up, 4),
            };
        }

        // What a straight line between two points hits first (physics raycast), skipping `skip` meters at each end so
        // the blocks at the ends (a laser antenna, a gun) don't count. For laser links and lines of fire.
        private object LineOfSight(JsonObject p)
        {
            dispatcher.RequireSession();
            var from = Point(p, "from") ?? throw new RpcException(ErrorKind.InvalidParams, "from is required");
            var to = Point(p, "to") ?? throw new RpcException(ErrorKind.InvalidParams, "to is required");
            double skip = p.Number("skip", 2.5);
            var dir = Vector3D.Normalize(to - from);
            double length = Vector3D.Distance(from, to);
            var hits = new List<VRage.Game.ModAPI.IHitInfo>();
            MyAPIGateway.Physics.CastRay(from + dir * skip, to - dir * skip, hits, (int)p.Number("layer", 30));
            // ignoreId: look through one ship (all its grids), e.g. the ship whose landing spot is being checked.
            var ignore = new HashSet<long>();
            if (p["ignoreId"] != null && MyAPIGateway.Entities.GetEntityById(p.Id("ignoreId")) is VRage.Game.ModAPI.IMyCubeGrid ig)
                foreach (var g in ig.GetGridGroup(VRage.Game.ModAPI.GridLinkTypeEnum.Mechanical)?.GetGrids(new List<VRage.Game.ModAPI.IMyCubeGrid>()) ?? new List<VRage.Game.ModAPI.IMyCubeGrid> { ig })
                    ignore.Add(g.EntityId);
            var hit = hits.FirstOrDefault(h => h.HitEntity == null || !ignore.Contains(h.HitEntity.EntityId));
            // Rock first if it comes before any physics hit (asteroids away from ships have no physics to hit).
            var rock = dispatcher.Context.VoxelHit(from + dir * skip, to - dir * skip);
            if (rock != null && (hit == null || Vector3D.Distance(from, rock.Item2) < Vector3D.Distance(from, hit.Position)))
                return new
                {
                    clear = false,
                    distance = Math.Round(length, 1),
                    hit = new
                    {
                        entity = rock.Item1,
                        entityId = (string)null,
                        block = (object)null,
                        position = SensingService.Vec(rock.Item2, 1),
                        fromStart = Math.Round(Vector3D.Distance(from, rock.Item2), 1),
                        voxel = true,
                    },
                };
            if (hit == null)
                return new { clear = true, distance = Math.Round(length, 1) };
            var entity = hit.HitEntity;
            string what = entity?.DisplayName ?? entity?.GetType().Name;
            object block = null;
            if (entity is VRage.Game.ModAPI.IMyCubeGrid grid)
            {
                var cell = grid.WorldToGridInteger(hit.Position + dir * 0.05);
                var slim = grid.GetCubeBlock(cell);
                if (slim != null)
                    block = new
                    {
                        name = (slim.FatBlock as IMyTerminalBlock)?.CustomName ?? slim.BlockDefinition.ToString(),
                        type = Dto.ShortType(slim.BlockDefinition.Id.TypeId.ToString()),
                    };
                what = Dto.GridName(grid);
            }
            return new
            {
                clear = false,
                distance = Math.Round(length, 1),
                hit = new
                {
                    entity = what,
                    entityId = entity != null ? Dto.Id(entity.EntityId) : null,
                    block,
                    position = SensingService.Vec(hit.Position, 1),
                    fromStart = Math.Round(Vector3D.Distance(from, hit.Position), 1),
                },
            };
        }

        private void Log(string message) => dispatcher.Context.Log(message);

        private static Vector3D? Point(JsonObject p, string name) =>
            p[name] is JsonArray a && a.Count == 3
                ? new Vector3D(a[0].GetValue<double>(), a[1].GetValue<double>(), a[2].GetValue<double>())
                : (Vector3D?)null;

        private object Antennas(JsonObject p)
        {
            var grid = access.Grid(p.Id("gridId"));
            var blocks = Access.Construct(grid).SelectMany(g => g.GetFatBlocks<IMyTerminalBlock>())
                .Where(b => b is IMyRadioAntenna || b is IMyBeacon || b is IMyLaserAntenna).Where(access.CanUse)
                .OrderBy(b => b.CustomName, StringComparer.OrdinalIgnoreCase);
            return new
            {
                gridName = Dto.GridName(grid),
                equipment = blocks.Select(b =>
                {
                    var e = new Dictionary<string, object>
                    {
                        ["id"] = Dto.Id(b.EntityId),
                        ["name"] = b.CustomName,
                        ["gridName"] = Dto.GridName(b.CubeGrid),
                        ["enabled"] = (b as IMyFunctionalBlock)?.Enabled,
                        ["working"] = b.IsWorking,
                        // Laser antennas only link to ones their owner may use.
                        ["ownerId"] = Dto.Id(b.OwnerId),
                        ["owner"] = Dto.Relation(b.GetUserRelationToOwner(access.IdentityId)),
                        ["position"] = SensingService.Vec(b.GetPosition(), 1),
                        // How the block is mounted: a laser antenna's head swivels around its base's up.
                        ["forward"] = SensingService.Vec(b.WorldMatrix.Forward, 3),
                        ["up"] = SensingService.Vec(b.WorldMatrix.Up, 3),
                    };
                    switch (b)
                    {
                        case IMyRadioAntenna a:
                            e["kind"] = "antenna";
                            e["radius"] = Math.Round(a.Radius, 0);
                            e["broadcasting"] = a.EnableBroadcasting;
                            e["isBroadcasting"] = a.IsBroadcasting;
                            e["hudText"] = a.HudText;
                            e["showShipName"] = a.ShowShipName;
                            break;
                        case IMyBeacon beacon:
                            e["kind"] = "beacon";
                            e["radius"] = Math.Round(beacon.Radius, 0);
                            e["hudText"] = beacon.HudText;
                            break;
                        case IMyLaserAntenna l:
                            e["kind"] = "laser";
                            e["status"] = l.Status.ToString();
                            e["range"] = l.Range >= 1e8 ? (double?)null : Math.Round(l.Range, 0);
                            e["targetCoords"] = l.TargetCoords == Vector3D.Zero ? null : SensingService.Vec(l.TargetCoords, 1);
                            e["permanent"] = l.IsPermanent;
                            e["requireLoS"] = l.RequireLoS;
                            e["outsideLimits"] = l.IsOutsideLimits;
                            e["other"] = l.Other == null ? null : new
                            {
                                id = Dto.Id(l.Other.EntityId),
                                name = l.Other.CustomName,
                                gridName = Dto.GridName(l.Other.CubeGrid),
                            };
                            break;
                    }
                    return e;
                }).ToList(),
            };
        }

        // Antenna and beacon settings: on/off, broadcast radius, HUD text, broadcasting, ship name on the HUD.
        private object Set(JsonObject p)
        {
            dispatcher.RequireWritable();
            var block = access.Block(p.Id("blockId"));
            if (p["enabled"] != null && block is IMyFunctionalBlock f)
                f.Enabled = p.Bool("enabled", f.Enabled);
            switch (block)
            {
                case IMyRadioAntenna a:
                    if (p["radius"] != null) a.Radius = (float)p.Number("radius", a.Radius);
                    if (p["broadcasting"] != null) a.EnableBroadcasting = p.Bool("broadcasting", a.EnableBroadcasting);
                    if (p.String("hudText") is string text) a.HudText = text;
                    if (p["showShipName"] != null) a.ShowShipName = p.Bool("showShipName", a.ShowShipName);
                    break;
                case IMyBeacon beacon:
                    if (p["radius"] != null) beacon.Radius = (float)p.Number("radius", beacon.Radius);
                    if (p.String("hudText") is string hud) beacon.HudText = hud;
                    break;
                default:
                    if (p["enabled"] == null)
                        throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' is not an antenna or beacon");
                    break;
            }
            Log($"radio.set '{block.CustomName}'");
            return Antennas(new JsonObject { ["gridId"] = block.CubeGrid.EntityId.ToString() });
        }

        // Aim a laser antenna at coordinates (another laser antenna there) and connect, like its terminal does.
        private object LaserConnect(JsonObject p)
        {
            dispatcher.RequireWritable();
            var laser = access.Block(p.Id("blockId")) as IMyLaserAntenna
                ?? throw new RpcException(ErrorKind.InvalidParams, "Not a laser antenna");
            var target = Point(p, "target");
            if (target.HasValue)
                laser.SetTargetCoords($"GPS:Target:{target.Value.X}:{target.Value.Y}:{target.Value.Z}:");
            if (p["permanent"] != null)
                laser.IsPermanent = p.Bool("permanent", laser.IsPermanent);
            laser.Connect();
            Log($"laser.connect '{laser.CustomName}'");
            return new { block = laser.CustomName, status = laser.Status.ToString() };
        }

        private object LaserIdle(JsonObject p)
        {
            dispatcher.RequireWritable();
            var laser = access.Block(p.Id("blockId")) as IMyLaserAntenna
                ?? throw new RpcException(ErrorKind.InvalidParams, "Not a laser antenna");
            var idle = laser.GetActionWithName("Idle")
                ?? throw new RpcException(ErrorKind.GameError, "The laser antenna has no Idle action");
            idle.Apply(laser);
            Log($"laser.idle '{laser.CustomName}'");
            return new { block = laser.CustomName, status = laser.Status.ToString() };
        }
    }
}
