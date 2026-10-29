using System;
using Sandbox.ModAPI;

namespace SEMcp.Core.Services
{
    internal sealed class SessionService
    {
        private readonly Dispatcher dispatcher;

        public SessionService(Dispatcher dispatcher)
        {
            this.dispatcher = dispatcher;
        }

        public void Register()
        {
            dispatcher.Register("ping", _ => Ping());
            dispatcher.Register("session.save", _ => Save());
        }

        // A normal save of the current world (like Ctrl+S), as a restore point before something risky. The game
        // keeps earlier versions in the world's Backup folder (MaxBackupSaves). Only the server can save.
        private object Save()
        {
            dispatcher.RequireSession();
            dispatcher.RequireWritable();
            if (MyAPIGateway.Multiplayer != null && !MyAPIGateway.Multiplayer.IsServer)
                throw new RpcException(ErrorKind.FeatureDisabled, "Only the host or server can save the world");
            var started = System.Diagnostics.Stopwatch.StartNew();
            bool saved = MyAPIGateway.Session.Save();
            dispatcher.Context.Log($"world saved: {saved}");
            return new
            {
                saved,
                world = MyAPIGateway.Session.Name,
                path = MyAPIGateway.Session.CurrentPath,
                backups = MyAPIGateway.Session.SessionSettings?.MaxBackupSaves,
                seconds = Math.Round(started.Elapsed.TotalSeconds, 1),
            };
        }

        // Works without a world loaded.
        private object Ping()
        {
            var context = dispatcher.Context;
            bool ready = context.IsSessionReady();
            var session = ready ? MyAPIGateway.Session : null;

            return new
            {
                host = context.HostVersion,
                generation = context.Generation,
                game = context.GameVersion,
                runtime = context.Runtime,
                sessionLoaded = ready,
                world = session?.Name,
                player = session?.Player?.DisplayName,
                onlineMode = session?.OnlineMode.ToString(),
                isServer = session?.IsServer,
            };
        }
    }
}
