using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using VRage.Game.ModAPI;
using VRageMath;
using MyDetectedEntityInfo = Sandbox.ModAPI.Ingame.MyDetectedEntityInfo;

namespace SEMcp.Core.Services
{
    // Camera blocks (list, raycast scans) and sensor blocks: what the ship itself can perceive.
    internal sealed class SensingService
    {
        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public SensingService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("camera.list", CameraList);
            dispatcher.Register("camera.raycast", Raycast);
            dispatcher.Register("sensors.get", Sensors);
        }

        internal static double[] Vec(Vector3D v, int digits = 2) =>
            new[] { Math.Round(v.X, digits), Math.Round(v.Y, digits), Math.Round(v.Z, digits) };

        private IEnumerable<T> OnConstruct<T>(JsonObject p) where T : class, IMyTerminalBlock
        {
            var grid = access.Grid(p.Id("gridId"));
            return Access.Construct(grid).SelectMany(g => g.GetFatBlocks<T>()).Where(access.CanUse)
                .OrderBy(b => b.CustomName, StringComparer.OrdinalIgnoreCase);
        }

        private object CameraList(JsonObject p)
        {
            Vector3D origin = access.Origin;
            return new
            {
                cameras = OnConstruct<IMyCameraBlock>(p).Select(c => new
                {
                    id = Dto.Id(c.EntityId),
                    name = c.CustomName,
                    gridName = Dto.GridName(c.CubeGrid),
                    working = c.IsWorking,
                    // The player is looking through it right now.
                    active = c.IsActive,
                    raycastEnabled = c.EnableRaycast,
                    scanRange = Math.Round(c.AvailableScanRange, 0),
                    position = Vec(c.GetPosition()),
                    forward = Vec(c.WorldMatrix.Forward, 3),
                    up = Vec(c.WorldMatrix.Up, 3),
                    distance = Math.Round(Vector3D.Distance(origin, c.GetPosition()), 1),
                }).ToList(),
            };
        }

        internal static object Detected(MyDetectedEntityInfo info, Vector3D from)
        {
            if (info.IsEmpty())
                return null;
            var box = info.BoundingBox;
            return new
            {
                id = Dto.Id(info.EntityId),
                name = info.Name,
                type = info.Type.ToString(),
                relation = Dto.Relation(info.Relationship),
                position = Vec(info.Position),
                hitPosition = info.HitPosition.HasValue ? Vec(info.HitPosition.Value) : null,
                distance = Math.Round(Vector3D.Distance(from, info.HitPosition ?? info.Position), 1),
                size = Vec(box.Max - box.Min, 1),
                speed = Math.Round(info.Velocity.Length(), 1),
                velocity = Vec(info.Velocity, 1),
            };
        }

        // A camera block's own raycast: limited by its charge (scan range builds up over time while enabled),
        // like a PB scan. Enables raycasting on the camera if needed, which starts the charge.
        private object Raycast(JsonObject p)
        {
            dispatcher.RequireWritable();
            var block = access.Block(p.Id("blockId"));
            var camera = block as IMyCameraBlock
                ?? throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' is not a camera");
            if (!camera.IsWorking)
                throw new RpcException(ErrorKind.InvalidParams, $"'{camera.CustomName}' is off, unpowered or damaged");

            bool wasEnabled = camera.EnableRaycast;
            if (!wasEnabled)
                camera.EnableRaycast = true;

            double distance = Math.Max(1, p.Number("distance", 2000));
            float pitch = (float)p.Number("pitch", 0), yaw = (float)p.Number("yaw", 0);
            Vector3D? target = p["target"] is JsonArray a && a.Count == 3
                ? new Vector3D(a[0].GetValue<double>(), a[1].GetValue<double>(), a[2].GetValue<double>())
                : (Vector3D?)null;
            if (target.HasValue)
                distance = Vector3D.Distance(camera.GetPosition(), target.Value);

            bool canScan = target.HasValue ? camera.CanScan(target.Value) : camera.CanScan(distance);
            object hit = null;
            if (canScan)
            {
                var info = target.HasValue ? camera.Raycast(target.Value) : camera.Raycast(distance, pitch, yaw);
                hit = Detected(info, camera.GetPosition());
            }
            return new
            {
                camera = camera.CustomName,
                enabledRaycast = !wasEnabled,
                scanned = canScan,
                distance = Math.Round(distance, 0),
                hit,
                scanRangeLeft = Math.Round(camera.AvailableScanRange, 0),
                // Why a scan couldn't happen: not charged enough, or outside the camera's cone.
                msUntilScan = canScan ? 0 : camera.TimeUntilScan(distance),
                coneLimitDeg = camera.RaycastConeLimit,
                maxDistance = camera.RaycastDistanceLimit < 0 ? (double?)null : camera.RaycastDistanceLimit,
            };
        }

        private object Sensors(JsonObject p)
        {
            IEnumerable<IMySensorBlock> sensors = p["blockId"] != null
                ? new[] { access.Block(p.Id("blockId")) as IMySensorBlock ?? throw new RpcException(ErrorKind.InvalidParams, "Not a sensor block") }
                : OnConstruct<IMySensorBlock>(p);
            var detected = new List<MyDetectedEntityInfo>();
            return new
            {
                sensors = sensors.Select(s =>
                {
                    detected.Clear();
                    s.DetectedEntities(detected);
                    var detects = new List<string>();
                    if (s.DetectPlayers) detects.Add("players");
                    if (s.DetectSmallShips) detects.Add("small ships");
                    if (s.DetectLargeShips) detects.Add("large ships");
                    if (s.DetectStations) detects.Add("stations");
                    if (s.DetectSubgrids) detects.Add("subgrids");
                    if (s.DetectFloatingObjects) detects.Add("floating objects");
                    if (s.DetectAsteroids) detects.Add("asteroids");
                    var who = new List<string>();
                    if (s.DetectOwner) who.Add("owner");
                    if (s.DetectFriendly) who.Add("friendly");
                    if (s.DetectNeutral) who.Add("neutral");
                    if (s.DetectEnemy) who.Add("enemy");
                    return new
                    {
                        id = Dto.Id(s.EntityId),
                        name = s.CustomName,
                        working = s.IsWorking,
                        active = s.IsActive,
                        // Field size in meters from the block: left/right/top/bottom/front/back.
                        extents = new[] { s.LeftExtend, s.RightExtend, s.TopExtend, s.BottomExtend, s.FrontExtend, s.BackExtend }.Select(x => Math.Round(x, 1)).ToArray(),
                        maxRange = s.MaxRange,
                        detects,
                        relations = who,
                        detected = detected.Select(d => Detected(d, s.GetPosition())).Where(d => d != null).ToList(),
                    };
                }).ToList(),
            };
        }
    }
}
