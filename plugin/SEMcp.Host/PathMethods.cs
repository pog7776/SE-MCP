using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.Game.AI;
using Sandbox.Game.AI.Pathfinding.RecastDetour;
using Sandbox.ModAPI;
using SEMcp.Core;
using VRage;
using VRage.Game.AI;
using VRage.Game.ModAPI;
using VRageMath;

namespace SEMcp.Host
{
    // The game's own pathfinders, for guiding the player:
    // - fly: the AI blocks' avoidance pathfinder (IMyPathfindingSessionComponent.FindAvoidPath). On first use it builds
    //   an octree of the free space in and around a grid (doors included) in the background, so the first calls
    //   answer "generating" and the caller asks again.
    // - walk: the bots' navmesh (MyRDPathfinding, Recast/Detour) over planet surfaces and grids with gravity.
    // Internals (MyServiceManager, MyAIComponent), so host-only.
    internal static class PathMethods
    {
        public static void Register(Dispatcher dispatcher)
        {
            var access = new Access(dispatcher);
            dispatcher.Register("path.find", p => Find(access, dispatcher, p));
        }

        private static object Find(Access access, Dispatcher dispatcher, JsonObject p)
        {
            dispatcher.RequireSession();
            var from = Point(p, "from") ?? throw new RpcException(ErrorKind.InvalidParams, "Give from: [x, y, z]");
            var to = Point(p, "to") ?? throw new RpcException(ErrorKind.InvalidParams, "Give to: [x, y, z]");
            string mode = (p.String("mode") ?? "fly").ToLowerInvariant();
            if (mode == "walk")
                return Walk(from, to);
            if (mode != "fly")
                throw new RpcException(ErrorKind.InvalidParams, "mode is fly or walk");

            var grid = access.Grid(p.Id("gridId"));
            float radius = (float)MathHelper.Clamp(p.Number("radius", 1), 0.1, 500);
            var service = MyServiceManager.Instance.GetService<IMyPathfindingSessionComponent>()
                ?? throw new RpcException(ErrorKind.GameError, "The autopilot pathfinding service isn't running");
            var path = service.FindAvoidPath(grid, from, to, radius, out MyAutopilotPathfindingState state);
            var points = new List<Vector3D>();
            if (state == MyAutopilotPathfindingState.PathFound && path != null)
                for (int i = 0; i < path.Count; i++)
                    if (path[i].Vertex is MyPathVertex v)
                        points.Add(v.WorldPosition);
            return new
            {
                mode,
                state = state.ToString(),
                found = state == MyAutopilotPathfindingState.PathFound,
                // Asking again shortly is the answer to these two.
                retry = state == MyAutopilotPathfindingState.OctreeGenerating,
                points = points.Select(Vec).ToList(),
                length = Math.Round(Length(from, points, to), 1),
            };
        }

        private static object Walk(Vector3D from, Vector3D to)
        {
            if (!(MyAIComponent.Static?.Pathfinding is MyRDPathfinding navmesh))
                throw new RpcException(ErrorKind.GameError, "The navmesh pathfinder isn't running (AI disabled in this world?)");
            var area = navmesh.GetNavigationArea(from);
            if (area == null)
                return new { mode = "walk", state = "NoNavigationArea", found = false, retry = false, points = new List<double[]>(), length = 0.0 };
            var points = navmesh.GetPath(area, from, to, out bool allTiles) ?? new List<Vector3D>();
            return new
            {
                mode = "walk",
                state = points.Count > 0 ? "PathFound" : allTiles ? "PathNotFound" : "TilesGenerating",
                found = points.Count > 0,
                retry = points.Count == 0 && !allTiles,
                points = points.Select(Vec).ToList(),
                length = Math.Round(Length(from, points, to), 1),
            };
        }

        private static double Length(Vector3D from, List<Vector3D> points, Vector3D to)
        {
            if (points.Count == 0)
                return 0;
            double total = Vector3D.Distance(from, points[0]);
            for (int i = 1; i < points.Count; i++)
                total += Vector3D.Distance(points[i - 1], points[i]);
            return total;
        }

        private static double[] Vec(Vector3D v) => new[] { Math.Round(v.X, 2), Math.Round(v.Y, 2), Math.Round(v.Z, 2) };

        private static Vector3D? Point(JsonObject p, string name) =>
            p[name] is JsonArray a && a.Count == 3
                ? new Vector3D(a[0].GetValue<double>(), a[1].GetValue<double>(), a[2].GetValue<double>())
                : (Vector3D?)null;
    }
}
