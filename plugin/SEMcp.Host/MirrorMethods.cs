using System;
using System.Globalization;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Text.Json.Nodes;
using SEMcp.Core;
using SEScreens;

namespace SEMcp.Host
{
    // mirror.* RPCs: desktop windows on LCDs, done by the SEScreens plugin (see ScreensLink). This side adds what
    // SEMcp owns: block access, read-only mode and the allowWindowMirror switch.
    internal static class MirrorMethods
    {
        private static bool allowed;

        public static void Register(Dispatcher dispatcher, bool allow)
        {
            allowed = allow;
            var access = new Access(dispatcher);
            // Require() runs before any method that names an SEScreens type is compiled, so a missing plugin is a
            // feature_disabled error rather than a FileNotFoundException.
            dispatcher.Register("mirror.windows", p => { Require(); return Windows(p); });
            dispatcher.Register("mirror.start", p => { Require(); return Start(access, dispatcher, p); });
            dispatcher.Register("mirror.stop", p => { Require(); return Stop(access, dispatcher, p); });
            dispatcher.Register("mirror.list", p => { Require(); return List(); });
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static object List() => new
        {
            mirrors = Screens.ListMirrors().Select(Describe).ToList(),
            gameFps = Screens.GameFps,
            frameMs = Math.Round(Screens.FrameTimeMs, 2),
        };

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static object Windows(JsonObject p)
        {
            string filter = p.String("filter");
            return new
            {
                windows = Screens.ListWindows()
                    .Where(w => filter == null || Screens.Matches(w, filter))
                    .Select(w => new { handle = w.HandleText, title = w.Title, process = w.Process, width = w.Width, height = w.Height })
                    .ToList(),
            };
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static object Start(Access access, Dispatcher dispatcher, JsonObject p)
        {
            dispatcher.RequireWritable();
            var block = access.Block(p.Id("blockId"));
            string handle = p.String("handle");
            WindowInfo window;
            if (handle != null)
            {
                window = Screens.FindWindowByHandle(handle) ?? throw new RpcException(ErrorKind.NotFound, $"No window with handle {handle}");
            }
            else
            {
                string query = p.String("window", required: true);
                window = Screens.FindWindow(query) ?? throw new RpcException(ErrorKind.NotFound, $"No visible window matches '{query}'");
            }
            try
            {
                return Describe(Screens.StartMirror(block, (int)p.Number("surface", 0), window, (int)p.Number("fps", 15),
                    (int)p.Number("resolution", Screens.DefaultResolution)));
            }
            catch (ArgumentException e)
            {
                throw new RpcException(ErrorKind.InvalidParams, e.Message);
            }
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        private static object Stop(Access access, Dispatcher dispatcher, JsonObject p)
        {
            dispatcher.RequireWritable();
            if (p["blockId"] == null)
                return new { stopped = Screens.StopAll() };
            var block = access.Block(p.Id("blockId"));
            int surface = (int)p.Number("surface", 0);
            if (!Screens.StopMirror(block.EntityId, surface))
                throw new RpcException(ErrorKind.NotFound, $"'{block.CustomName}' screen {surface} isn't mirroring a window");
            return new { stopped = 1 };
        }

        private static void Require()
        {
            if (!allowed)
                throw new RpcException(ErrorKind.FeatureDisabled, "Window mirroring is off (allowWindowMirror in config.json)");
            if (!ScreensLink.Available)
                throw new RpcException(ErrorKind.FeatureDisabled, "The SEScreens plugin isn't installed");
        }

        private static object Describe(MirrorInfo m) => new
        {
            blockId = m.BlockId.ToString(CultureInfo.InvariantCulture),
            block = m.Block,
            surface = m.Surface,
            kind = m.Kind,
            window = m.Window,
            process = m.Process,
            handle = m.Handle,
            fps = m.Fps,
            resolution = new[] { m.Width, m.Height },
            captured = m.Captured,
            uploaded = m.Uploaded,
            error = m.Error,
        };
    }
}
