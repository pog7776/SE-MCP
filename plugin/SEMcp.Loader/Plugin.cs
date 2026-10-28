using System;
using Sandbox.Game.World;
using VRage.Plugins;
using VRage.Utils;

namespace SEMcp.Loader
{
    // M0 hello-world: proves Pulsar loads us and we can log from Init and Update.
    // M1 turns this into the hot-reload loader for SEMcp.Host.
    public class Plugin : IPlugin
    {
        private bool sessionReady;

        public void Init(object gameInstance)
        {
            Log($"Init (loader {typeof(Plugin).Assembly.GetName().Version})");
        }

        public void Update()
        {
            try
            {
                bool ready = MySession.Static?.Ready == true;
                if (ready == sessionReady)
                    return;

                sessionReady = ready;
                Log(ready ? $"Session ready: {MySession.Static.Name}" : "Session unloaded");
            }
            catch (Exception e)
            {
                Log($"Update failed: {e}");
            }
        }

        public void Dispose()
        {
            Log("Dispose");
        }

        internal static void Log(string message)
        {
            MyLog.Default.WriteLine("[SEMcp] " + message);
        }
    }
}
