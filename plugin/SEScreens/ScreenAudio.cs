using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Sandbox.Game.Entities;
using Sandbox.Game.Entities.Blocks;
using Sandbox.ModAPI;
using VRage.Audio;
using VRage.Data.Audio;
using VRage.Utils;
using VRageMath;

namespace SEScreens
{
    // Makes a screen's streamed sound positional.
    //
    // Streamed voices (MyEntity3DSoundEmitter.PlaySound with buffers, as voice chat uses) get their 3D position
    // once, when the voice starts: the audio engine re-positions a voice each frame only if its cue is in the cue
    // bank (MyXAudio2.Update3DVoicePosition). So we register a cue of our own, tag our voices with it, and they
    // pan and fade as you move. The emitter's position is set each frame to the screen's own centre, so each
    // screen of a multi-screen block sounds from where it is.
    internal static class ScreenAudio
    {
        public const string CueName = "SEScreens_Stream";
        private static readonly MyCueId Cue = new MyCueId(MyStringHash.GetOrCompute(CueName));

        private static readonly FieldInfo VoiceCue =
            Type.GetType("VRage.Audio.MySourceVoice, VRage.Audio")?.GetField("m_cueId", BindingFlags.Instance | BindingFlags.NonPublic);
        private static readonly FieldInfo ScreenAreas =
            typeof(Sandbox.Game.Components.MyRenderComponentScreenAreas).GetField("m_screenAreas", BindingFlags.Instance | BindingFlags.NonPublic);
        private static readonly FieldInfo PanelRender =
            typeof(MyTextPanelComponent).GetField("m_render", BindingFlags.Instance | BindingFlags.NonPublic);

        // Adds our cue to the game's cue bank (it's rebuilt with each session, so this runs before every use).
        public static void EnsureCue(float maxDistance)
        {
            var cues = Cues();
            if (cues == null || cues.ContainsKey(Cue))
                return;
            cues[Cue] = new MySoundData
            {
                SubtypeId = Cue.Hash,
                MaxDistance = maxDistance,
                VolumeCurve = MyCurveType.Linear,
                DisablePitchEffects = true,
                Waves = new List<VRage.MyAudioWave>(),
            };
        }

        public static void RemoveCue() => Cues()?.Remove(Cue);

        // Tags a streamed voice with our cue so the engine keeps positioning it.
        public static void Tag(IMySourceVoice voice)
        {
            if (voice != null && VoiceCue != null && VoiceCue.DeclaringType.IsInstanceOfType(voice))
                VoiceCue.SetValue(voice, Cue);
        }

        // The screen's centre in the block's model space, from the triangles of that screen's material; null if the
        // model isn't loaded or has no such mesh (then the sound comes from the block's centre). front is the
        // screen's facing (away from the block's centre), or zero.
        public static Vector3? LocalCentre(IMyTerminalBlock block, MyTextPanelComponent panel, out Vector3 front)
        {
            front = Vector3.Zero;
            try
            {
                var model = (block as MyCubeBlock)?.Model;
                string material = Material(panel);
                if (model == null || !model.LoadedData || material == null)
                    return null;
                var mesh = model.GetMeshList()?.FirstOrDefault(m => m.Material?.Name == material);
                if (mesh == null || mesh.TriCount == 0)
                    return null;
                Vector3 sum = Vector3.Zero;
                int count = 0;
                for (int t = mesh.TriStart; t < mesh.TriStart + mesh.TriCount; t++)
                {
                    var tri = model.GetTriangle(t);
                    Vector3 a = model.GetVertex(tri.I0), b = model.GetVertex(tri.I1), c = model.GetVertex(tri.I2);
                    sum += a + b + c;
                    count += 3;
                    front += Vector3.Cross(b - a, c - a);
                }
                Vector3 centre = sum / count;
                // Triangle winding varies between models; the screen faces away from the block.
                if (front.LengthSquared() > 0)
                    front = Vector3.Normalize(Vector3.Dot(front, centre) < 0 ? -front : front);
                return centre;
            }
            catch (Exception)
            {
                // Model data we can't read; fall back to the block's centre.
                return null;
            }
        }

        // The screen area's material name: its mesh in the block's model.
        public static string Material(MyTextPanelComponent panel)
        {
            if (!(PanelRender?.GetValue(panel) is object render) || !(ScreenAreas?.GetValue(render) is IList areas))
                return null;
            if (panel.Area < 0 || panel.Area >= areas.Count)
                return null;
            var area = areas[panel.Area];
            return area?.GetType().GetField("Material")?.GetValue(area) as string;
        }

        private static Dictionary<MyCueId, MySoundData> Cues()
        {
            object audio = MyAudio.Static;
            var bank = audio?.GetType().GetField("m_cueBank", BindingFlags.Instance | BindingFlags.NonPublic)?.GetValue(audio);
            return bank?.GetType().GetField("m_cues")?.GetValue(bank) as Dictionary<MyCueId, MySoundData>;
        }
    }
}
