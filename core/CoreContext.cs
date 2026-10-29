using System;

namespace SEMcp.Core
{
    // What the host tells Core about the environment. Core can't learn these through ModAPI alone
    // (MyAPIGateway.Session is never cleared after a world unloads, so it can't say "no world").
    public sealed class CoreContext
    {
        public string HostVersion { get; set; }
        public int Generation { get; set; }
        public string GameVersion { get; set; }
        public string Runtime { get; set; }
        public Func<bool> IsSessionReady { get; set; } = () => false;
        public double MaxRangeMeters { get; set; } = 50000;
        public bool ReadOnly { get; set; }
        public bool AllowPbCodePush { get; set; } = true;
        public bool AllowAutopilot { get; set; } = true;
        // Compiler messages of a programmable block. The game keeps them private (only its editor shows them), so the host supplies them.
        public Func<Sandbox.ModAPI.IMyProgrammableBlock, System.Collections.Generic.IList<string>> PbCompilerErrors { get; set; } = _ => null;
        // Conveyor pathfinding between two blocks for one item type (size-aware), as the inventory screen does it.
        // Works for blocks without an inventory too (conveyor junctions). Null when the host can't tell.
        public Func<VRage.ModAPI.IMyEntity, VRage.ModAPI.IMyEntity, VRage.Game.ModAPI.Ingame.MyItemType, bool?> CanConvey { get; set; } = (_, __, ___) => null;
        // Move one item stack the way the inventory screen does: a player request the server carries out, so it
        // also works as a multiplayer client. Null = not available (Core falls back to the server-only ModAPI move).
        public Action<VRage.Game.ModAPI.IMyInventory, VRage.Game.ModAPI.IMyInventory, uint, VRage.MyFixedPoint> TransferByUser { get; set; }
        // Every write is logged here (the SE log), so the player can see what the assistant changed.
        public Action<string> Log { get; set; } = _ => { };
    }
}
