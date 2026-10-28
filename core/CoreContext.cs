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
    }
}
