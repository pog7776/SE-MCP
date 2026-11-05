using Sandbox.Game.GameSystems.TextSurfaceScripts;
using Sandbox.ModAPI.Ingame;
using VRage.Game.GUI.TextPanel;
using VRage.Game.ModAPI.Ingame;
using VRageMath;

namespace SEScreens
{
    // "Window Mirror" in an LCD's script list. It owns the screen so the game draws nothing else there; while
    // Screens has a mirror on the surface, it writes the window straight into the screen's texture.
    // The script's own drawing is an idle card that only renders once: the game re-renders a script surface
    // only when its sprites change, so identical sprites every run leave the mirrored frames alone.
    [MyTextSurfaceScript(Id, "Window Mirror")]
    public sealed class MirrorScript : MyTextSurfaceScriptBase
    {
        public const string Id = "SEScreens_WindowMirror";

        public MirrorScript(IMyTextSurface surface, IMyCubeBlock block, Vector2 size)
            : base(surface, block, size)
        {
        }

        public override ScriptUpdate NeedsUpdate => ScriptUpdate.Update100;

        public override void Run()
        {
            base.Run();
            using (var frame = m_surface.DrawFrame())
            {
                var area = new RectangleF((m_surface.TextureSize - m_surface.SurfaceSize) / 2, m_surface.SurfaceSize);
                frame.Add(new MySprite(SpriteType.TEXTURE, "SquareSimple", area.Center, area.Size, Color.Black));
                frame.Add(new MySprite(SpriteType.TEXT, "Window Mirror\nno signal", area.Center - new Vector2(0, 30 * m_scale.Y),
                    null, new Color(90, 90, 90), "White", TextAlignment.CENTER, 1.2f * m_scale.Y));
            }
        }
    }
}
