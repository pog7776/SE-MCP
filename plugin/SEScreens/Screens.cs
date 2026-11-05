using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Sandbox.Game.Components;
using Sandbox.Game.Entities.Blocks;
using Sandbox.Game.GameSystems.TextSurfaceScripts;
using Sandbox.ModAPI;
using VRage.Game.GUI.TextPanel;
using VRageMath;
using VRageRender;
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
        public string Window;
        public string Process;
        public string Handle;
        public int Fps;
        public int Width, Height;
        public int Captured;
        public int Uploaded;
        public string Error;
    }

    // Live desktop windows on LCDs. The surface is switched to MirrorScript, and each new captured frame replaces
    // the pixels of the screen's own generated texture (MyRenderProxy.ResetGeneratedTexture), so the game keeps
    // binding, range culling and power state. Local to this client: other players see the idle card.
    //
    // This is the public API other plugins (SEMcp) call. Everything here runs on the game thread. Whoever loaded
    // the assembly drives the lifecycle: ScreensPlugin under Pulsar, or SEMcp's host in development.
    public static class Screens
    {
        private const int CheckScriptEveryFrames = 100;

        private sealed class Mirror
        {
            public IMyTerminalBlock Block;
            public int Surface;
            public MyTextPanelComponent Panel;
            public WindowCapture Capture;
            public WindowInfo Window;
            public int Fps;
            public ContentType PreviousContent;
            public string PreviousScript;
            public int Uploads;
        }

        private static readonly Dictionary<(long, int), Mirror> Mirrors = new Dictionary<(long, int), Mirror>();
        private static readonly FieldInfo TextureGenerated =
            typeof(MyTextPanelComponent).GetField("m_textureGenerated", BindingFlags.Instance | BindingFlags.NonPublic);
        private static readonly FieldInfo FactoryScripts =
            typeof(MyTextSurfaceScriptFactory).GetField("m_scripts", BindingFlags.Instance | BindingFlags.NonPublic);
        private static int frame;
        private static Action<string> log = _ => { };

        public static bool IsRunning { get; private set; }

        // A stand-in "window" of grey and colour bars at known levels, for checking how screens show colour.
        public static readonly WindowInfo TestCard = new WindowInfo { Handle = IntPtr.Zero, Title = "Test card", Process = "" };

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
            if (Mirrors.Count == 0)
                return;
            List<(long, int)> ended = null;
            foreach (var pair in Mirrors)
            {
                var m = pair.Value;
                if (m.Capture.Ended || m.Block.Closed || m.Panel.ContentType != ContentType.SCRIPT || m.Panel.Script != MirrorScript.Id)
                {
                    (ended ?? (ended = new List<(long, int)>())).Add(pair.Key);
                    continue;
                }
                var size = Size(m.Panel);
                if (size.X != m.Capture.Width || size.Y != m.Capture.Height)
                {
                    m.Capture.Dispose();
                    m.Capture = new WindowCapture(m.Capture.Window, size.X, size.Y, MyRenderComponentScreenAreas.GetTextureByteCount(size), m.Fps);
                    continue;
                }
                byte[] pixels = m.Capture.TakeLatest();
                // Out of range or unpowered screens drop their texture; skip them rather than upload into nothing.
                if (pixels == null || !(TextureGenerated?.GetValue(m.Panel) is bool generated && generated))
                    continue;
                MyRenderProxy.ResetGeneratedTexture(m.Panel.GetRenderTextureName(), pixels);
                m.Uploads++;
            }
            if (ended != null)
                foreach (var key in ended)
                {
                    var m = Mirrors[key];
                    if (m.Capture.Error != null)
                        log($"Mirror of '{m.Window.Title}' stopped: {m.Capture.Error}");
                    End(key, restore: !m.Block.Closed && m.Panel.Script == MirrorScript.Id);
                }
        }

        // Plugin unload or hot reload: stop capturing, put screens back, and take the script out of the list.
        public static void Stop()
        {
            foreach (var key in Mirrors.Keys.ToList())
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

        public static List<MirrorInfo> ListMirrors() => Mirrors.Values.Select(Describe).ToList();

        // Shows window on the block's screen (switching it to Window Mirror). Throws ArgumentException for a block
        // or surface that can't show it. Starting again on the same surface keeps what it showed before the first.
        public static MirrorInfo StartMirror(IMyTerminalBlock block, int surface, WindowInfo window, int fps)
        {
            if (!IsRunning)
                throw new InvalidOperationException("Screens isn't running");
            var panel = Panel(block, surface);
            fps = MathHelper.Clamp(fps, 1, 30);

            var key = (block.EntityId, surface);
            ContentType previousContent = panel.ContentType;
            string previousScript = panel.Script;
            if (Mirrors.TryGetValue(key, out var old))
            {
                previousContent = old.PreviousContent;
                previousScript = old.PreviousScript;
                old.Capture.Dispose();
            }

            EnsureScriptRegistered();
            panel.ContentType = ContentType.SCRIPT;
            panel.Script = MirrorScript.Id;
            var size = Size(panel);
            var m = new Mirror
            {
                Block = block,
                Surface = surface,
                Panel = panel,
                Window = window,
                Fps = fps,
                PreviousContent = previousContent,
                PreviousScript = previousScript,
                Capture = new WindowCapture(window.Handle, size.X, size.Y, MyRenderComponentScreenAreas.GetTextureByteCount(size), fps),
            };
            Mirrors[key] = m;
            return Describe(m);
        }

        // False if that surface wasn't mirroring.
        public static bool StopMirror(long blockId, int surface)
        {
            if (!Mirrors.ContainsKey((blockId, surface)))
                return false;
            End((blockId, surface), restore: true);
            return true;
        }

        public static int StopAll()
        {
            int count = Mirrors.Count;
            foreach (var key in Mirrors.Keys.ToList())
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
            var m = Mirrors[key];
            Mirrors.Remove(key);
            m.Capture.Dispose();
            if (!restore || m.Block.Closed)
                return;
            // Back to what the screen showed before; a screen that was already on Window Mirror goes to its idle card.
            if (m.PreviousContent == ContentType.SCRIPT && m.PreviousScript == MirrorScript.Id)
                return;
            m.Panel.ContentType = m.PreviousContent;
            m.Panel.Script = m.PreviousContent == ContentType.SCRIPT ? m.PreviousScript : "";
            // Multi-screen blocks (cockpits, consoles) don't redraw a screen for a content change alone, so it kept
            // showing the mirror's texture; update it now, as the block's own 10-tick update would.
            m.Panel.UpdateAfterSimulation(m.Block.IsWorking, isInRange: true);
        }

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

        private static Vector2I Size(MyTextPanelComponent panel) =>
            new Vector2I(Math.Max(1, (int)panel.TextureSize.X), Math.Max(1, (int)panel.TextureSize.Y));

        private static MirrorInfo Describe(Mirror m) => new MirrorInfo
        {
            BlockId = m.Block.EntityId,
            Block = m.Block.CustomName,
            Surface = m.Surface,
            Window = m.Window.Title,
            Process = m.Window.Process,
            Handle = m.Window.HandleText,
            Fps = m.Fps,
            Width = m.Capture.Width,
            Height = m.Capture.Height,
            Captured = m.Capture.Frames,
            Uploaded = m.Uploads,
            Error = m.Capture.Error,
        };
    }
}
