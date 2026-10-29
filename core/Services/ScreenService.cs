using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using VRage.Game.GUI.TextPanel;
using VRage.Game.ModAPI;
using VRageMath;
using IngameSurface = Sandbox.ModAPI.Ingame.IMyTextSurface;
using IngameSurfaceProvider = Sandbox.ModAPI.Ingame.IMyTextSurfaceProvider;

namespace SEMcp.Core.Services
{
    // LCDs, cockpit screens, PB and button-panel screens: every text surface a block has. Writing goes through
    // IMyTextSurface.WriteText, the same call a PB script makes; the block syncs the text like a terminal edit.
    internal sealed class ScreenService
    {
        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public ScreenService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("screens.list", List);
            dispatcher.Register("screen.write", Write);
            dispatcher.Register("screen.mode", Mode);
        }

        private static string Hex(Color c) => $"#{c.R:X2}{c.G:X2}{c.B:X2}";

        private static object Surface(IngameSurface s, int index, bool fullText)
        {
            string text = s.GetText() ?? "";
            var images = new List<string>();
            s.GetSelectedImages(images);
            return new
            {
                index,
                name = s.DisplayName,
                content = s.ContentType.ToString(),
                script = string.IsNullOrEmpty(s.Script) ? null : s.Script,
                images,
                font = s.Font,
                fontSize = Math.Round(s.FontSize, 2),
                fontColor = Hex(s.FontColor),
                background = Hex(s.BackgroundColor),
                alignment = s.Alignment.ToString(),
                padding = Math.Round(s.TextPadding, 1),
                size = new[] { Math.Round(s.SurfaceSize.X), Math.Round(s.SurfaceSize.Y) },
                textLength = text.Length,
                text = fullText || text.Length <= 200 ? text : text.Substring(0, 200) + "...",
            };
        }

        private static bool HasSurfaces(IMyTerminalBlock block) =>
            block is IngameSurfaceProvider provider && provider.SurfaceCount > 0;

        private object ScreensOf(IMyTerminalBlock block, bool fullText)
        {
            var provider = (IngameSurfaceProvider)block;
            return new
            {
                blockId = Dto.Id(block.EntityId),
                name = block.CustomName,
                type = Dto.ShortType(block.BlockDefinition.TypeIdString),
                working = block.IsWorking,
                surfaces = Enumerable.Range(0, provider.SurfaceCount).Select(i => Surface(provider.GetSurface(i), i, fullText)).ToList(),
            };
        }

        private object List(JsonObject p)
        {
            if (p["blockId"] != null)
            {
                var block = access.Block(p.Id("blockId"));
                if (!HasSurfaces(block))
                    throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' has no screens");
                return new { blocks = new[] { ScreensOf(block, fullText: true) } };
            }
            var grid = access.Grid(p.Id("gridId"));
            return new
            {
                blocks = Access.Construct(grid)
                    .SelectMany(g => g.GetFatBlocks<IMyTerminalBlock>())
                    .Where(b => HasSurfaces(b) && access.CanUse(b))
                    .OrderBy(b => b.CustomName, StringComparer.OrdinalIgnoreCase)
                    .Select(b => ScreensOf(b, fullText: false))
                    .ToList(),
            };
        }

        private (IMyTerminalBlock block, IngameSurface surface, int index) Target(JsonObject p)
        {
            var block = access.Block(p.Id("blockId"));
            if (!HasSurfaces(block))
                throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' has no screens");
            var provider = (IngameSurfaceProvider)block;
            int index = (int)p.Number("surface", 0);
            if (index < 0 || index >= provider.SurfaceCount)
                throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' has screens 0-{provider.SurfaceCount - 1}");
            return (block, provider.GetSurface(index), index);
        }

        // Shows text on a screen: switches it to text mode, then applies any style given.
        private object Write(JsonObject p)
        {
            dispatcher.RequireWritable();
            var (block, surface, index) = Target(p);
            var before = Surface(surface, index, fullText: true);

            surface.ContentType = ContentType.TEXT_AND_IMAGE;
            if (p.Bool("clearImages", true))
                surface.ClearImagesFromSelection();
            if (p["fontSize"] != null)
                surface.FontSize = (float)MathHelper.Clamp(p.Number("fontSize", 1), 0.1, 10);
            if (p.String("font") is string font)
                surface.Font = font;
            if (p["fontColor"] != null)
                surface.FontColor = ControlService.ToColor(p["fontColor"]);
            if (p["background"] != null)
                surface.BackgroundColor = ControlService.ToColor(p["background"]);
            if (p.String("alignment") is string align)
            {
                if (!Enum.TryParse(align, true, out TextAlignment alignment))
                    throw new RpcException(ErrorKind.InvalidParams, "alignment must be left, center or right");
                surface.Alignment = alignment;
            }
            if (p["padding"] != null)
                surface.TextPadding = (float)MathHelper.Clamp(p.Number("padding", 2), 0, 50);
            if (p["text"] != null)
                surface.WriteText(p.String("text") ?? "", p.Bool("append", false));

            dispatcher.Context.Log($"screen {index} of '{block.CustomName}' ({block.EntityId}) written");
            return new { block = block.CustomName, before, after = Surface(surface, index, fullText: false) };
        }

        // Switches a screen's mode: "text", "script" (with a script name, e.g. TSS_ArtificialHorizon), or "none".
        private object Mode(JsonObject p)
        {
            dispatcher.RequireWritable();
            var (block, surface, index) = Target(p);
            var before = Surface(surface, index, fullText: false);
            switch ((p.String("mode", required: true) ?? "").ToLowerInvariant())
            {
                case "text":
                    surface.ContentType = ContentType.TEXT_AND_IMAGE;
                    break;
                case "none":
                    surface.ContentType = ContentType.NONE;
                    break;
                case "script":
                    string script = p.String("script", required: true);
                    var scripts = new List<string>();
                    surface.GetScripts(scripts);
                    if (!scripts.Contains(script))
                        throw new RpcException(ErrorKind.InvalidParams, $"Unknown script '{script}'. Available: {string.Join(", ", scripts)}");
                    surface.ContentType = ContentType.SCRIPT;
                    surface.Script = script;
                    break;
                default:
                    throw new RpcException(ErrorKind.InvalidParams, "mode must be text, script or none");
            }
            dispatcher.Context.Log($"screen {index} of '{block.CustomName}' ({block.EntityId}) mode {p.String("mode")}");
            return new { block = block.CustomName, before, after = Surface(surface, index, fullText: false) };
        }
    }
}
