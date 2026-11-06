using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Sandbox.Engine.Utils;
using Sandbox.Game.Entities;
using Sandbox.Game.Components;
using Sandbox.Game.Entities.Blocks;
using Sandbox.Game.GameSystems.TextSurfaceScripts;
using Sandbox.ModAPI;
using VRage.Audio;
using VRage.Data.Audio;
using VRage.Game.Entity;
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
        // Sound, when the source has any: seconds received from it, chunks handed to the game, game voice state.
        public double AudioSeconds;
        public int AudioChunks;
        public string AudioState;
        // Screen input: null if the source takes none, else whether it can (its screen mesh found) and is in use.
        public string Input;
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
        private const int AudioChunkBytes = 24000 * 2 / 10;
        // Metres: how far a screen's sound carries.
        private const float AudioRange = 40;

        internal sealed class Feed
        {
            public string Script;
            public ScreenGeometry Geometry;
            public bool GeometryLoaded;
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
            public MyEntity3DSoundEmitter Sound;
            public VRage.Audio.IMySourceVoice TaggedVoice;
            public Vector3? ScreenCentre;
            public long AudioBytes;
            public int AudioChunks;
            public readonly System.IO.MemoryStream PendingAudio = new System.IO.MemoryStream();
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
            AppDomain.CurrentDomain.SetData(AppHook, (Action<VRage.Game.ModAPI.Ingame.IMyCubeBlock, Sandbox.ModAPI.Ingame.IMyTextSurface, string>)RunApp);
        }

        // Where our screen apps find the live Screens (see ScreenTestScript).
        public const string AppHook = "SEScreens.RunApp";

        // An app of ours is on this surface: start its feed unless it's already running. Called every 10 ticks.
        private static void RunApp(VRage.Game.ModAPI.Ingame.IMyCubeBlock block, Sandbox.ModAPI.Ingame.IMyTextSurface surface, string script)
        {
            if (!IsRunning || !(block is IMyTerminalBlock terminal) || !(block is IngameSurfaceProvider provider))
                return;
            int index = -1;
            for (int i = 0; i < provider.SurfaceCount; i++)
                if (provider.GetSurface(i) == surface)
                    index = i;
            if (index < 0 || (Feeds.TryGetValue((block.EntityId, index), out var f) && f.Script == script && !f.Source.Ended))
                return;
            try
            {
                if (script == ScreenTestScript.Id)
                    Show(terminal, index, new SourceInfo { Kind = "testcard", Title = "Screen Test", Detail = "" },
                        (w, h) => new WindowCapture(IntPtr.Zero, w, h, 30), 30, DefaultResolution, script);
            }
            catch (Exception e)
            {
                log($"Screen app {script} on '{terminal.CustomName}' screen {index}: {e.Message}");
            }
        }

        // Every frame.
        public static void Update()
        {
            if (!IsRunning)
                return;
            if (frame++ % CheckScriptEveryFrames == 0)
                EnsureScriptRegistered();
            ScreenInput.Update();
            if (Feeds.Count == 0)
                return;
            List<(long, int)> ended = null;
            foreach (var pair in Feeds)
            {
                var f = pair.Value;
                if (f.Source.Ended || f.Block.Closed || f.Panel.ContentType != ContentType.SCRIPT || f.Panel.Script != f.Script)
                {
                    (ended ?? (ended = new List<(long, int)>())).Add(pair.Key);
                    continue;
                }
                if (f.Source is IAudioSource audio)
                    PlayAudio(f, audio);
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
                    End(key, restore: !f.Block.Closed && f.Panel.Script == f.Script);
                }
        }

        // Plugin unload or hot reload: stop every feed, put screens back, and take the script out of the list.
        public static void Stop()
        {
            ScreenInput.Stop();
            foreach (var key in Feeds.Keys.ToList())
                End(key, restore: true);
            var factory = MyTextSurfaceScriptFactory.Instance;
            if (factory != null && FactoryScripts?.GetValue(factory) is Dictionary<string, MyTextSurfaceScriptFactory.ScriptInfo> scripts)
            {
                scripts.Remove(MirrorScript.Id);
                scripts.Remove(ScreenTestScript.Id);
            }
            AppDomain.CurrentDomain.SetData(AppHook, null);
            ScreenAudio.RemoveCue();
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

        // Shows a web page on the block's screen. If that screen already has a browser, it navigates instead
        // (keeping its size and fps).
        public static MirrorInfo OpenBrowser(IMyTerminalBlock block, int surface, string url, int fps, int resolution = DefaultResolution)
        {
            if (Feeds.TryGetValue((block.EntityId, surface), out var f) && f.Source is BrowserSource open && !open.Ended)
            {
                open.Navigate(url);
                f.Info.Detail = url;
                return Describe(f);
            }
            fps = MathHelper.Clamp(fps, 1, 60);
            var info = new SourceInfo { Kind = "browser", Title = "Browser", Detail = url };
            return Show(block, surface, info, (w, h) => new BrowserSource(info, url, w, h, fps), fps, resolution);
        }

        // Sends a raw command to the browser on that screen (see SEScreens.Browser: move/down/up/wheel X Y,
        // text STRING). False if it has no browser.
        public static bool BrowserInput(long blockId, int surface, string command)
        {
            if (!(Feeds.TryGetValue((blockId, surface), out var f) && f.Source is BrowserSource browser))
                return false;
            browser.Send(command);
            return true;
        }

        public static bool BrowserAvailable => System.IO.File.Exists(BrowserSource.HelperPath());

        // Shows any source on the block's screen (switching it to Window Mirror, or the given app). create gets the texture size: the
        // screen's aspect ratio with its long side = resolution (64-2048). Throws ArgumentException for a block or
        // surface that can't show it. Showing again on the same surface keeps what it showed before the first.
        // script: the screen app the feed belongs to. Window Mirror by default; an app of ours (Screen Test) keeps its own.
        public static MirrorInfo Show(IMyTerminalBlock block, int surface, SourceInfo info, Func<int, int, IFrameSource> create,
            int fps, int resolution = DefaultResolution, string script = null)
        {
            script = script ?? MirrorScript.Id;
            if (!IsRunning)
                throw new InvalidOperationException("Screens isn't running");
            var panel = Panel(block, surface);
            var size = Size(panel, MathHelper.Clamp(resolution, 64, 2048));
            // First, so a source that can't start leaves the screen untouched.
            var source = create(size.X, size.Y);

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
            panel.Script = script;
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
                Source = source,
                Script = script,
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

        // Screens whose source takes input and that are showing it now (powered, in range).
        internal static IEnumerable<Feed> UsableFeeds()
        {
            foreach (var f in Feeds.Values)
                if (IsUsable(f))
                    yield return f;
        }

        internal static bool IsUsable(Feed f) =>
            f != null && Feeds.TryGetValue((f.Block.EntityId, f.Surface), out var current) && current == f
            && f.Source is IInputSink && !f.Source.Ended && !f.Block.Closed && f.Bound;

        internal static bool AnyUsableWithin(Vector3D position, double metres) =>
            UsableFeeds().Any(f => Vector3D.Distance(f.Block.WorldMatrix.Translation, position) <= metres);

        // The screen's surface, loaded the first time it's needed; null if the model has none we can read.
        internal static ScreenGeometry Geometry(Feed f)
        {
            if (!f.GeometryLoaded)
            {
                f.Geometry = ScreenGeometry.Load(f.Block, f.Panel);
                f.GeometryLoaded = true;
                if (f.Geometry == null)
                    log($"'{f.Block.CustomName}' screen {f.Surface}: no screen mesh found, so it can't take the mouse");
            }
            return f.Geometry;
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

        // A source's sound comes out of the screen, like a player's voice chat comes out of their character
        // (MyEntity3DSoundEmitter.PlaySound with raw buffers): 3D, fading out by AudioRange, at the game's volume.
        // Sent in 100 ms chunks; the voice holds at most 62 queued buffers. ScreenAudio keeps it positioned.
        private static void PlayAudio(Feed f, IAudioSource audio)
        {
            byte[] pcm = audio.TakeAudio();
            if (pcm != null)
            {
                f.PendingAudio.Write(pcm, 0, pcm.Length);
                f.AudioBytes += pcm.Length;
            }
            if (f.Sound == null)
            {
                if (f.PendingAudio.Length < AudioChunkBytes)
                    return;
                f.Sound = new MyEntity3DSoundEmitter((MyEntity)f.Block);
                f.ScreenCentre = ScreenAudio.LocalCentre(f.Block, f.Panel);
            }
            // Every frame: the emitter's position is stored relative to the camera.
            f.Sound.SetPosition(f.ScreenCentre.HasValue
                ? Vector3D.Transform(f.ScreenCentre.Value, f.Block.WorldMatrix)
                : f.Block.WorldMatrix.Translation);
            if (f.PendingAudio.Length < AudioChunkBytes)
                return;
            ScreenAudio.EnsureCue(AudioRange);
            f.Sound.PlaySound(f.PendingAudio.ToArray(), MyAudio.Static.VolumeGame, AudioRange, MySoundDimensions.D3);
            // A new voice starts whenever the last one ran dry; tag each one.
            if (f.Sound.Sound != f.TaggedVoice)
            {
                ScreenAudio.Tag(f.Sound.Sound);
                f.TaggedVoice = f.Sound.Sound;
            }
            f.AudioChunks++;
            f.PendingAudio.SetLength(0);
        }

        private static void End((long, int) key, bool restore)
        {
            var f = Feeds[key];
            Feeds.Remove(key);
            f.Source.Dispose();
            f.Sound?.StopSound(forced: true, cleanUp: true, cleanupSound: true);
            if (!f.Block.Closed)
                // Back on the game's own texture before ours goes. Forced: the panel's cache still thinks it's bound.
                f.Panel.ChangeRenderTexture(f.Panel.Area, f.Panel.GetRenderTextureName(), isForced: true);
            MyRenderProxy.DestroyGeneratedTexture(f.Texture);
            // A feed an app of ours started stays the player's choice: the app starts it again if it's still picked.
            if (!restore || f.Block.Closed || f.Script != MirrorScript.Id)
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
            AudioSeconds = Math.Round(f.AudioBytes / 48000.0, 1),
            AudioChunks = f.AudioChunks,
            Input = !(f.Source is IInputSink) ? null
                : ScreenInput.Focused == f ? "in use"
                : Geometry(f) == null ? "no screen mesh" : "ready",
            AudioState = !(f.Source is IAudioSource) ? null
                : f.Sound == null ? "no emitter"
                : f.Sound.Sound == null ? "no voice (game audio off?)"
                : (f.Sound.IsPlaying ? "playing" : "idle") + (f.Sound.Sound.CueEnum.ToString() == ScreenAudio.CueName ? ", positional" : ", fixed position")
                    + (f.ScreenCentre.HasValue ? ", at the screen" : ", at the block centre"),
        };
    }
}
