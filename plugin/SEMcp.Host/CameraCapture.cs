using System;
using System.Collections.Concurrent;
using System.Reflection;
using System.Threading.Tasks;
using HarmonyLib;
using Sandbox.Game.World;
using VRage.Render.Image;
using VRageMath;
using VRageRender.Messages;

namespace SEMcp.Host
{
    // Renders the world from an arbitrary viewpoint (a camera block) into an offscreen texture and encodes it,
    // without touching what the player sees. Same approach as the CameraLCD plugin: a prefix on
    // MyRender11.DrawScene points the render camera somewhere else, draws the scene into a borrowed render
    // target, then puts the player's camera back before the normal frame draws.
    //
    // The renderer is internal, so everything goes through reflection. The patch is removed in Dispose, which
    // the host calls on stop, so hot reloads don't stack patches.
    internal sealed class CameraCapture : IDisposable
    {
        private const string HarmonyId = "SEMcp.CameraCapture";

        public sealed class Request
        {
            public MatrixD View;
            public Vector3D Position;
            public float FovRadians;
            public int Width, Height;
            public bool Png;
            public readonly TaskCompletionSource<byte[]> Done = new TaskCompletionSource<byte[]>(TaskCreationOptions.RunContinuationsAsynchronously);
        }

        private static CameraCapture active;

        private readonly Harmony harmony = new Harmony(HarmonyId);
        private readonly ConcurrentQueue<Request> requests = new ConcurrentQueue<Request>();
        private readonly Action<string> log;

        // Renderer internals.
        private readonly FieldInfo resolutionField;          // MyRender11.m_resolution (Vector2I)
        private readonly PropertyInfo viewportProperty;      // MyRender11.ViewportResolution
        private readonly MethodInfo setupCameraMatrices;     // MyRender11.SetupCameraMatrices(message)
        private readonly MethodInfo drawGameScene;           // MyRender11.DrawGameScene(IRtvBindable, out IBorrowedRtvTexture)
        private readonly FieldInfo texturePoolField;         // MyManagers.RwTexturesPool
        private readonly MethodInfo borrowRtv;               // BorrowRtv(name, w, h, Format, samples, quality)
        private readonly MethodInfo releaseTexture;          // IBorrowedTexture.Release()
        private readonly MethodInfo toData;                  // MyTextureData.ToData(IResource, byte[], FileFormat)

        public CameraCapture(Action<string> log)
        {
            this.log = log;
            const BindingFlags Static = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
            const BindingFlags Instance = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

            Type render = Need(AccessTools.TypeByName("VRageRender.MyRender11"), "MyRender11");
            resolutionField = Need(render.GetField("m_resolution", Static), "m_resolution");
            viewportProperty = Need(render.GetProperty("ViewportResolution", Static), "ViewportResolution");
            setupCameraMatrices = Need(render.GetMethod("SetupCameraMatrices", Static), "SetupCameraMatrices");
            drawGameScene = Need(render.GetMethod("DrawGameScene", Static), "DrawGameScene");

            Type managers = Need(AccessTools.TypeByName("VRage.Render11.Common.MyManagers"), "MyManagers");
            texturePoolField = Need(managers.GetField("RwTexturesPool", Static), "RwTexturesPool");
            Type pool = Need(AccessTools.TypeByName("VRage.Render11.Resources.MyBorrowedRwTextureManager"), "MyBorrowedRwTextureManager");
            borrowRtv = Need(AccessTools.Method(pool, "BorrowRtv", new[] { typeof(string), typeof(int), typeof(int), typeof(SharpDX.DXGI.Format), typeof(int), typeof(int) }), "BorrowRtv");
            Type borrowed = Need(AccessTools.TypeByName("VRage.Render11.Resources.Textures.MyBorrowedTexture"), "MyBorrowedTexture");
            releaseTexture = Need(borrowed.GetMethod("Release", Instance), "Release");
            Type textureData = Need(AccessTools.TypeByName("VRageRender.MyTextureData"), "MyTextureData");
            toData = Need(textureData.GetMethod("ToData", Static), "ToData");

            harmony.Patch(render.GetMethod("DrawScene", Static), prefix: new HarmonyMethod(typeof(CameraCapture).GetMethod(nameof(PrefixDrawScene), Static)));
            active = this;
        }

