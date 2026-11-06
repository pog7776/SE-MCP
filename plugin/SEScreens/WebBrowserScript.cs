using System;
using Sandbox.Game.GameSystems.TextSurfaceScripts;
using Sandbox.ModAPI.Ingame;
using VRage.Game.GUI.TextPanel;
using VRage.Game.ModAPI.Ingame;
using VRageMath;

namespace SEScreens
{
    // "Web Browser" in an LCD's script list: picking it opens the screen's home page (from the block's CustomData,
    // see ScreenSettings; added there on first use so it's easy to find and edit). Editing it there sends the screen
    // to the new page. Use it in cursor mode; Ctrl+L opens the address dialog, Alt+Home goes home.
    // Like Screen Test, it reaches Screens through the AppDomain hook so it survives hot reload.
    [MyTextSurfaceScript(Id, "Web Browser")]
    public sealed class WebBrowserScript : MyTextSurfaceScriptBase
    {
        public const string Id = "SEScreens_WebBrowser";

        public WebBrowserScript(IMyTextSurface surface, IMyCubeBlock block, Vector2 size)
            : base(surface, block, size)
        {
        }

        public override ScriptUpdate NeedsUpdate => ScriptUpdate.Update10;

        public override void Run()
        {
            base.Run();
            // Identical sprites every run, so the game doesn't redraw over the feed (see MirrorScript).
            using (var frame = m_surface.DrawFrame())
            {
                var area = new RectangleF((m_surface.TextureSize - m_surface.SurfaceSize) / 2, m_surface.SurfaceSize);
                frame.Add(new MySprite(SpriteType.TEXTURE, "SquareSimple", area.Center, area.Size, Color.Black));
                frame.Add(new MySprite(SpriteType.TEXT, "Web Browser\nneeds the SEScreens plugin", area.Center - new Vector2(0, 30 * m_scale.Y),
                    null, new Color(90, 90, 90), "White", TextAlignment.CENTER, 1.0f * m_scale.Y));
            }
            (AppDomain.CurrentDomain.GetData(Screens.AppHook) as Action<IMyCubeBlock, IMyTextSurface, string>)?.Invoke(m_block, m_surface, Id);
        }
    }
}
