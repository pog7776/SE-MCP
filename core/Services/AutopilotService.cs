using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using Sandbox.ModAPI.Interfaces;
using VRage.Game.ModAPI;
using VRageMath;
using FlightMode = Sandbox.ModAPI.Ingame.FlightMode;
using MyWaypointInfo = Sandbox.ModAPI.Ingame.MyWaypointInfo;

namespace SEMcp.Core.Services
{
    // Remote Control autopilot and the player's GPS list.
    internal sealed class AutopilotService
    {
        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public AutopilotService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("rc.get", Get);
            dispatcher.Register("rc.fly_to", FlyTo);
            dispatcher.Register("rc.stop", Stop);
            dispatcher.Register("gps.list", GpsList);
            dispatcher.Register("gps.add", GpsAdd);
            dispatcher.Register("gps.remove", GpsRemove);
        }

        private void Log(string message) => dispatcher.Context.Log(message);

        private static double[] Vec(Vector3D v) => new[] { Math.Round(v.X, 2), Math.Round(v.Y, 2), Math.Round(v.Z, 2) };

        private static Vector3D ToVector(JsonNode node, string what)
        {
            if (node is JsonArray a && a.Count == 3)
                return new Vector3D(a[0].GetValue<double>(), a[1].GetValue<double>(), a[2].GetValue<double>());
            if (node is JsonObject o && o["x"] != null && o["y"] != null && o["z"] != null)
                return new Vector3D(o["x"].GetValue<double>(), o["y"].GetValue<double>(), o["z"].GetValue<double>());
            throw new RpcException(ErrorKind.InvalidParams, $"{what} must be [x,y,z] or {{x,y,z}}");
        }

        private IMyRemoteControl Rc(JsonObject p)
        {
            var block = access.Block(p.Id("blockId"));
            return block as IMyRemoteControl
                ?? throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' is not a remote control block");
        }

        private static bool? BoolProperty(IMyTerminalBlock block, string id) =>
            block.GetProperty(id)?.As<bool>()?.GetValue(block);

        private object State(IMyRemoteControl rc)
        {
            var waypoints = new List<MyWaypointInfo>();
            rc.GetWaypointInfo(waypoints);
            Vector3D position = rc.GetPosition();
            var current = rc.CurrentWaypoint;
            return new
            {
                id = Dto.Id(rc.EntityId),
                name = rc.CustomName,
                gridId = Dto.Id(rc.CubeGrid.EntityId),
                gridName = Dto.GridName(rc.CubeGrid),
                working = rc.IsWorking,
                autopilot = rc.IsAutoPilotEnabled,
                // RC "Control thrusters"/"Control wheels" etc. aren't exposed; these are the autopilot settings.
                flightMode = rc.FlightMode.ToString(),
                direction = rc.Direction.ToString(),
                speedLimit = Math.Round(rc.SpeedLimit, 1),
                collisionAvoidance = BoolProperty(rc, "CollisionAvoidance"),
                dockingMode = BoolProperty(rc, "DockingMode"),
                position = Vec(position),
                // Autopilot turns the ship so this points at the waypoint (with direction = Forward).
                forward = Vec(rc.WorldMatrix.Forward),
                up = Vec(rc.WorldMatrix.Up),
                right = Vec(rc.WorldMatrix.Right),
                // The RC face that matches the ship's front (its main or first cockpit), so autopilot flies nose-first.
                frontDirection = FrontDirection(rc, out string frontReference)?.ToString(),
                frontReference,
                speed = Math.Round(rc.GetShipSpeed(), 2),
                gravityG = Math.Round(rc.GetNaturalGravity().Length() / 9.81, 3),
                currentWaypoint = current.IsEmpty() ? null : new { name = current.Name, coords = Vec(current.Coords), distance = Math.Round(Vector3D.Distance(position, current.Coords), 1) },
                waypoints = waypoints.Select(w => new { name = w.Name, coords = Vec(w.Coords) }).ToList(),
                // Someone is driving the ship through this RC; the game won't engage autopilot then.
                remoteControlled = rc.IsUnderControl,
            };
        }

        // Which of the RC's six faces points where the ship's cockpit faces. Null when the construct has no cockpit.
        private static Base6Directions.Direction? FrontDirection(IMyRemoteControl rc, out string reference)
        {
            var cockpit = Access.Construct(rc.CubeGrid)
                .SelectMany(g => g.GetFatBlocks<IMyCockpit>())
                .Where(c => c.CanControlShip && !(c is IMyCryoChamber))
                .OrderByDescending(c => c.IsMainCockpit)
                .ThenByDescending(c => c.IsUnderControl)
                .FirstOrDefault();
            reference = cockpit?.CustomName;
            if (cockpit == null)
                return null;
            Vector3D front = cockpit.WorldMatrix.Forward;
            return Base6Directions.EnumDirections
                .OrderByDescending(d => Vector3D.Dot(rc.WorldMatrix.GetDirectionVector(d), front))
                .First();
        }

        private object Get(JsonObject p) => State(Rc(p));

        private void RequireAutopilot()
        {
            dispatcher.RequireWritable();
            if (!dispatcher.Context.AllowAutopilot)
                throw new RpcException(ErrorKind.FeatureDisabled, "Autopilot is turned off (allowAutopilot in config.json)");
        }

