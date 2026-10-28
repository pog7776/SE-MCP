using System;
using System.Text.Json;
using System.Text.Json.Nodes;
using SEMcp.Core;

namespace SEMcp.Host
{
    // JSON-RPC 2.0 envelopes. All errors use code -32000 with the kind in error.data.kind.
    public static class Rpc
    {
        private static readonly JsonSerializerOptions JsonOptions = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        };

        public static JsonNode ToNode(object value) =>
            value as JsonNode ?? JsonSerializer.SerializeToNode(value, JsonOptions);

        public static JsonObject Result(JsonNode id, JsonNode result) => new JsonObject
        {
            ["jsonrpc"] = "2.0",
            ["id"] = id,
            ["result"] = result,
        };

        public static JsonObject Error(JsonNode id, string kind, string message) => new JsonObject
        {
            ["jsonrpc"] = "2.0",
            ["id"] = id,
            ["error"] = new JsonObject
            {
                ["code"] = -32000,
                ["message"] = message,
                ["data"] = new JsonObject { ["kind"] = kind },
            },
        };

        public static JsonObject FromException(JsonNode id, Exception e)
        {
            if (e is AggregateException aggregate && aggregate.InnerException != null)
                e = aggregate.InnerException;

            switch (e)
            {
                case RpcException rpc:
                    return Error(id, rpc.Kind, rpc.Message);
                case JsonException _:
                case InvalidOperationException _ when e.Message.Contains("JsonNode"):
                    return Error(id, ErrorKind.InvalidParams, e.Message);
                default:
                    return Error(id, ErrorKind.GameError, $"{e.GetType().Name}: {e.Message}");
            }
        }
    }
}
