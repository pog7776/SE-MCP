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
            new SessionService(this).Register();
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
    }
}