        // Replaces the RC's waypoints and engages autopilot, like setting it up in the terminal.
        private object FlyTo(JsonObject p)
        {
            RequireAutopilot();
            var rc = Rc(p);
            if (!rc.IsWorking)
                throw new RpcException(ErrorKind.InvalidParams, $"'{rc.CustomName}' is off, unpowered or damaged");

            var points = new List<MyWaypointInfo>();
            if (p["waypoints"] is JsonArray list)
            {
                int i = 0;
                foreach (var node in list)
                {
                    i++;
                    var o = node as JsonObject ?? throw new RpcException(ErrorKind.InvalidParams, "waypoints[] must be objects {name?, coords}");
                    points.Add(new MyWaypointInfo(o.String("name") ?? $"Waypoint {i}", ToVector(o["coords"], "coords")));
                }
            }
            else
            {
                points.Add(new MyWaypointInfo(p.String("name") ?? "Claude", ToVector(p["target"], "target")));
            }
            if (points.Count == 0)
                throw new RpcException(ErrorKind.InvalidParams, "Give target or waypoints");

            string mode = p.String("mode") ?? "OneWay";
            if (!Enum.TryParse(mode, true, out FlightMode flightMode))
                throw new RpcException(ErrorKind.InvalidParams, $"mode must be one of {string.Join(", ", Enum.GetNames(typeof(FlightMode)))}");

            rc.SetAutoPilotEnabled(false);
            rc.ClearWaypoints();
            foreach (var w in points)
                rc.AddWaypoint(w);
            rc.FlightMode = flightMode;
            string direction = p.String("direction") ?? "auto";
            if (direction.Equals("auto", StringComparison.OrdinalIgnoreCase))
            {
                if (FrontDirection(rc, out _) is Base6Directions.Direction front)
                    rc.Direction = front;
            }
            else if (Enum.TryParse(direction, true, out Base6Directions.Direction leading))
                rc.Direction = leading;
            else
                throw new RpcException(ErrorKind.InvalidParams, "direction must be auto, Forward, Backward, Left, Right, Up or Down");
            if (p["speedLimit"] != null)
                rc.SpeedLimit = (float)Math.Max(1, p.Number("speedLimit", 100));
            rc.SetCollisionAvoidance(p.Bool("collisionAvoidance", true));
            rc.SetDockingMode(p.Bool("precision", false));
            rc.SetAutoPilotEnabled(true);

            Log($"autopilot on '{rc.CustomName}' ({rc.EntityId}): {points.Count} waypoint(s), {flightMode}, enabled={rc.IsAutoPilotEnabled}");
            if (!rc.IsAutoPilotEnabled)
                throw new RpcException(ErrorKind.GameError,
                    rc.IsUnderControl
                        ? $"'{rc.CustomName}' is being remote-controlled, so autopilot can't engage"
                        : $"The game refused to engage autopilot on '{rc.CustomName}'");
            return State(rc);
        }

        private object Stop(JsonObject p)
        {
            dispatcher.RequireWritable();
            var rc = Rc(p);
            rc.SetAutoPilotEnabled(false);
            if (p.Bool("clearWaypoints", false))
                rc.ClearWaypoints();
            Log($"autopilot off on '{rc.CustomName}' ({rc.EntityId})");
            return State(rc);
        }

        // --- GPS (the local player's list) ---

        private object Gps(IMyGps gps) => new
        {
            hash = gps.Hash,
            name = gps.Name,
            description = gps.Description,
            coords = Vec(gps.Coords),
            distance = Math.Round(Vector3D.Distance(access.Origin, gps.Coords), 1),
            showOnHud = gps.ShowOnHud,
            color = $"#{gps.GPSColor.R:X2}{gps.GPSColor.G:X2}{gps.GPSColor.B:X2}",
        };

        private List<IMyGps> AllGps() => MyAPIGateway.Session.GPS.GetGpsList(access.IdentityId) ?? new List<IMyGps>();

        private object GpsList(JsonObject p)
        {
            dispatcher.RequireSession();
            var origin = access.Origin;
            return new { gps = AllGps().OrderBy(g => Vector3D.DistanceSquared(origin, g.Coords)).Select(Gps).ToList() };
        }

        private object GpsAdd(JsonObject p)
        {
            dispatcher.RequireWritable();
            dispatcher.RequireSession();
            string name = p.String("name", required: true);
            var gps = MyAPIGateway.Session.GPS.Create(name, p.String("description") ?? "", ToVector(p["coords"], "coords"), p.Bool("showOnHud", true));
            MyAPIGateway.Session.GPS.AddGps(access.IdentityId, gps);
            Log($"gps add '{name}' {gps.Coords}");
            return Gps(gps);
        }

        private object GpsRemove(JsonObject p)
        {
            dispatcher.RequireWritable();
            dispatcher.RequireSession();
            var all = AllGps();
            var gps = p["hash"] != null
                ? all.FirstOrDefault(g => g.Hash == (int)p.Number("hash", 0))
                : all.FirstOrDefault(g => string.Equals(g.Name, p.String("name", required: true), StringComparison.OrdinalIgnoreCase));
            if (gps == null)
                throw new RpcException(ErrorKind.NotFound, "No such GPS entry");
            MyAPIGateway.Session.GPS.RemoveGps(access.IdentityId, gps);
            Log($"gps remove '{gps.Name}'");
            return new { removed = gps.Name };
        }
    }
}
