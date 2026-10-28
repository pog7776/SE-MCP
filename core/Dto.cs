using System;
using System.Collections.Generic;
using System.Linq;
using Sandbox.ModAPI;
using VRage;
using VRage.Game;
using VRage.Game.ModAPI;
using VRage.ModAPI;
using VRageMath;
using IngameInventoryItem = VRage.Game.ModAPI.Ingame.MyInventoryItem;

namespace SEMcp.Core
{
    // Game objects → plain JSON-friendly objects. Ids are strings (JSON numbers lose precision past 2^53).
    public static class Dto
    {
        public static string Id(long id) => id.ToString();

        public static object Vector(Vector3D v) => new { x = Math.Round(v.X, 1), y = Math.Round(v.Y, 1), z = Math.Round(v.Z, 1) };

        public static string Relation(MyRelationsBetweenPlayerAndBlock relation)
        {
            switch (relation)
            {
                case MyRelationsBetweenPlayerAndBlock.Owner: return "owner";
                case MyRelationsBetweenPlayerAndBlock.FactionShare: return "faction";
                case MyRelationsBetweenPlayerAndBlock.NoOwnership: return "nobody";
                case MyRelationsBetweenPlayerAndBlock.Friends: return "friend";
                case MyRelationsBetweenPlayerAndBlock.Neutral: return "neutral";
                case MyRelationsBetweenPlayerAndBlock.Enemies: return "enemy";
                default: return relation.ToString().ToLowerInvariant();
            }
        }

        public static string GridName(IMyCubeGrid grid) => grid.CustomName ?? grid.DisplayName;

        public static string Size(IMyCubeGrid grid) => grid.GridSizeEnum == MyCubeSize.Large ? "large" : "small";

        public static double Speed(IMyEntity entity) => Math.Round(entity.Physics?.LinearVelocity.Length() ?? 0, 1);

        // "MyObjectBuilder_Reactor" → "Reactor"
        public static string ShortType(string typeId) =>
            typeId != null && typeId.StartsWith("MyObjectBuilder_") ? typeId.Substring("MyObjectBuilder_".Length) : typeId;

        public static double Percent(double value, double max) => max > 0 ? Math.Round(100 * value / max, 1) : 0;

        public static double Integrity(IMySlimBlock slim) => Percent(slim.Integrity, slim.MaxIntegrity);

        public static double Liters(MyFixedPoint volume) => Math.Round((double)volume * 1000, 1);

        public static double Kg(MyFixedPoint mass) => Math.Round((double)mass, 1);

        public static object BlockSummary(IMyTerminalBlock block, Access access)
        {
            var slim = block.SlimBlock;
            return new
            {
                id = Id(block.EntityId),
                name = block.CustomName,
                type = ShortType(block.BlockDefinition.TypeIdString),
                subtype = block.BlockDefinition.SubtypeName,
                displayType = block.DefinitionDisplayNameText,
                gridId = Id(block.CubeGrid.EntityId),
                enabled = (block as IMyFunctionalBlock)?.Enabled,
                functional = block.IsFunctional,
                working = block.IsWorking,
                integrity = slim != null ? Integrity(slim) : 100,
                accessible = access.CanUse(block),
            };
        }

        public static Dictionary<string, int> MissingComponents(IMySlimBlock slim)
        {
            var missing = new Dictionary<string, int>();
            slim.GetMissingComponents(missing);
            return missing;
        }

        public static List<object> Items(IMyInventory inventory)
        {
            var items = new List<IngameInventoryItem>();
            inventory.GetItems(items);
            return items.Select(item => (object)new
            {
                type = ShortType(item.Type.TypeId),
                subtype = item.Type.SubtypeId,
                amount = Math.Round((double)item.Amount, 2),
            }).ToList();
        }

        public static object Inventory(IMyInventory inventory, int index) => new
        {
            index,
            volume = Liters(inventory.CurrentVolume),
            maxVolume = Liters(inventory.MaxVolume),
            fill = Percent((double)inventory.CurrentVolume, (double)inventory.MaxVolume),
            mass = Kg(inventory.CurrentMass),
            items = Items(inventory),
        };

        public static IEnumerable<IMyInventory> Inventories(IMyEntity entity)
        {
            if (entity == null || !entity.HasInventory)
                yield break;
            for (int i = 0; i < entity.InventoryCount; i++)
                yield return entity.GetInventory(i);
        }
    }
}
