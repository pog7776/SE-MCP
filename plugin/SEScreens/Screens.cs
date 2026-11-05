using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Sandbox.Engine.Utils;
using Sandbox.Game.Components;
using Sandbox.Game.Entities.Blocks;
using Sandbox.Game.GameSystems.TextSurfaceScripts;
using Sandbox.ModAPI;
using VRage.Game.GUI.TextPanel;
using VRageMath;
using VRageRender;
using VRageRender.Messages;
using IngameSurfaceProvider = Sandbox.ModAPI.Ingame.IMyTextSurfaceProvider;

namespace SEScreens
{
    public sealed class WindowInfo
    {
        public IntPtr Handle;
        public string Title;
        public string Process;
        public int Width, Height;

        public string HandleText => "0x" + Handle.ToInt64().ToString("X");
    }

    public sealed class MirrorInfo
    {
        public long BlockId;
        public string Block;
        public int Surface;
        public string Kind;
        public string Window;   // the source's title
        public string Process;  // the source's detail (process name, URL)
        public string Handle;
        public int Fps;
        public int Width, Height;
        public int Captured;
        public int Uploaded;
        public string Error;
    }

    // Live content on LCDs. A screen showing a source is switched to MirrorScript (so the game draws nothing of its
    // own there) and bound to a texture of ours, sized for the source rather than the game's small LCD texture.
    // New frames replace that texture's pixels (MyRenderProxy.ResetGeneratedTexture). The game's own texture
    // still tells us when the screen is powered and in range; while it isn't, the game's offline look shows.
    // Local to this client: other players see the idle card.
    //
    // This is the public API other plugins (SEMcp) call. Everything here runs on the game thread. Whoever loaded
    // the assembly drives the lifecycle: ScreensPlugin under Pulsar, or SEMcp's host in development.
    public static class Screens
    {
        public const int DefaultResolution = 1024;
        private const int CheckScriptEveryFrames = 100;
        // The game rebinds a screen's texture on some events (recolour, model change); put ours back this often.
        private const int RebindEveryFrames = 30;

        private sealed class Feed
        {
            public IMyTerminalBlock Block;
            public int Surface;
            public MyTextPanelComponent Panel;
            public IFrameSource Source;
            public SourceInfo Info;
            public string Texture;
            public bool Bound;
            public int Fps;
            public ContentType PreviousContent;
            public string PreviousScript;
            public int Uploads;
        }

        private static readonly Dictionary<(long, int), Feed> Feeds = new Dictionary<(long, int), Feed>();
        private static readonly FieldInfo TextureGenerated =
            typeof(MyTextPanelComponent).GetField("m_textureGenerated", BindingFlags.Instance | BindingFlags.NonPublic);
        private static readonly FieldInfo PanelRender =
            typeof(MyTextPanelComponent).GetField("m_render", BindingFlags.Instance | BindingFlags.NonPublic);
        private static readonly FieldInfo FactoryScripts =
            typeof(MyTextSurfaceScriptFactory).GetField("m_scripts", BindingFlags.Instance | BindingFlags.NonPublic);
        private static int frame;
        private static Action<string> log = _ => { };

        public static bool IsRunning { get; private set; }

        // A stand-in "window" of grey and colour bars at known levels, for checking how screens show colour.
        public static readonly WindowInfo TestCard = new WindowInfo { Handle = IntPtr.Zero, Title = "Test card", Process = "" };

        // The game's frame rate and average frame time (ms), for judging what feeds cost.
        public static int GameFps => MyFpsManager.GetFps();
        public static float FrameTimeMs => MyFpsManager.FrameTimeAvg;

        public static void Start(Action<string> logger)
        {
            log = logger ?? (_ => { });
            IsRunning = true;
        }

