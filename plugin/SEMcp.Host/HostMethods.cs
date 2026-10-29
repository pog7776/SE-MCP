using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text.Json.Nodes;
using Sandbox.Definitions;
using Sandbox.Game;
using Sandbox.Game.Entities.Blocks;
using Sandbox.Game.GameSystems;
using Sandbox.Game.GameSystems.Conveyors;
using Sandbox.Game.SessionComponents;
using VRage.Game;
using VRage.Game.Entity;
using VRage.Game.ObjectBuilders.Gui;
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
            dispatcher.Register("buildplanner.get", p => BuildPlanner(dispatcher));
            dispatcher.Register("highlight.set", p => Highlight(access, dispatcher, p));
            dispatcher.Register("highlight.clear", p => ClearHighlights(p));
        }

        // Entities we've highlighted, so they can be cleared together (and on host stop).
        private static readonly HashSet<long> Highlighted = new HashSet<long>();

        private static MyHighlightSystem HighlightSystem =>
            MySession.Static?.GetComponent<MyHighlightSystem>()
            ?? throw new RpcException(ErrorKind.NoSession, "No highlight system (no world loaded?)");

        private static void SetHighlight(long entityId, Color color, int thickness, ulong pulseFrames)
        {
            HighlightSystem.RequestHighlightChange(new MyHighlightData
            {
                EntityId = entityId,
                OutlineColor = color,
                Thickness = thickness, // -1 turns it off
                PulseTimeInFrames = pulseFrames,
                PlayerId = -1, // the local player only
                IgnoreUseObjectData = true,
            });
        }

        // Makes blocks glow for the local player only (the scenario highlight system). Blocks that aren't
        // entities (plain armor) can't glow; those are reported back.
        private static object Highlight(Access access, Dispatcher dispatcher, JsonObject p)
        {
            dispatcher.RequireSession();
            var ids = (p["blockIds"] as JsonArray ?? throw new RpcException(ErrorKind.InvalidParams, "Give blockIds"))
                .Select(n => long.Parse(n.ToString())).ToList();
            var color = p["color"] != null ? ColorParam.Parse(p["color"]) : Color.Yellow;
            int thickness = (int)MathHelper.Clamp(p.Number("thickness", 8), 1, 30);
            ulong pulse = (ulong)MathHelper.Clamp(p.Number("pulseFrames", 60), 0, 600);
            var lit = new List<string>();
            var skipped = new List<string>();
            foreach (long id in ids)
            {
                IMyTerminalBlock block;
                try
                {
                    block = access.Block(id);
                }
                catch (RpcException e)
                {
                    skipped.Add($"{id}: {e.Message}");
                    continue;
                }
                SetHighlight(block.EntityId, color, thickness, pulse);
                Highlighted.Add(block.EntityId);
                lit.Add(block.CustomName);
            }
            return new { highlighted = lit, skipped };
        }

        private static object ClearHighlights(JsonObject p)
        {
            var ids = p["blockIds"] is JsonArray list ? list.Select(n => long.Parse(n.ToString())).ToList() : Highlighted.ToList();
            int cleared = 0;
            foreach (long id in ids)
            {
                if (MySession.Static == null)
                    break;
                SetHighlight(id, Color.Transparent, -1, 0);
                Highlighted.Remove(id);
                cleared++;
            }
            return new { cleared };
        }

        // Host stop (hot reload, world unload): don't leave blocks glowing with nothing to switch them off.
        public static void ClearAllHighlights()
        {
            try
            {
                if (MySession.Static != null)
                    ClearHighlights(new JsonObject());
            }
            catch (Exception)
            {
                Highlighted.Clear();
            }
        }

        private static readonly FieldInfo CompilerErrorsField =
            typeof(MyProgrammableBlock).GetField("m_compilerErrors", BindingFlags.Instance | BindingFlags.NonPublic);

        // MyProgrammableBlock.m_compilerErrors: filled by each compile (server side), shown only in the code editor.
        public static IList<string> PbCompilerErrors(Sandbox.ModAPI.IMyProgrammableBlock pb) =>
            CompilerErrorsField?.GetValue(pb) is List<string> errors ? errors.ToList() : null;

        // The player's build planner (G menu: "add to build planner"): blocks they mean to build and the components
        // each still needs. Stored on the identity, which ModAPI doesn't expose.
        private static object BuildPlanner(Dispatcher dispatcher)
        {
            dispatcher.RequireSession();
            var character = MySession.Static.LocalCharacter
                ?? throw new RpcException(ErrorKind.NotFound, "The player has no character (dead or spectating?)");
            return new
            {
                items = character.BuildPlanner.Select((item, index) => new
                {
                    index,
                    block = item.BlockDefinition == null ? null : (item.BlockDefinition.DisplayNameText ?? item.BlockDefinition.Id.SubtypeName).Split('\n')[0].Trim(),
                    blockId = item.BlockDefinition == null ? null : $"{Dto.ShortType(item.BlockDefinition.Id.TypeId.ToString())}/{item.BlockDefinition.Id.SubtypeName}",
                    size = item.BlockDefinition?.CubeSize == MyCubeSize.Large ? "large" : "small",
                    inProgress = item.IsInProgress,
                    components = (item.Components ?? new List<MyIdentity.BuildPlanItem.Component>())
                        .Where(c => c.ComponentDefinition != null && c.Count > 0)
                        .Select(c => new { type = "Component", subtype = c.ComponentDefinition.Id.SubtypeName, count = c.Count })
                        .ToList(),
                }).ToList(),
            };
        }

        private static IMyConveyorEndpointBlock Endpoint(VRage.ModAPI.IMyEntity entity)
        {
            if (entity is IMyConveyorEndpointBlock endpoint)
                return endpoint;
            return entity is MyEntity e && e.Components.TryGet<IMyConveyorEndpointBlock>(out var component) ? component : null;
        }

        // The inventory screen's rule: an item can go from A to B if the conveyor network connects them with
        // conveyors big enough for that item.
        public static bool? CanConvey(VRage.ModAPI.IMyEntity from, VRage.ModAPI.IMyEntity to, VRage.Game.ModAPI.Ingame.MyItemType item)
        {
            var start = Endpoint(from);
            var end = Endpoint(to);
            if (start == null || end == null)
                return false;
            return MyGridConveyorSystem.ComputeCanTransfer(start, end, (MyDefinitionId)item);
        }

        // Same call the inventory screen makes when the player drags an item (a validated request to the server).
        public static void TransferByUser(VRage.Game.ModAPI.IMyInventory src, VRage.Game.ModAPI.IMyInventory dst, uint itemId, VRage.MyFixedPoint amount) =>
            MyInventory.TransferByUser((MyInventory)src, (MyInventory)dst, itemId, -1, amount);

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
            var construct = Access.Construct(grid);
            var box = BoundingBoxD.CreateInvalid();
            foreach (var g in construct)
                box.Include(g.WorldAABB);
            // The ship's "front" as the pilot sees it: the grid's own axes often differ from the cockpit's.
            var cockpit = construct.SelectMany(g => g.GetFatBlocks<IMyCockpit>())
                .Where(c => c.CanControlShip && !(c is IMyCryoChamber))
                .OrderByDescending(c => c.IsMainCockpit)
                .ThenByDescending(c => c.IsUnderControl)
                .FirstOrDefault();
            return new
            {
                center = Vec(box.Center),
                radius = Math.Round(box.HalfExtents.Length(), 1),
                cockpit = cockpit == null ? null : new { name = cockpit.CustomName, forward = Vec(cockpit.WorldMatrix.Forward), up = Vec(cockpit.WorldMatrix.Up) },
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
