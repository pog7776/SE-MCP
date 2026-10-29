using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.Definitions;
using Sandbox.ModAPI;
using VRage.Game;
using VRage.Game.ModAPI;
using IngameProductionItem = Sandbox.ModAPI.Ingame.MyProductionItem;

namespace SEMcp.Core.Services
{
    internal sealed class ProductionService
    {
        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public ProductionService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("production.get", Get);
            dispatcher.Register("recipes.list", Recipes);
            dispatcher.Register("production.queue", Queue);
            dispatcher.Register("production.remove", Remove);
            dispatcher.Register("production.clear", Clear);
        }

        // Assemblers, refineries and other production blocks on a ship: what they're doing and what's queued.
        private object Get(JsonObject p)
        {
            var grid = access.Grid(p.Id("gridId"));
            var grids = p.Bool("includeSubgrids", true) ? Access.Construct(grid) : new List<IMyCubeGrid> { grid };
            var blocks = grids.SelectMany(g => g.GetFatBlocks<IMyProductionBlock>())
                .Where(access.CanUse)
                .OrderBy(b => b.CustomName, StringComparer.OrdinalIgnoreCase);

            return new
            {
                blocks = blocks.Select(block =>
                {
                    var queue = new List<IngameProductionItem>();
                    block.GetQueue(queue);
                    var assembler = block as IMyAssembler;
                    return new
                    {
                        id = Dto.Id(block.EntityId),
                        name = block.CustomName,
                        type = Dto.ShortType(block.BlockDefinition.TypeIdString),
                        displayType = block.DefinitionDisplayNameText,
                        enabled = block.Enabled,
                        working = block.IsWorking,
                        producing = block.IsProducing,
                        mode = assembler?.Mode.ToString(),
                        repeating = assembler?.Repeating,
                        cooperative = assembler?.CooperativeMode,
                        queue = queue.Select((item, index) => new
                        {
                            index,
                            blueprint = item.BlueprintId.SubtypeName,
                            blueprintId = BlueprintKey(item.BlueprintId),
                            amount = Math.Round((double)item.Amount, 2),
                        }).ToList(),
                        input = Fill(block.InputInventory),
                        output = Fill(block.OutputInventory),
                    };
                }).ToList(),
            };
        }

        // "BlueprintDefinition/SteelPlate": short and unambiguous, and what production.queue takes back.
        private static string BlueprintKey(MyDefinitionId id) => $"{Dto.ShortType(id.TypeId.ToString())}/{id.SubtypeName}";

        private static object Items(MyBlueprintDefinitionBase.Item[] items) => (items ?? new MyBlueprintDefinitionBase.Item[0])
            .Select(i => new { type = Dto.ShortType(i.Id.TypeId.ToString()), subtype = i.Id.SubtypeName, amount = Math.Round((double)i.Amount, 4) })
            .ToList();

        // Blueprints some production block can actually make, with the kinds of block that make them. Block
        // blueprints for the build planner and other non-craftable ones are left out.
        private static Dictionary<MyBlueprintDefinitionBase, HashSet<string>> MadeIn()
        {
            var made = new Dictionary<MyBlueprintDefinitionBase, HashSet<string>>();
            foreach (var producer in MyDefinitionManager.Static.GetAllDefinitions().OfType<MyProductionBlockDefinition>())
            {
                if (!producer.Public || producer.BlueprintClasses == null)
                    continue;
                // Gas tanks, O2/H2 generators and irrigation use blueprints for bottle refills and ice-to-gas, with a
                // placeholder "gravel" result. Not crafting.
                string kind = producer.GetType().Name;
                if (kind.Contains("GasTank") || kind.Contains("OxygenGenerator") || kind.Contains("Irrigation"))
                    continue;
                string name = producer.DisplayNameText ?? producer.Id.SubtypeName;
                foreach (var blueprintClass in producer.BlueprintClasses)
                {
                    foreach (var blueprint in blueprintClass)
                    {
                        if (!made.TryGetValue(blueprint, out var names))
                            made[blueprint] = names = new HashSet<string>();
                        names.Add(name);
                    }
                }
            }
            return made;
        }

