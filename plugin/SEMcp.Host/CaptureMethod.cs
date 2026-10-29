using System;
using System.Reflection;
using System.Text.Json.Nodes;
using System.Threading.Tasks;
using Sandbox.Game.Entities;
using Sandbox.Game.World;
using Sandbox.ModAPI;
using SEMcp.Core;
using VRageMath;

namespace SEMcp.Host
{
    // camera.capture: an image from a camera block, from an arbitrary viewpoint, or from the player's own view
    // (without the HUD). The viewpoint is worked out on the game thread, then the render thread draws it.
    internal static class CaptureMethod
    {
        private static readonly FieldInfo CameraFov =
            typeof(MyCameraBlock).GetField("m_fov", BindingFlags.Instance | BindingFlags.NonPublic);

        private const int MaxSide = 1920;

        public static async Task<JsonNode> Run(JsonObject p, Dispatcher dispatcher, GameThreadQueue queue, CameraCapture capture, bool allowed)
        {
            if (!allowed)
                throw new RpcException(ErrorKind.FeatureDisabled, "Image capture is turned off (allowScreenshots in config.json)");
            if (capture == null)
                throw new RpcException(ErrorKind.GameError, "Offscreen rendering isn't available (the renderer hook failed to install; see the SE log)");

            var request = await queue.Run(() => Prepare(p, dispatcher));
            var finished = await Task.WhenAny(capture.Enqueue(request.Request), Task.Delay(TimeSpan.FromSeconds(8)));
            if (finished != request.Request.Done.Task)
            {
                request.Request.Done.TrySetCanceled();
                throw new RpcException(ErrorKind.Timeout, "The game didn't render a frame in time (minimized or paused on a loading screen?)");
            }

            byte[] data = await request.Request.Done.Task;
            return new JsonObject
            {
                ["source"] = request.Source,
                ["format"] = request.Request.Png ? "png" : "jpeg",
                ["width"] = request.Request.Width,
                ["height"] = request.Request.Height,
                ["fovDegrees"] = Math.Round(MathHelper.ToDegrees(request.Request.FovRadians), 1),
                ["position"] = new JsonArray(Math.Round(request.Request.Position.X, 2), Math.Round(request.Request.Position.Y, 2), Math.Round(request.Request.Position.Z, 2)),
                ["image"] = Convert.ToBase64String(data),
            };
        }

        private sealed class Prepared
        {
            public CameraCapture.Request Request;
            public string Source;
        }

        // Game thread.
        private static Prepared Prepare(JsonObject p, Dispatcher dispatcher)
        {
            dispatcher.RequireSession();
            var access = new Access(dispatcher);
            var player = MySector.MainCamera ?? throw new RpcException(ErrorKind.NoSession, "No camera");

            int width = (int)MathHelper.Clamp(p.Number("width", 1280), 64, MaxSide);
            int height = (int)MathHelper.Clamp(p.Number("height", Math.Round(width * 9.0 / 16)), 64, MaxSide);
            var request = new CameraCapture.Request { Width = width, Height = height, Png = p.String("format") == "png" };
            string source;

            if (p["blockId"] != null)
            {
                var block = access.Block(p.Id("blockId"));
                if (!(block is MyCameraBlock camera))
                    throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' is not a camera block");
                if (!camera.IsWorking)
                    throw new RpcException(ErrorKind.InvalidParams, $"'{camera.CustomName}' is off, unpowered or damaged");
                request.View = camera.GetViewMatrix();
                request.Position = camera.WorldMatrix.Translation;
                request.FovRadians = CameraFov?.GetValue(camera) is float fov && fov > 0 ? fov : player.FieldOfView;
                source = camera.CustomName.ToString();
            }
            else if (p["position"] is JsonArray pos && p["forward"] is JsonArray fwd)
            {
                // Free viewpoint, e.g. "look at the ship from 50 m off its bow". Limited to the access range.
                Vector3D position = ToVector(pos), forward = Vector3D.Normalize(ToVector(fwd));
                Vector3D up = p["up"] is JsonArray u ? ToVector(u) : Vector3D.Up;
                if (Math.Abs(Vector3D.Dot(Vector3D.Normalize(up), forward)) > 0.99)
                    up = Vector3D.CalculatePerpendicularVector(forward);
                if (Vector3D.Distance(position, access.Origin) > dispatcher.Context.MaxRangeMeters)
                    throw new RpcException(ErrorKind.OutOfRange, "That viewpoint is beyond maxRangeMeters from the player");
                request.View = MatrixD.CreateLookAt(position, position + forward, up);
                request.Position = position;
                request.FovRadians = MathHelper.ToRadians((float)MathHelper.Clamp(p.Number("fovDegrees", 70), 5, 120));
                source = "viewpoint";
            }
            else
            {
                // The player's own view, rendered without HUD or GUI.
                request.View = player.ViewMatrix;
                request.Position = player.Position;
                request.FovRadians = player.FieldOfView;
                source = "player view";
            }

            if (p["fovDegrees"] != null)
                request.FovRadians = MathHelper.ToRadians((float)MathHelper.Clamp(p.Number("fovDegrees", 70), 5, 120));
            dispatcher.Context.Log($"camera capture from {source} ({width}x{height})");
            return new Prepared { Request = request, Source = source };
        }

        private static Vector3D ToVector(JsonArray a) =>
            new Vector3D(a[0].GetValue<double>(), a[1].GetValue<double>(), a[2].GetValue<double>());
    }
}
