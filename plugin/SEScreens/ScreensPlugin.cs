using System;
using VRage.Plugins;
using VRage.Utils;

namespace SEScreens
{
    // Pulsar entry point when SEScreens is installed as its own plugin. In development SEMcp's host loads this
    // assembly instead and drives Screens itself; it checks Active first so the two never both run.
    public class ScreensPlugin : IPlugin
    {
        public static bool Active { get; private set; }

        public void Init(object gameInstance)
        {
            Active = true;
            Screens.Start(Log);
            Log("Init");
        }

        public void Update()
        {
            try
            {
                Screens.Update();
            }
            catch (Exception e)
            {
                Log($"Update failed: {e}");
            }
        }

        public void Dispose()
        {
            try
            {
                Screens.Stop();
            }
            catch (Exception e)
            {
                Log($"Dispose failed: {e}");
            }
            Active = false;
        }

        internal static void Log(string message)
        {
            MyLog.Default.WriteLine("[SEScreens] " + message);
        }
    }
}