        // Every public blueprint (recipe) in the game, mods included, with the world's production multipliers.
        // With blockId: only what that block can make.
        private object Recipes(JsonObject p)
        {
            dispatcher.RequireSession();
            IMyProductionBlock block = null;
            if (p["blockId"] != null)
                block = access.Block(p.Id("blockId")) as IMyProductionBlock
                    ?? throw new RpcException(ErrorKind.InvalidParams, "Not a production block");
            var settings = MyAPIGateway.Session.SessionSettings;
            return new
            {
                assemblerSpeed = settings?.AssemblerSpeedMultiplier,
                // Assemblers use ingredients / this; refineries yield more by their own efficiency (upgrades).
                assemblerEfficiency = settings?.AssemblerEfficiencyMultiplier,
                refinerySpeed = settings?.RefinerySpeedMultiplier,
                // Ore-to-ingot yield factor of each refinery type (before yield modules): output = recipe output x this.
                refineryEfficiency = MyDefinitionManager.Static.GetAllDefinitions().OfType<MyRefineryDefinition>()
                    .Where(d => d.Public)
                    .GroupBy(d => (d.DisplayNameText ?? d.Id.SubtypeName).Split('\n')[0].Trim())
                    .ToDictionary(g => g.Key, g => Math.Round(g.Max(d => d.MaterialEfficiency), 3)),
                recipes = MadeIn()
                    .Where(x => x.Key.Results != null && x.Key.Results.Length > 0 && x.Key.Prerequisites != null && x.Key.Prerequisites.Length > 0)
                    // Real items only, and not the build planner's per-block component kits ("RadioAntenna/LargeBlockRadioAntenna").
                    .Where(x => x.Key.Results.All(r => MyDefinitionManager.Static.TryGetPhysicalItemDefinition(r.Id, out _)))
                    .Where(x => !x.Key.Id.SubtypeName.Contains("/"))
                    .Where(x => block == null || block.CanUseBlueprint(x.Key))
                    .Select(x => (b: x.Key, madeIn: x.Value))
                    .Select(x => new
                    {
                        id = BlueprintKey(x.b.Id),
                        // Display names carry the tooltip after the first line.
                        name = (x.b.DisplayNameText ?? x.b.Id.SubtypeName).Split('\n')[0].Trim(),
                        madeIn = x.madeIn.OrderBy(n => n).ToList(),
                        inputs = Items(x.b.Prerequisites),
                        outputs = Items(x.b.Results),
                        seconds = Math.Round(x.b.BaseProductionTimeInSeconds, 3),
                        ignoresEfficiency = x.b.IgnoreEfficiencyMultiplier,
                    })
                    .ToList(),
            };
        }

        private IMyProductionBlock Producer(JsonObject p)
        {
            var block = access.Block(p.Id("blockId"));
            return block as IMyProductionBlock
                ?? throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' is not a production block");
        }

        private static MyBlueprintDefinitionBase Blueprint(string key)
        {
            if (string.IsNullOrWhiteSpace(key) || !key.Contains("/"))
                throw new RpcException(ErrorKind.InvalidParams, "blueprint must look like 'BlueprintDefinition/SteelPlate' (see recipes.list)");
            var parts = key.Split(new[] { '/' }, 2);
            string type = parts[0].StartsWith("MyObjectBuilder_") ? parts[0] : "MyObjectBuilder_" + parts[0];
            if (!MyDefinitionId.TryParse(type, parts[1], out var id))
                throw new RpcException(ErrorKind.InvalidParams, $"Unknown blueprint type '{parts[0]}'");
            return MyDefinitionManager.Static.GetBlueprintDefinition(id)
                ?? throw new RpcException(ErrorKind.NotFound, $"No blueprint '{key}'");
        }

        // Adds to the queue (end, or index) like clicking a blueprint in the production screen.
        private object Queue(JsonObject p)
        {
            dispatcher.RequireWritable();
            var block = Producer(p);
            var blueprint = Blueprint(p.String("blueprint", required: true));
            double amount = p.Number("amount", 1);
            if (amount <= 0)
                throw new RpcException(ErrorKind.InvalidParams, "amount must be positive");
            if (!block.CanUseBlueprint(blueprint))
                throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' can't make {blueprint.DisplayNameText}");
            var fixedAmount = (VRage.MyFixedPoint)amount;
            if (p["index"] != null)
                block.InsertQueueItem((int)p.Number("index", 0), blueprint, fixedAmount);
            else
                block.AddQueueItem(blueprint, fixedAmount);
            dispatcher.Context.Log($"queued {amount} {blueprint.DisplayNameText} in '{block.CustomName}'");
            return new { block = block.CustomName, queued = blueprint.DisplayNameText, amount };
        }

        private object Remove(JsonObject p)
        {
            dispatcher.RequireWritable();
            var block = Producer(p);
            var queue = new List<IngameProductionItem>();
            block.GetQueue(queue);
            int index = (int)p.Number("index", -1);
            if (index < 0 || index >= queue.Count)
                throw new RpcException(ErrorKind.InvalidParams, $"No queue entry {index} (queue has {queue.Count})");
            var item = queue[index];
            var amount = p["amount"] != null ? (VRage.MyFixedPoint)p.Number("amount", 0) : item.Amount;
            block.RemoveQueueItem(index, amount);
            dispatcher.Context.Log($"removed {amount} {item.BlueprintId.SubtypeName} from '{block.CustomName}' queue");
            return new { block = block.CustomName, removed = item.BlueprintId.SubtypeName, amount = (double)amount };
        }

        private object Clear(JsonObject p)
        {
            dispatcher.RequireWritable();
            var block = Producer(p);
            block.ClearQueue();
            dispatcher.Context.Log($"cleared queue of '{block.CustomName}'");
            return new { block = block.CustomName, cleared = true };
        }

        private static object Fill(VRage.Game.ModAPI.Ingame.IMyInventory inventory) => inventory == null ? null : new
        {
            fill = Dto.Percent((double)inventory.CurrentVolume, (double)inventory.MaxVolume),
            items = inventory.ItemCount,
        };
    }
}
