using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;

namespace SEMcp.Host
{
    // SEScreens (live LCD content) is its own plugin. If Pulsar already runs it, the host uses that copy and leaves
    // its lifecycle alone. Otherwise (development) the host loads the SEScreens.dll deployed next to it, from bytes
    // like the loader does for Core, and drives Screens itself; a hot reload then swaps it with the host.
    // Nothing may touch an SEScreens type before Open() has registered the resolver.
    internal static class ScreensLink
    {
        private const string AssemblyName = "SEScreens";
#if NETFRAMEWORK
        private const string HostFlavour = "net48";
#else
        private const string HostFlavour = "net9.0";
#endif

        private static Assembly screens;

        // True when this host loaded SEScreens and so must Start/Update/Stop it.
        public static bool OwnedByHost { get; private set; }
        public static bool Available => screens != null;

        public static void Open(Action<string> log)
        {
            screens = AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(IsRunningPlugin);
            if (screens == null)
            {
                string dll = Path.Combine(HostDir(), AssemblyName + ".dll");
                if (!File.Exists(dll))
                {
                    log($"SEScreens not found ({dll}); window mirroring is off");
                    return;
                }
                string pdb = Path.ChangeExtension(dll, ".pdb");
                screens = File.Exists(pdb) ? Assembly.Load(File.ReadAllBytes(dll), File.ReadAllBytes(pdb)) : Assembly.Load(File.ReadAllBytes(dll));
                OwnedByHost = true;
            }
            AppDomain.CurrentDomain.AssemblyResolve += Resolve;
            log($"SEScreens {screens.GetName().Version} {(OwnedByHost ? "loaded by the host" : "running as a plugin")}");
        }

        // Cursor mode off screens: the block menu on right-click, using things on left-click. A plugin copy of SEScreens too old to have the hook just
        // goes without it.
        public static void StartBlockMenu(Action<string> log)
        {
            if (!Available)
                return;
            try
            {
                AddBlockMenu();
            }
            catch (Exception e)
            {
                log($"Block menu unavailable: {e.Message}");
            }
        }

        // Lifecycle, only when the host owns SEScreens. Separate non-inlined methods so the host's own code never
        // compiles against SEScreens types when it isn't there.
        public static void Start(Action<string> log)
        {
            if (OwnedByHost)
                StartScreens(log);
        }

        public static void Update()
        {
            if (OwnedByHost)
                UpdateScreens();
        }

        public static void Close()
        {
            if (Available)
            {
                try
                {
                    RemoveBlockMenu();
                }
                catch (Exception)
                {
                    // Never started (old plugin copy).
                }
            }
            if (OwnedByHost)
                StopScreens();
            AppDomain.CurrentDomain.AssemblyResolve -= Resolve;
            screens = null;
            OwnedByHost = false;
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static void StartScreens(Action<string> log) => SEScreens.Screens.Start(log);

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static void UpdateScreens() => SEScreens.Screens.Update();

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static void StopScreens() => SEScreens.Screens.Stop();

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static void AddBlockMenu()
        {
            BlockMenu.Start();
            CursorUse.Start();
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static void RemoveBlockMenu()
        {
            CursorUse.Stop();
            BlockMenu.Stop();
        }

        // Pulsar's copy: its ScreensPlugin has been initialised. Copies earlier host generations loaded never are.
        private static bool IsRunningPlugin(Assembly assembly)
        {
            if (assembly.GetName().Name != AssemblyName)
                return false;
            var active = assembly.GetType("SEScreens.ScreensPlugin")?.GetProperty("Active", BindingFlags.Public | BindingFlags.Static);
            return active?.GetValue(null) is bool on && on;
        }

        private static Assembly Resolve(object sender, ResolveEventArgs args) =>
            new AssemblyName(args.Name).Name == AssemblyName ? screens : null;

        // Same rule as the loader's Plugin.Init.
        private static string HostDir()
        {
            string dir = Environment.GetEnvironmentVariable("SEMCP_HOST_DIR");
            if (!string.IsNullOrEmpty(dir))
                return dir;
            string appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            return Path.Combine(appData, "SpaceEngineers", "SEMcp", "host", HostFlavour);
        }
    }
}
