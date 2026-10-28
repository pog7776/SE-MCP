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
