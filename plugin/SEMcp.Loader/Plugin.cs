using System;
using System.IO;
using VRage.Plugins;
using VRage.Utils;

namespace SEMcp.Loader
{
    // Pulsar entry point. Everything interesting lives in the hot-reloadable SEMcp.Host.
    public class Plugin : IPlugin
    {
#if NETFRAMEWORK
        private const string HostFlavour = "net48";
#else
        private const string HostFlavour = "net9.0";
#endif

        private HostLoader loader;

        public void Init(object gameInstance)
        {
            try
            {
                string hostDir = Environment.GetEnvironmentVariable("SEMCP_HOST_DIR");
                if (string.IsNullOrEmpty(hostDir))
                {
                    string appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
                    hostDir = Path.Combine(appData, "SpaceEngineers", "SEMcp", "host", HostFlavour);
                }

                loader = new HostLoader(hostDir);
                Log($"Init (loader {loader.LoaderVersion}, {HostFlavour}), host dir {hostDir}");
            }
            catch (Exception e)
            {
                Log($"Init failed: {e}");
            }
        }

        public void Update()
        {
            try
            {
                loader?.Update();
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
                loader?.Dispose();
                Log("Dispose");
            }
            catch (Exception e)
            {
                Log($"Dispose failed: {e}");
            }
        }

        internal static void Log(string message)
        {
            MyLog.Default.WriteLine("[SEMcp] " + message);
        }
    }
}
