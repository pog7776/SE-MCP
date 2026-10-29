using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.Definitions;
using Sandbox.ModAPI;
using SpaceEngineers.Game.ModAPI;
using VRage.Game;
using VRage.Game.ModAPI;
using VRage.ModAPI;
using VRageMath;

namespace SEMcp.Core.Services
{
    // Turrets, fixed guns and custom turret controllers: what they're aimed at, their ammo, and changing targets.
    // Firing is gated separately (allowWeapons in config); aiming and target settings are not.
    internal sealed class WeaponService
    {
        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public WeaponService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("weapons.get", Get);
            dispatcher.Register("turret.set", Set);
            dispatcher.Register("turret.aim", Aim);
            dispatcher.Register("weapon.fire", Fire);
            dispatcher.Register("weapon.magazine", Magazine);
            dispatcher.Register("camera.track", Track);
            dispatcher.Register("damage.get", Damage);
        }

        private void Log(string message) => dispatcher.Context.Log(message);

        private static double[] Vec(Vector3D v, int digits = 2) =>
            new[] { Math.Round(v.X, digits), Math.Round(v.Y, digits), Math.Round(v.Z, digits) };

        private static Vector3D? Point(JsonObject p, string name) =>
            p[name] is JsonArray a && a.Count == 3
                ? new Vector3D(a[0].GetValue<double>(), a[1].GetValue<double>(), a[2].GetValue<double>())
                : (Vector3D?)null;

        private IEnumerable<IMyTerminalBlock> Weapons(IMyCubeGrid grid) =>
            Access.Construct(grid).SelectMany(g => g.GetFatBlocks<IMyTerminalBlock>())
                .Where(b => b is IMyUserControllableGun || b is IMyTurretControlBlock).Where(access.CanUse)
                .OrderBy(b => b.CustomName, StringComparer.OrdinalIgnoreCase);

        // One block (blockId) or several (blockIds), each checked for access.
        private List<IMyTerminalBlock> Blocks(JsonObject p)
        {
            if (p["blockIds"] is JsonArray ids)
                return ids.Select(id => access.Block(long.Parse(id.ToString()))).ToList();
            return new List<IMyTerminalBlock> { access.Block(p.Id("blockId")) };
        }

        private static string Kind(IMyTerminalBlock b) =>
            b is IMyLargeTurretBase ? "turret" : b is IMyTurretControlBlock ? "turret controller" : "fixed";

        // Magazines this weapon accepts, from its definition (several only for interior turrets and flare launchers).
        internal static List<MyAmmoMagazineDefinition> AcceptedMagazines(IMyTerminalBlock block)
        {
            if (!(MyDefinitionManager.Static.GetCubeBlockDefinition(block.BlockDefinition) is MyWeaponBlockDefinition def))
                return new List<MyAmmoMagazineDefinition>();
            if (!MyDefinitionManager.Static.TryGetWeaponDefinition(def.WeaponDefinitionId, out var weapon))
                return new List<MyAmmoMagazineDefinition>();
            return weapon.AmmoMagazinesId.Select(id => MyDefinitionManager.Static.GetAmmoMagazineDefinition(id)).Where(m => m != null).ToList();
        }

        private static Dictionary<string, object> Targets(IMyLargeTurretBase t) => new Dictionary<string, object>
        {
            ["enemies"] = t.TargetEnemies, ["neutrals"] = t.TargetNeutrals, ["characters"] = t.TargetCharacters,
            ["largeGrids"] = t.TargetLargeGrids, ["smallGrids"] = t.TargetSmallGrids, ["stations"] = t.TargetStations,
            ["missiles"] = t.TargetMissiles, ["meteors"] = t.TargetMeteors,
        };

        private static Dictionary<string, object> Targets(IMyTurretControlBlock t) => new Dictionary<string, object>
        {
            ["friends"] = t.TargetFriends, ["neutrals"] = t.TargetNeutrals, ["characters"] = t.TargetCharacters,
            ["largeGrids"] = t.TargetLargeGrids, ["smallGrids"] = t.TargetSmallGrids, ["stations"] = t.TargetStations,
            ["missiles"] = t.TargetMissiles, ["meteors"] = t.TargetMeteors,
        };

