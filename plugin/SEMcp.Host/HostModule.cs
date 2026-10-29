using System;
using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using Sandbox.Game.World;
using SEMcp.Core;
using SEMcp.Loader;
using VRage.Game;

namespace SEMcp.Host
{
    // Created by the loader on every (re)load. Owns the config, the TCP server and the game-thread queue.
    // Rule that keeps hot reload safe: never subscribe to game events or register anything with the game;
    // everything reaches the game through the queue drained in Update().
    public sealed class HostModule : IHostModule
    {
        private const double FrameBudgetMs = 2;

        private ILoaderContext loader;
        private Config config;
        private GameThreadQueue queue;
        private Dispatcher dispatcher;
        private TcpServer server;
        private CameraCapture capture;

        public void Start(ILoaderContext context)
        {
            loader = context;
            config = Config.LoadOrCreate();
            queue = new GameThreadQueue();
            dispatcher = new Dispatcher(new CoreContext
            {
                HostVersion = typeof(HostModule).Assembly.GetName().Version.ToString(),
                Generation = context.Generation,
                GameVersion = MyFinalBuildConstants.APP_VERSION_STRING_DOTS.ToString(),
                Runtime = RuntimeInformation.FrameworkDescription,
                IsSessionReady = () => MySession.Static?.Ready == true,
                MaxRangeMeters = config.MaxRangeMeters,
                ReadOnly = config.ReadOnly,
                AllowPbCodePush = config.AllowPbCodePush,
                AllowAutopilot = config.AllowAutopilot,
                AllowWeapons = config.AllowWeapons,
                CurrentMagazine = HostMethods.CurrentMagazine,
                LoadedRounds = HostMethods.LoadedRounds,
                VoxelHit = HostMethods.VoxelHit,
                SwitchMagazine = HostMethods.SwitchMagazine,
                PbCompilerErrors = HostMethods.PbCompilerErrors,
                CanConvey = HostMethods.CanConvey,
                TransferByUser = HostMethods.TransferByUser,
                Log = message => context.Log(message),
            });

            HostMethods.Register(dispatcher);
            ToolbarMethods.Register(dispatcher);
            OverlayMethods.Register(dispatcher);
            StartCapture();

            server = new TcpServer(config.Port, config.Token, Handle, Log);
            server.Start();
        }

        public void Update()
        {
            queue.Drain(FrameBudgetMs);
        }

        public void Stop()
        {
            server?.Stop();
            HostMethods.ClearAllHighlights();
            OverlayMethods.Close();
            capture?.Dispose();
            capture = null;
            AppDomain.CurrentDomain.AssemblyResolve -= ResolveLoaded;
            queue?.Close();
            Log("Host stopped");
        }

        // Background thread. Host-level methods are answered here; everything else runs on the game thread.
        private Task<JsonNode> Handle(string method, JsonObject parameters)
        {
            switch (method)
            {
                case "debug.reload":
                    // Respond first; the reload closes this connection on the next frame.
                    Task.Delay(200).ContinueWith(_ => loader.RequestReload());
                    return Task.FromResult<JsonNode>(new JsonObject { ["reloading"] = true });
                case "camera.capture":
                    return CaptureMethod.Run(parameters, dispatcher, queue, capture, config.AllowScreenshots);
            }

            return queue.Run(() => Rpc.ToNode(dispatcher.Dispatch(method, parameters)));
        }

        // Harmony (0Harmony) is loaded by Pulsar, not from the game folder, so point our references at that copy.
        private static System.Reflection.Assembly ResolveLoaded(object sender, ResolveEventArgs args)
        {
            string name = new System.Reflection.AssemblyName(args.Name).Name;
            foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
                if (assembly.GetName().Name == name)
                    return assembly;
            return null;
        }

        // Separate method so the Harmony reference only resolves after ResolveLoaded is registered.
        [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
        private void StartCapture()
        {
            AppDomain.CurrentDomain.AssemblyResolve += ResolveLoaded;
            try
            {
                capture = CreateCapture();
            }
            catch (Exception e)
            {
                Log($"Offscreen camera capture unavailable: {e}");
            }
        }

        [System.Runtime.CompilerServices.MethodImpl(System.Runtime.CompilerServices.MethodImplOptions.NoInlining)]
        private CameraCapture CreateCapture() => new CameraCapture(Log);

        private void Log(string message) => loader.Log(message);
    }
}