        // Every frame.
        public static void Update()
        {
            if (!IsRunning)
                return;
            if (frame++ % CheckScriptEveryFrames == 0)
                EnsureScriptRegistered();
            if (Feeds.Count == 0)
                return;
            List<(long, int)> ended = null;
            foreach (var pair in Feeds)
            {
                var f = pair.Value;
                if (f.Source.Ended || f.Block.Closed || f.Panel.ContentType != ContentType.SCRIPT || f.Panel.Script != MirrorScript.Id)
                {
                    (ended ?? (ended = new List<(long, int)>())).Add(pair.Key);
                    continue;
                }
                // Out of range, unpowered or broken screens drop the game's texture and show its offline look.
                if (!(TextureGenerated?.GetValue(f.Panel) is bool live && live))
                {
                    f.Bound = false;
                    continue;
                }
                if (!f.Bound || frame % RebindEveryFrames == 0)
                {
                    Render(f.Panel)?.ChangeTexture(f.Panel.Area, f.Texture);
                    f.Bound = true;
                }
                byte[] pixels = f.Source.TakeLatest();
                if (pixels == null)
                    continue;
                MyRenderProxy.ResetGeneratedTexture(f.Texture, pixels);
                f.Uploads++;
            }
            if (ended != null)
                foreach (var key in ended)
                {
                    var f = Feeds[key];
                    if (f.Source.Error != null)
                        log($"Feed of '{f.Info.Title}' stopped: {f.Source.Error}");
                    End(key, restore: !f.Block.Closed && f.Panel.Script == MirrorScript.Id);
                }
        }

        // Plugin unload or hot reload: stop every feed, put screens back, and take the script out of the list.
        public static void Stop()
        {
            foreach (var key in Feeds.Keys.ToList())
                End(key, restore: true);
            var factory = MyTextSurfaceScriptFactory.Instance;
            if (factory != null && FactoryScripts?.GetValue(factory) is Dictionary<string, MyTextSurfaceScriptFactory.ScriptInfo> scripts)
                scripts.Remove(MirrorScript.Id);
            IsRunning = false;
        }

        // Visible, titled top-level windows of other processes, front-most first.
        public static List<WindowInfo> ListWindows() => WindowCapture.List();

        // Front-most window whose title contains query or whose process is named query; "testcard" is the test card.
        public static WindowInfo FindWindow(string query)
        {
            if (query.Equals("testcard", StringComparison.OrdinalIgnoreCase))
                return TestCard;
            return ListWindows().FirstOrDefault(w => Matches(w, query));
        }

        public static WindowInfo FindWindowByHandle(string handle) =>
            ListWindows().FirstOrDefault(w => string.Equals(w.HandleText, handle, StringComparison.OrdinalIgnoreCase));

        public static bool Matches(WindowInfo w, string query) =>
            w.Title.IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0
            || string.Equals(w.Process, query, StringComparison.OrdinalIgnoreCase);

        public static List<MirrorInfo> ListMirrors() => Feeds.Values.Select(Describe).ToList();

        // Mirrors a desktop window (or the test card) onto the block's screen.
        public static MirrorInfo StartMirror(IMyTerminalBlock block, int surface, WindowInfo window, int fps, int resolution = DefaultResolution)
        {
            fps = MathHelper.Clamp(fps, 1, 30);
            var info = new SourceInfo
            {
                Kind = window.Handle == IntPtr.Zero ? "testcard" : "window",
                Title = window.Title,
                Detail = window.Process,
                Handle = window.Handle == IntPtr.Zero ? null : window.HandleText,
            };
            return Show(block, surface, info, (w, h) => new WindowCapture(window.Handle, w, h, fps), fps, resolution);
        }

        // Shows any source on the block's screen (switching it to Window Mirror). create gets the texture size: the
        // screen's aspect ratio with its long side = resolution (64-2048). Throws ArgumentException for a block or
        // surface that can't show it. Showing again on the same surface keeps what it showed before the first.
        public static MirrorInfo Show(IMyTerminalBlock block, int surface, SourceInfo info, Func<int, int, IFrameSource> create,
            int fps, int resolution = DefaultResolution)
        {
            if (!IsRunning)
                throw new InvalidOperationException("Screens isn't running");
            var panel = Panel(block, surface);
            var size = Size(panel, MathHelper.Clamp(resolution, 64, 2048));

            var key = (block.EntityId, surface);
            ContentType previousContent = panel.ContentType;
            string previousScript = panel.Script;
            if (Feeds.TryGetValue(key, out var old))
            {
                previousContent = old.PreviousContent;
                previousScript = old.PreviousScript;
                Feeds.Remove(key);
                old.Source.Dispose();
                MyRenderProxy.DestroyGeneratedTexture(old.Texture);
            }

            EnsureScriptRegistered();
            panel.ContentType = ContentType.SCRIPT;
            panel.Script = MirrorScript.Id;
            string texture = $"SEScreens_{block.EntityId}_{surface}";
            MyRenderProxy.CreateGeneratedTexture(texture, size.X, size.Y, MyGeneratedTextureType.RGBA, 1, null, generateMipmaps: true, immediatelyReady: true);
            var f = new Feed
            {
                Block = block,
                Surface = surface,
                Panel = panel,
                Info = info,
                Texture = texture,
                Fps = fps,
                PreviousContent = previousContent,
                PreviousScript = previousScript,
                Source = create(size.X, size.Y),
            };
            Feeds[key] = f;
            return Describe(f);
        }