        private object Get(JsonObject p)
        {
            var grid = access.Grid(p.Id("gridId"));
            var blocks = Weapons(grid).ToList();

            // Ammo the ship carries, per magazine type: every inventory on the construct (turrets pull from the conveyors).
            var carried = new Dictionary<string, double>();
            foreach (var g in Access.Construct(grid))
                foreach (var b in g.GetFatBlocks<IMyTerminalBlock>())
                    foreach (var inv in Dto.Inventories(b))
                        foreach (var item in Dto.ItemList(inv))
                            if (item.Type.TypeId == "MyObjectBuilder_AmmoMagazine")
                                carried[item.Type.SubtypeId] = carried.TryGetValue(item.Type.SubtypeId, out var n) ? n + (double)item.Amount : (double)item.Amount;

            var magazineNames = new Dictionary<string, object>();
            var list = blocks.Select(b =>
            {
                var accepted = AcceptedMagazines(b);
                foreach (var m in accepted)
                    magazineNames[m.Id.SubtypeName] = new { name = m.DisplayNameText, rounds = m.Capacity, carried = carried.TryGetValue(m.Id.SubtypeName, out var c) ? c : 0 };
                var entry = new Dictionary<string, object>
                {
                    ["id"] = Dto.Id(b.EntityId),
                    ["name"] = b.CustomName,
                    ["type"] = b.BlockDefinition.SubtypeName,
                    ["kind"] = Kind(b),
                    ["gridName"] = Dto.GridName(b.CubeGrid),
                    ["enabled"] = (b as IMyFunctionalBlock)?.Enabled,
                    ["working"] = b.IsWorking,
                    ["integrity"] = Dto.Integrity(b.SlimBlock),
                };
                if (b is IMyUserControllableGun gun)
                {
                    entry["shooting"] = gun.IsShooting;
                    entry["magazines"] = accepted.Select(m => m.Id.SubtypeName).ToList();
                    entry["loaded"] = dispatcher.Context.CurrentMagazine(b);
                    entry["roundsLoaded"] = dispatcher.Context.LoadedRounds(b);
                    entry["magazinesInside"] = Dto.Inventories(b).SelectMany(Dto.ItemList)
                        .Where(i => i.Type.TypeId == "MyObjectBuilder_AmmoMagazine").Sum(i => (double)i.Amount);
                }
                if (b is IMyLargeTurretBase t)
                {
                    var groups = new List<string>();
                    t.GetTargetingGroups(groups);
                    entry["ai"] = t.AIEnabled;
                    entry["underControl"] = t.IsUnderControl;
                    entry["hasTarget"] = t.HasTarget;
                    entry["aimed"] = t.IsAimed;
                    entry["target"] = t.HasTarget ? SensingService.Detected(t.GetTargetedEntity(), t.GetPosition()) : null;
                    entry["range"] = Math.Round(t.Range, 0);
                    entry["idleRotation"] = t.EnableIdleRotation;
                    entry["targets"] = Targets(t);
                    entry["targetingGroup"] = t.GetTargetingGroup();
                    entry["targetingGroups"] = groups;
                    entry["azimuthDeg"] = Math.Round(MathHelper.ToDegrees(t.Azimuth), 1);
                    entry["elevationDeg"] = Math.Round(MathHelper.ToDegrees(t.Elevation), 1);
                    // Where the barrel points now, in world space: azimuth/elevation are relative to the turret's base.
                    Vector3D local;
                    Vector3D.CreateFromAzimuthAndElevation(t.Azimuth, t.Elevation, out local);
                    entry["aimDirection"] = Vec(Vector3D.TransformNormal(local, t.WorldMatrix), 3);
                }
                if (b is IMyUserControllableGun)
                {
                    entry["position"] = Vec(b.GetPosition(), 1);
                    // The way the block is mounted (a turret's base up; a fixed gun fires along forward).
                    entry["forward"] = Vec(b.WorldMatrix.Forward, 3);
                    entry["up"] = Vec(b.WorldMatrix.Up, 3);
                }
                if (b is IMyTurretControlBlock tc)
                {
                    var groups = new List<string>();
                    tc.GetTargetingGroups(groups);
                    var tools = new List<Sandbox.ModAPI.Ingame.IMyFunctionalBlock>();
                    tc.GetTools(tools);
                    entry["ai"] = tc.AIEnabled;
                    entry["underControl"] = tc.IsUnderControl;
                    entry["hasTarget"] = tc.HasTarget;
                    entry["aimed"] = tc.IsAimed;
                    entry["target"] = tc.HasTarget ? SensingService.Detected(tc.GetTargetedEntity(), tc.GetPosition()) : null;
                    entry["range"] = Math.Round(tc.Range, 0);
                    entry["targets"] = Targets(tc);
                    entry["targetingGroup"] = tc.GetTargetingGroup();
                    entry["targetingGroups"] = groups;
                    entry["tools"] = tools.Select(x => x.CustomName).ToList();
                    entry["camera"] = tc.Camera?.CustomName;
                    entry["azimuthRotor"] = tc.AzimuthRotor?.CustomName;
                    entry["elevationRotor"] = tc.ElevationRotor?.CustomName;
                }
                return entry;
            }).ToList();

