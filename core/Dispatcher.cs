using System;
using System.Collections.Generic;
using System.Text.Json.Nodes;
using SEMcp.Core.Services;

namespace SEMcp.Core
{
    // Routes RPC method names to handlers. Must be called on the game thread.
    // Handlers return any JSON-serializable object, or throw RpcException.
    public sealed class Dispatcher
    {
        private readonly Dictionary<string, Func<JsonObject, object>> handlers = new Dictionary<string, Func<JsonObject, object>>();

        public CoreContext Context { get; }

        public Dispatcher(CoreContext context)
        {
            Context = context;
            var access = new Access(this);
            new SessionService(this).Register();
            new GridService(this, access).Register();
            new BlockService(this, access).Register();
            new InventoryService(this, access).Register();
            new PlayerService(this, access).Register();
            new ControlService(this, access).Register();
            new ProductionService(this, access).Register();
            new ProgrammableBlockService(this, access).Register();
            new AutopilotService(this, access).Register();
            new SensingService(this, access).Register();
            new BuildService(this, access).Register();
            new ScreenService(this, access).Register();
            new WeaponService(this, access).Register();
        }

        public void Register(string method, Func<JsonObject, object> handler)
        {
            handlers[method] = handler;
        }

        public bool Has(string method) => handlers.ContainsKey(method);

        public object Dispatch(string method, JsonObject parameters)
        {
            if (!handlers.TryGetValue(method, out var handler))
                throw new RpcException(ErrorKind.UnknownMethod, $"Unknown method '{method}'");

            try
            {
                return handler(parameters ?? new JsonObject());
            }
            catch (RpcException)
            {
                throw;
            }
            catch (Exception e)
            {
                throw new RpcException(ErrorKind.GameError, $"{e.GetType().Name}: {e.Message}");
            }
        }

        public void RequireSession()
        {
            if (!Context.IsSessionReady())
                throw new RpcException(ErrorKind.NoSession, "No world is loaded");
        }

        public void RequireWritable()
        {
            if (Context.ReadOnly)
                throw new RpcException(ErrorKind.ReadOnly, "The plugin is in read-only mode (readOnly in config.json)");
        }
    }
}