        // False if that surface wasn't showing anything of ours.
        public static bool StopMirror(long blockId, int surface)
        {
            if (!Feeds.ContainsKey((blockId, surface)))
                return false;
            End((blockId, surface), restore: true);
            return true;
        }

        public static int StopAll()
        {
            int count = Feeds.Count;
            foreach (var key in Feeds.Keys.ToList())
                End(key, restore: true);
            return count;
        }

        // The factory reloads its list with every session and only scans the game's and Pulsar's assemblies, not
        // a hot-reloaded copy, so add ours back whenever it's missing.
        private static void EnsureScriptRegistered()
        {
            var factory = MyTextSurfaceScriptFactory.Instance;
            if (factory == null || factory.Scripts.ContainsKey(MirrorScript.Id))
                return;
            factory.RegisterFromAssembly(typeof(MirrorScript).Assembly);
        }

        private static void End((long, int) key, bool restore)
        {
            var f = Feeds[key];
            Feeds.Remove(key);
            f.Source.Dispose();
            if (!f.Block.Closed)
                // Back on the game's own texture before ours goes. Forced: the panel's cache still thinks it's bound.
                f.Panel.ChangeRenderTexture(f.Panel.Area, f.Panel.GetRenderTextureName(), isForced: true);
            MyRenderProxy.DestroyGeneratedTexture(f.Texture);
            if (!restore || f.Block.Closed)
                return;
            // Back to what the screen showed before; a screen that was already on Window Mirror keeps its idle card.
            if (f.PreviousContent == ContentType.SCRIPT && f.PreviousScript == MirrorScript.Id)
                return;
            f.Panel.ContentType = f.PreviousContent;
            f.Panel.Script = f.PreviousContent == ContentType.SCRIPT ? f.PreviousScript : "";
            // Multi-screen blocks (cockpits, consoles) don't redraw a screen for a content change alone; update it
            // now, as the block's own 10-tick update would.
            f.Panel.UpdateAfterSimulation(f.Block.IsWorking, isInRange: true);
        }

        private static MyRenderComponentScreenAreas Render(MyTextPanelComponent panel) =>
            PanelRender?.GetValue(panel) as MyRenderComponentScreenAreas;

        private static MyTextPanelComponent Panel(IMyTerminalBlock block, int surface)
        {
            if (!(block is IngameSurfaceProvider provider) || provider.SurfaceCount == 0)
                throw new ArgumentException($"'{block.CustomName}' has no screens");
            if (surface < 0 || surface >= provider.SurfaceCount)
                throw new ArgumentException($"'{block.CustomName}' has screens 0-{provider.SurfaceCount - 1}");
            if (!(provider.GetSurface(surface) is MyTextPanelComponent panel))
                throw new ArgumentException($"'{block.CustomName}' screen {surface} isn't a text panel surface");
            return panel;
        }

        // The game's texture has the screen's aspect ratio (its UVs cover it whole); scale it to resolution.
        private static Vector2I Size(MyTextPanelComponent panel, int resolution)
        {
            float w = Math.Max(1, panel.TextureSize.X), h = Math.Max(1, panel.TextureSize.Y);
            float scale = resolution / Math.Max(w, h);
            return new Vector2I(Math.Max(1, (int)(w * scale)), Math.Max(1, (int)(h * scale)));
        }

        private static MirrorInfo Describe(Feed f) => new MirrorInfo
        {
            BlockId = f.Block.EntityId,
            Block = f.Block.CustomName,
            Surface = f.Surface,
            Kind = f.Info.Kind,
            Window = f.Info.Title,
            Process = f.Info.Detail,
            Handle = f.Info.Handle,
            Fps = f.Fps,
            Width = f.Source.Width,
            Height = f.Source.Height,
            Captured = f.Source.Frames,
            Uploaded = f.Uploads,
            Error = f.Source.Error,
        };
    }
}