        private static T Need<T>(T member, string name) where T : class =>
            member ?? throw new InvalidOperationException($"Renderer member not found: {name} (game update?)");

        public Task<byte[]> Enqueue(Request request)
        {
            requests.Enqueue(request);
            return request.Done.Task;
        }

        public void Dispose()
        {
            active = null;
            harmony.UnpatchAll(HarmonyId);
            while (requests.TryDequeue(out var r))
                r.Done.TrySetException(new ObjectDisposedException(nameof(CameraCapture)));
        }

        // Render thread. Always lets the normal frame draw afterwards.
        private static void PrefixDrawScene()
        {
            var capture = active;
            if (capture != null && capture.requests.TryDequeue(out var request))
            {
                try
                {
                    request.Done.TrySetResult(capture.Render(request));
                }
                catch (Exception e)
                {
                    var inner = e is TargetInvocationException tie && tie.InnerException != null ? tie.InnerException : e;
                    capture.log($"camera capture failed: {inner}");
                    request.Done.TrySetException(inner);
                }
            }
        }

        private byte[] Render(Request request)
        {
            var player = MySector.MainCamera ?? throw new InvalidOperationException("No main camera");
            var oldResolution = (Vector2I)resolutionField.GetValue(null);
            var oldViewport = (Vector2I)viewportProperty.GetValue(null);
            var size = new Vector2I(request.Width, request.Height);
            object texture = null;
            object debugAo = null;
            try
            {
                resolutionField.SetValue(null, size);
                viewportProperty.SetValue(null, size);
                SetCamera(request.View, request.Position, request.FovRadians, player, lastMomentUpdateIndex: 1);

                texture = borrowRtv.Invoke(texturePoolField.GetValue(null),
                    new object[] { "SEMcpCamera", request.Width, request.Height, SharpDX.DXGI.Format.R8G8B8A8_UNorm_SRgb, 1, 0 });
                var args = new object[] { texture, null };
                drawGameScene.Invoke(null, args);
                debugAo = args[1];

                var format = request.Png ? MyImage.FileFormat.Png : MyImage.FileFormat.Jpg;
                var data = (byte[])toData.Invoke(null, new object[] { texture, null, format });
                if (data == null || data.Length == 0)
                    throw new InvalidOperationException("Reading the rendered image back failed (see the SE log)");
                return data;
            }
            finally
            {
                if (debugAo != null)
                    releaseTexture.Invoke(debugAo, null);
                if (texture != null)
                    releaseTexture.Invoke(texture, null);
                resolutionField.SetValue(null, oldResolution);
                viewportProperty.SetValue(null, oldViewport);
                SetCamera(player.ViewMatrix, player.Position, player.FieldOfView, player, lastMomentUpdateIndex: 0);
            }
        }

        private void SetCamera(MatrixD view, Vector3D position, float fov, VRage.Game.Utils.MyCamera player, int lastMomentUpdateIndex)
        {
            var message = new MyRenderMessageSetCameraViewMatrix
            {
                ViewMatrix = view,
                ProjectionMatrix = player.ProjectionMatrix,
                ProjectionFarMatrix = player.ProjectionMatrixFar,
                FOV = fov,
                FOVForSkybox = fov,
                NearPlane = player.NearPlaneDistance,
                FarPlane = player.FarPlaneDistance,
                FarFarPlane = player.FarFarPlaneDistance,
                CameraPosition = position,
                LastMomentUpdateIndex = lastMomentUpdateIndex,
                Smooth = false,
            };
            setupCameraMatrices.Invoke(null, new object[] { message });
        }
    }
}