            return new { gridName = Dto.GridName(grid), weapons = list, ammo = magazineNames };
        }

        private object Set(JsonObject p)
        {
            dispatcher.RequireWritable();
            var changed = new List<string>();
            foreach (var b in Blocks(p))
            {
                if (b is IMyLargeTurretBase t)
                {
                    if (p.Bool("reset", false))
                    {
                        // Resetting also switches idle rotation on; keep the player's setting.
                        bool idle = t.EnableIdleRotation;
                        t.ResetTargetingToDefault();
                        t.EnableIdleRotation = idle;
                        t.SyncEnableIdleRotation();
                    }
                    if (p["range"] != null) t.Range = (float)p.Number("range", t.Range);
                    if (p["idleRotation"] != null) { t.EnableIdleRotation = p.Bool("idleRotation", true); t.SyncEnableIdleRotation(); }
                    if (p.String("targetingGroup") is string group) t.SetTargetingGroup(group);
                    if (p["targets"] is JsonObject flags)
                    {
                        bool F(string key, bool now) => flags[key] != null ? flags[key].GetValue<bool>() : now;
                        t.TargetEnemies = F("enemies", t.TargetEnemies);
                        t.TargetNeutrals = F("neutrals", t.TargetNeutrals);
                        t.TargetCharacters = F("characters", t.TargetCharacters);
                        t.TargetLargeGrids = F("largeGrids", t.TargetLargeGrids);
                        t.TargetSmallGrids = F("smallGrids", t.TargetSmallGrids);
                        t.TargetStations = F("stations", t.TargetStations);
                        t.TargetMissiles = F("missiles", t.TargetMissiles);
                        t.TargetMeteors = F("meteors", t.TargetMeteors);
                    }
                }
                else if (b is IMyTurretControlBlock tc)
                {
                    if (p["range"] != null) tc.Range = (float)p.Number("range", tc.Range);
                    if (p.String("targetingGroup") is string group) tc.SetTargetingGroup(group);
                    if (p["targets"] is JsonObject flags)
                    {
                        bool F(string key, bool now) => flags[key] != null ? flags[key].GetValue<bool>() : now;
                        tc.TargetFriends = F("friends", tc.TargetFriends);
                        tc.TargetNeutrals = F("neutrals", tc.TargetNeutrals);
                        tc.TargetCharacters = F("characters", tc.TargetCharacters);
                        tc.TargetLargeGrids = F("largeGrids", tc.TargetLargeGrids);
                        tc.TargetSmallGrids = F("smallGrids", tc.TargetSmallGrids);
                        tc.TargetStations = F("stations", tc.TargetStations);
                        tc.TargetMissiles = F("missiles", tc.TargetMissiles);
                        tc.TargetMeteors = F("meteors", tc.TargetMeteors);
                    }
                }
                else
                    throw new RpcException(ErrorKind.InvalidParams, $"'{b.CustomName}' is not a turret or turret controller");
                changed.Add(b.CustomName);
                Log($"turret.set '{b.CustomName}'");
            }
            return new { changed };
        }

