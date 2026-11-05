using System;
using System.Collections.Generic;
using System.Linq;
using Sandbox.Game.Entities;
using Sandbox.Game.Entities.Blocks;
using Sandbox.ModAPI;
using VRageMath;

namespace SEScreens
{
    // A screen's surface in its block's model: the triangles of that screen's material, with their texture
    // coordinates. Turns a line in the world (the camera through the mouse) into a point on the screen's texture.
    internal sealed class ScreenGeometry
    {
        private readonly Vector3[] positions; // three per triangle, model space
        private readonly Vector2[] uvs;       // matching texture coordinates, 0..1 across the screen, v down

        private ScreenGeometry(Vector3[] positions, Vector2[] uvs)
        {
            this.positions = positions;
            this.uvs = uvs;
        }

        // Null if the model has no mesh for that screen or no texture coordinates.
        public static ScreenGeometry Load(IMyTerminalBlock block, MyTextPanelComponent panel)
        {
            try
            {
                var model = (block as MyCubeBlock)?.Model;
                string material = ScreenAudio.Material(panel);
                if (model == null || !model.LoadedData || material == null || !model.LoadTexCoordData() || model.TexCoords == null)
                    return null;
                var mesh = model.GetMeshList()?.FirstOrDefault(m => m.Material?.Name == material);
                if (mesh == null || mesh.TriCount == 0)
                    return null;
                var positions = new List<Vector3>();
                var uvs = new List<Vector2>();
                for (int t = mesh.TriStart; t < mesh.TriStart + mesh.TriCount; t++)
                {
                    var tri = model.GetTriangle(t);
                    foreach (int i in new[] { tri.I0, tri.I1, tri.I2 })
                    {
                        positions.Add(model.GetVertex(i));
                        uvs.Add(model.TexCoords[i].ToVector2());
                    }
                }
                Normalise(uvs);
                return new ScreenGeometry(positions.ToArray(), uvs.ToArray());
            }
            catch (Exception)
            {
                // Model data we can't read: the screen can't take the mouse.
                return null;
            }
        }

        // Screens map the whole texture over their mesh with UVs in 0..1. If a model's run outside that (tiled or
        // offset), stretch them over the screen instead.
        private static void Normalise(List<Vector2> uvs)
        {
            var min = new Vector2(uvs.Min(u => u.X), uvs.Min(u => u.Y));
            var max = new Vector2(uvs.Max(u => u.X), uvs.Max(u => u.Y));
            const float slack = 0.02f;
            if (min.X >= -slack && min.Y >= -slack && max.X <= 1 + slack && max.Y <= 1 + slack)
                return;
            var size = Vector2.Max(max - min, new Vector2(1e-6f));
            for (int i = 0; i < uvs.Count; i++)
                uvs[i] = (uvs[i] - min) / size;
        }

        // The nearest point where the world-space line crosses the screen: its distance along the line and its
        // texture coordinate (0..1, v down). Hits from behind the screen count too: the mouse only hits screens
        // the player can see.
        public bool Hit(MatrixD world, LineD line, out double distance, out Vector2 uv)
        {
            distance = double.MaxValue;
            uv = default;
            MatrixD toLocal = MatrixD.Invert(world);
            Vector3 from = Vector3D.Transform(line.From, toLocal);
            Vector3 dir = Vector3D.TransformNormal(line.Direction, toLocal);
            bool hit = false;
            for (int i = 0; i < positions.Length; i += 3)
            {
                if (!Intersect(from, dir, positions[i], positions[i + 1], positions[i + 2], out float t, out float b1, out float b2))
                    continue;
                if (t < 0 || t > line.Length || t >= distance)
                    continue;
                distance = t;
                uv = uvs[i] * (1 - b1 - b2) + uvs[i + 1] * b1 + uvs[i + 2] * b2;
                hit = true;
            }
            return hit;
        }

        // Möller–Trumbore: the line's parameter t and barycentric b1, b2 of the hit.
        private static bool Intersect(Vector3 from, Vector3 dir, Vector3 a, Vector3 b, Vector3 c, out float t, out float b1, out float b2)
        {
            t = b1 = b2 = 0;
            Vector3 e1 = b - a, e2 = c - a;
            Vector3 p = Vector3.Cross(dir, e2);
            float det = Vector3.Dot(e1, p);
            if (Math.Abs(det) < 1e-9f)
                return false;
            float inv = 1 / det;
            Vector3 s = from - a;
            b1 = Vector3.Dot(s, p) * inv;
            if (b1 < 0 || b1 > 1)
                return false;
            Vector3 q = Vector3.Cross(s, e1);
            b2 = Vector3.Dot(dir, q) * inv;
            if (b2 < 0 || b1 + b2 > 1)
                return false;
            t = Vector3.Dot(e2, q) * inv;
            return true;
        }
    }
}
