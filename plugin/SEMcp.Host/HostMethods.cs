using System;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.Definitions;
using Sandbox.Game.World;
using Sandbox.ModAPI;
using SEMcp.Core;
using VRage.Game.ModAPI;
using VRageMath;

namespace SEMcp.Host
{
    // Methods that need game internals (not ModAPI), so they live in the host rather than Core.
    internal static class HostMethods
    {
        public static void Register(Dispatcher dispatcher)
        {
            var access = new Access(dispatcher);
            dispatcher.Register("sun.info", p => SunInfo(access, p));
            dispatcher.Register("grid.motion", p => Motion(access, p));
        }

        private static object Vec(Vector3D v) => new[] { Math.Round(v.X, 5), Math.Round(v.Y, 5), Math.Round(v.Z, 5) };

        // Sun direction and how each solar panel / oxygen farm faces it, using the game's own formula:
        // exposure = dot(PanelOrientation in world space, direction to sun).
        private static object SunInfo(Access access, JsonObject p)
        {
            Vector3D sun = MySector.DirectionToSunNormalized;
            object panels = null;
            if (p["gridId"] != null)
            {
                var grid = access.Grid(p.Id("gridId"));
                panels = Access.Construct(grid)
                    .SelectMany(g => g.GetFatBlocks<IMyTerminalBlock>())
                    .Select(block => (block, definition: block.SlimBlock?.BlockDefinition as MySolarPanelDefinition))
                    .Where(x => x.definition != null)
                    .Select(x =>
                    {
                        Vector3D normal = Vector3D.TransformNormal(x.definition.PanelOrientation, x.block.WorldMatrix);
                        double dot = Vector3D.Dot(normal, sun);
                        return new
                        {
                            id = Dto.Id(x.block.EntityId),
                            name = x.block.CustomName,
                            normal = Vec(normal),
                            twoSided = x.definition.IsTwoSided,
                            exposure = Math.Round(x.definition.IsTwoSided ? Math.Abs(dot) : Math.Max(0, dot), 4),
                            maxOutputMW = x.definition.MaxPowerOutput,
                            currentMaxMW = Math.Round((x.block as IMyPowerProducer)?.MaxOutput ?? 0, 5),
                        };
                    })
                    .ToList();
            }
            return new { sunDirection = Vec(sun), panels };
        }

        // Orientation and spin of a grid and its gyroscopes, for steering with gyro overrides.
        private static object Motion(Access access, JsonObject p)
        {
            var grid = access.Grid(p.Id("gridId"));
            MatrixD m = grid.WorldMatrix;
            var physics = grid.Physics;
            Vector3 gravity = MyAPIGateway.Physics.CalculateNaturalGravityAt(grid.WorldAABB.Center, out _);
            return new
            {
                isStatic = grid.IsStatic,
                gravityG = Math.Round(gravity.Length() / 9.81, 3),
                forward = Vec(m.Forward),
                up = Vec(m.Up),
                right = Vec(m.Right),
                angularVelocity = Vec(physics?.AngularVelocity ?? Vector3.Zero),
                linearVelocity = Vec(physics?.LinearVelocity ?? Vector3.Zero),
                gyros = Access.Construct(grid)
                    .SelectMany(g => g.GetFatBlocks<IMyGyro>())
                    .Where(access.CanUse)
                    .Select(g => new
                    {
                        id = Dto.Id(g.EntityId),
                        name = g.CustomName,
                        forward = Vec(g.WorldMatrix.Forward),
                        up = Vec(g.WorldMatrix.Up),
                        right = Vec(g.WorldMatrix.Right),
                        overriding = g.GyroOverride,
                        working = g.IsWorking,
                    })
                    .ToList(),
            };
        }
    }
}