        // Point turrets: at a position (optionally moving, so the turret leads it), at an entity, or at fixed angles.
        private object Aim(JsonObject p)
        {
            dispatcher.RequireWritable();
            var position = Point(p, "position");
            var velocity = Point(p, "velocity") ?? Vector3D.Zero;
            IMyEntity entity = p["entityId"] != null ? MyAPIGateway.Entities.GetEntityById(p.Id("entityId")) : null;
            if (p["entityId"] != null && entity == null)
                throw new RpcException(ErrorKind.NotFound, $"No entity with id {p.Id("entityId")}");

            var aimed = new List<string>();
            foreach (var b in Blocks(p))
            {
                if (b is IMyLargeTurretBase t)
                {
                    if (entity != null) t.TrackTarget(entity);
                    else if (position.HasValue)
                    {
                        if (velocity.LengthSquared() > 0) t.TrackTarget(position.Value, velocity);
                        else t.SetTarget(position.Value);
                    }
                    else if (p["azimuth"] != null || p["elevation"] != null)
                    {
                        t.SetManualAzimuthAndElevation(
                            MathHelper.ToRadians((float)p.Number("azimuth", MathHelper.ToDegrees(t.Azimuth))),
                            MathHelper.ToRadians((float)p.Number("elevation", MathHelper.ToDegrees(t.Elevation))));
                        t.SyncAzimuth();
                        t.SyncElevation();
                    }
                    else
                        throw new RpcException(ErrorKind.InvalidParams, "Give position (+velocity), entityId, or azimuth/elevation");
                }
                else if (b is IMyTurretControlBlock tc)
                {
                    if (entity == null)
                        throw new RpcException(ErrorKind.InvalidParams, $"'{b.CustomName}' (turret controller) can only track an entity (entityId)");
                    tc.TrackTarget(entity);
                }
                else
                    throw new RpcException(ErrorKind.InvalidParams, $"'{b.CustomName}' is a fixed weapon; aim it by turning the ship");
                aimed.Add(b.CustomName);
                Log($"turret.aim '{b.CustomName}'");
            }
            return new { aimed };
        }

        // Pull the trigger through terminal actions, as a timer or PB would, so it syncs in multiplayer.
        private object Fire(JsonObject p)
        {
            dispatcher.RequireWritable();
            if (!dispatcher.Context.AllowWeapons)
                throw new RpcException(ErrorKind.FeatureDisabled, "Firing weapons is turned off (allowWeapons in config.json)");
            string mode = p.String("mode") ?? "once";
            string action = mode == "once" ? "ShootOnce" : mode == "on" ? "Shoot_On" : mode == "off" ? "Shoot_Off"
                : throw new RpcException(ErrorKind.InvalidParams, "mode must be once, on or off");

            var fired = new List<string>();
            foreach (var b in Blocks(p))
            {
                if (!(b is IMyUserControllableGun))
                    throw new RpcException(ErrorKind.InvalidParams, $"'{b.CustomName}' is not a weapon");
                var a = b.GetActionWithName(action)
                    ?? throw new RpcException(ErrorKind.InvalidParams, $"'{b.CustomName}' has no '{action}' action");
                a.Apply(b);
                fired.Add(b.CustomName);
                Log($"weapon.fire '{b.CustomName}' {mode}");
            }
            return new { fired, mode };
        }

        private object Magazine(JsonObject p)
        {
            dispatcher.RequireWritable();
            var block = access.Block(p.Id("blockId"));
            string wanted = p.String("magazine", required: true);
            var accepted = AcceptedMagazines(block);
            var match = accepted.FirstOrDefault(m => string.Equals(m.Id.SubtypeName, wanted, StringComparison.OrdinalIgnoreCase))
                ?? accepted.FirstOrDefault(m => string.Equals(m.DisplayNameText, wanted, StringComparison.OrdinalIgnoreCase))
                ?? throw new RpcException(ErrorKind.InvalidParams,
                    $"'{block.CustomName}' doesn't take '{wanted}'. It takes: {string.Join(", ", accepted.Select(m => $"{m.DisplayNameText} ({m.Id.SubtypeName})"))}");
            string before = dispatcher.Context.CurrentMagazine(block);
            bool ok = dispatcher.Context.SwitchMagazine(block, match.Id);
            Log($"weapon.magazine '{block.CustomName}' -> {match.Id.SubtypeName}: {ok}");
            return new { block = block.CustomName, before, after = dispatcher.Context.CurrentMagazine(block), switched = ok, magazine = match.Id.SubtypeName };
        }

