using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using VRage.Game;
using VRage.Game.ModAPI;
using VRage.Game.ModAPI.Interfaces;
using VRage.ModAPI;
using VRage.Game.ObjectBuilders.Definitions;
using VRageMath;

namespace SEMcp.Core.Services
{
    internal sealed class PlayerService
    {
        private const double LookRange = 200;
        private static readonly MyDefinitionId Hydrogen = new MyDefinitionId(typeof(MyObjectBuilder_GasProperties), "Hydrogen");

        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public PlayerService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("player.context", _ => Context());
            dispatcher.Register("character.status", _ => CharacterStatus());
            dispatcher.Register("character.switch", Switch);
        }

        // Suit toggles, like pressing the keys: helmet (J), jetpack (X), dampeners (Z), lights (L). Only the ones
        // given are touched, and only if they differ. The character's own Switch* calls sync like player input.
        private object Switch(JsonObject p)
        {
            dispatcher.RequireWritable();
            var character = access.Player.Character
                ?? throw new RpcException(ErrorKind.NotFound, "The player has no character (dead or spectating?)");
            var controllable = (VRage.Game.ModAPI.Interfaces.IMyControllableEntity)character;
            var changed = new List<string>();
            void Apply(string key, bool current, Action toggle)
            {
                if (p[key] == null)
                    return;
                bool wanted = p.Bool(key, current);
                if (wanted == current)
                    return;
                toggle();
                changed.Add($"{key} {(wanted ? "on" : "off")}");
            }
            Apply("helmet", controllable.EnabledHelmet, controllable.SwitchHelmet);
            Apply("jetpack", controllable.EnabledThrusts, controllable.SwitchThrusts);
            Apply("dampeners", controllable.EnabledDamping, controllable.SwitchDamping);
            Apply("lights", controllable.EnabledLights, controllable.SwitchLights);
            if (changed.Count > 0)
                dispatcher.Context.Log("suit: " + string.Join(", ", changed));
            return new
            {
                changed,
                helmet = controllable.EnabledHelmet,
                jetpack = controllable.EnabledThrusts,
                dampeners = controllable.EnabledDamping,
                lights = controllable.EnabledLights,
            };
        }

        // What the player is controlling and looking at: lets tools default to "this ship" / "that block".
        private object Context()
        {
            var player = access.Player;
            var controlled = player.Controller?.ControlledEntity?.Entity;

            object controlledInfo = null;
            if (controlled is IMyCubeBlock block)
            {
                controlledInfo = new
                {
                    kind = Dto.ShortType(block.BlockDefinition.TypeIdString),
                    blockId = Dto.Id(block.EntityId),
                    blockName = (block as IMyTerminalBlock)?.CustomName ?? block.DisplayNameText,
                    gridId = Dto.Id(block.CubeGrid.EntityId),
                    gridName = Dto.GridName(block.CubeGrid),
                };
            }
            else if (controlled is IMyCharacter)
            {
                controlledInfo = new { kind = "Character" };
            }

            return new
            {
                player = player.DisplayName,
                identityId = Dto.Id(player.IdentityId),
                position = Dto.Vector(access.Origin),
                controlled = controlledInfo,
                lookingAt = LookingAt(),
                nearestGrid = NearestGrid(),
            };
        }

        private object LookingAt()
        {
            var camera = MyAPIGateway.Session.Camera;
            if (camera == null)
                return null;

            Vector3D from = camera.WorldMatrix.Translation;
            Vector3D direction = camera.WorldMatrix.Forward;
            // Start a little ahead so first-person rays don't hit the player's own character.
            Vector3D start = from + direction * 0.5;
            Vector3D end = from + direction * LookRange;
            if (!MyAPIGateway.Physics.CastRay(start, end, out IHitInfo hit) || hit?.HitEntity == null)
                return null;

            double distance = Math.Round(Vector3D.Distance(from, hit.Position), 1);
            IMyEntity entity = hit.HitEntity.GetTopMostParent();

            if (entity is IMyCubeGrid grid)
            {
                var relation = access.Relation(grid);
                object blockInfo = null;
                Vector3I? cell = grid.RayCastBlocks(start, end);
                var slim = cell.HasValue ? grid.GetCubeBlock(cell.Value) : null;
                if (slim != null)
                {
                    var terminal = slim.FatBlock as IMyTerminalBlock;
                    blockInfo = new
                    {
                        id = slim.FatBlock != null ? Dto.Id(slim.FatBlock.EntityId) : null,
                        name = terminal?.CustomName ?? slim.BlockDefinition.DisplayNameText,
                        type = Dto.ShortType(slim.BlockDefinition.Id.TypeId.ToString()),
                        integrity = Dto.Integrity(slim),
                        accessible = terminal != null && access.CanUse(terminal),
                    };
                }

                return new
                {
                    kind = "grid",
                    distance,
                    gridId = Dto.Id(grid.EntityId),
                    gridName = Dto.GridName(grid),
                    relation = Dto.Relation(relation),
                    block = blockInfo,
                };
            }

            if (entity is IMyCharacter character)
                return new { kind = "character", distance, name = character.DisplayName };
            if (entity is IMyVoxelBase voxel)
                return new { kind = "voxel", distance, name = voxel.StorageName };
            return new { kind = "other", distance, name = entity.DisplayName };
        }

        // The closest usable grid within 50 m: usually the ship or station the player is standing in or on.
        private object NearestGrid()
        {
            var entities = new System.Collections.Generic.HashSet<IMyEntity>();
            Vector3D origin = access.Origin;
            var sphere = new BoundingSphereD(origin, 50);
            foreach (var e in MyAPIGateway.Entities.GetTopMostEntitiesInSphere(ref sphere))
                entities.Add(e);

            var grid = entities.OfType<IMyCubeGrid>()
                .Where(g => g.Physics != null && access.CanUse(g))
                .OrderBy(access.Distance)
                .FirstOrDefault();
            return grid == null ? null : new { gridId = Dto.Id(grid.EntityId), gridName = Dto.GridName(grid), distance = Math.Round(access.Distance(grid), 1) };
        }

        private object CharacterStatus()
        {
            var character = access.Player.Character;
            if (character == null)
                return new { alive = false };

            Vector3D position = character.WorldMatrix.Translation;
            Vector3 gravity = MyAPIGateway.Physics.CalculateNaturalGravityAt(position, out _);
            var inventory = character.GetInventory();

            return new
            {
                alive = !character.IsDead,
                health = Math.Round((character as IMyDestroyableObject)?.Integrity ?? 0, 1),
                energy = Math.Round(character.SuitEnergyLevel * 100, 1),
                oxygen = Math.Round(character.OxygenLevel * 100, 1),
                hydrogen = Math.Round(character.GetSuitGasFillLevel(Hydrogen) * 100, 1),
                environmentOxygen = Math.Round(character.EnvironmentOxygenLevel * 100, 1),
                jetpack = character.EnabledThrusts,
                dampeners = character.EnabledDamping,
                helmet = character.EnabledHelmet,
                lights = character.EnabledLights,
                movement = character.CurrentMovementState.ToString(),
                position = Dto.Vector(position),
                speed = Dto.Speed(character),
                gravityG = Math.Round(gravity.Length() / 9.81, 2),
                inventoryFill = inventory != null ? Dto.Percent((double)inventory.CurrentVolume, (double)inventory.MaxVolume) : 0,
            };
        }
    }
}
