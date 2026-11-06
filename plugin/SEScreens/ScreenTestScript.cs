using System;
using Sandbox.Game.GameSystems.TextSurfaceScripts;
using Sandbox.ModAPI.Ingame;
using VRage.Game.GUI.TextPanel;
using VRage.Game.ModAPI.Ingame;
using VRageMath;

namespace SEScreens
{
    // "Screen Test" in an LCD's script list: picking it shows the test card (grey and colour bars of known levels)
    // with a cross where cursor mode's mouse lands, red while a button is held. No assistant or RPC needed, and the
    // choice is saved with the world like any screen app; the card comes back when the world loads.
    //
    // The app asks Screens for the feed through an AppDomain hook rather than calling it: after a hot reload this
    // instance belongs to the old assembly, and the hook always points at the live one.
    [MyTextSurfaceScript(Id, "Screen Test")]
    public sealed class ScreenTestScript : MyTextSurfaceScriptBase
    {
        public const string Id = "SEScreens_ScreenTest";

        public ScreenTestScript(IMyTextSurface surface, IMyCubeBlock block, Vector2 size)
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
                frame.Add(new MySprite(SpriteType.TEXT, "Screen Test\nneeds the SEScreens plugin", area.Center - new Vector2(0, 30 * m_scale.Y),
                    null, new Color(90, 90, 90), "White", TextAlignment.CENTER, 1.0f * m_scale.Y));
            }
            (AppDomain.CurrentDomain.GetData(Screens.AppHook) as Action<IMyCubeBlock, IMyTextSurface, string>)?.Invoke(m_block, m_surface, Id);
        }
    }
}