        // One tracking step: raycast where the target should be now with whichever camera can reach it.
        // The caller predicts the point (last position + velocity x time) and repeats this to keep a track.
        private object Track(JsonObject p)
        {
            dispatcher.RequireWritable();
            var grid = access.Grid(p.Id("gridId"));
            var point = Point(p, "position") ?? throw new RpcException(ErrorKind.InvalidParams, "position is required");
            long expected = p["entityId"] != null ? p.Id("entityId") : 0;

            var cameras = Access.Construct(grid).SelectMany(g => g.GetFatBlocks<IMyCameraBlock>())
                .Where(c => access.CanUse(c) && c.IsWorking).ToList();
            if (cameras.Count == 0)
                throw new RpcException(ErrorKind.NotFound, $"'{Dto.GridName(grid)}' has no working cameras");

            var tried = new List<object>();
            // Cameras that can see the point, the most charged first. Aim a little past it, so a target that moved
            // slightly further away is still hit.
            foreach (var c in cameras.OrderByDescending(c => c.AvailableScanRange))
            {
                if (!c.EnableRaycast)
                    c.EnableRaycast = true;
                var from = c.GetPosition();
                var dir = Vector3D.Normalize(point - from);
                var aim = point + dir * Math.Max(25, p.Number("overshoot", 50));
                if (!c.CanScan(aim))
                {
                    int wait = c.TimeUntilScan(Vector3D.Distance(from, aim));
                    tried.Add(new { camera = c.CustomName, reason = wait > 0 ? "charging" : "outside its view cone", msUntilScan = wait });
                    continue;
                }
                var info = c.Raycast(aim);
                var hit = SensingService.Detected(info, from);
                return new
                {
                    camera = c.CustomName,
                    hit,
                    lost = info.IsEmpty() || (expected != 0 && info.EntityId != expected),
                    scanRangeLeft = Math.Round(c.AvailableScanRange, 0),
                };
            }
            return new { camera = (string)null, hit = (object)null, lost = (bool?)null, notReady = tried };
        }

        // Damaged or unfinished blocks on the ship, for a damage report.
        private object Damage(JsonObject p)
        {
            var grid = access.Grid(p.Id("gridId"));
            var damaged = new List<object>();
            int total = 0;
            foreach (var g in Access.Construct(grid))
            {
                var slims = new List<IMySlimBlock>();
                g.GetBlocks(slims);
                total += slims.Count;
                foreach (var s in slims)
                {
                    bool hurt = s.CurrentDamage > 0 || s.BuildLevelRatio < 1 || s.HasDeformation;
                    if (!hurt)
                        continue;
                    var fat = s.FatBlock as IMyTerminalBlock;
                    var def = MyDefinitionManager.Static.GetCubeBlockDefinition(s.BlockDefinition.Id);
                    damaged.Add(new
                    {
                        id = fat != null ? Dto.Id(fat.EntityId) : null,
                        name = fat?.CustomName ?? def?.DisplayNameText ?? s.BlockDefinition.Id.SubtypeName,
                        type = Dto.ShortType(s.BlockDefinition.Id.TypeId.ToString()),
                        gridName = Dto.GridName(g),
                        position = Vec(g.GridIntegerToWorld(s.Position), 1),
                        integrity = Dto.Integrity(s),
                        damage = Math.Round(s.CurrentDamage, 0),
                        functional = s.FatBlock?.IsFunctional,
                        deformed = s.HasDeformation,
                        missing = Dto.MissingComponents(s),
                    });
                }
            }
            return new { gridName = Dto.GridName(grid), blocks = total, damaged };
        }
    }
}
