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
