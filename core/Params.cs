using System.Text.Json;
using System.Text.Json.Nodes;

namespace SEMcp.Core
{
    // Typed access to request params, with invalid_params errors that name the field.
    public static class Params
    {
        public static string String(this JsonObject p, string name, bool required = false)
        {
            var node = p[name];
            if (node == null)
            {
                if (required)
                    throw new RpcException(ErrorKind.InvalidParams, $"Missing '{name}'");
                return null;
            }
            // Ids may arrive as numbers or strings.
            return node.GetValueKind() == JsonValueKind.String ? node.GetValue<string>() : node.ToJsonString();
        }

        public static bool Bool(this JsonObject p, string name, bool fallback)
        {
            var node = p[name];
            if (node == null)
                return fallback;
            if (node.GetValueKind() == JsonValueKind.True || node.GetValueKind() == JsonValueKind.False)
                return node.GetValue<bool>();
            throw new RpcException(ErrorKind.InvalidParams, $"'{name}' must be true or false");
        }

        public static double Number(this JsonObject p, string name, double fallback)
        {
            var node = p[name];
            if (node == null)
                return fallback;
            if (node.GetValueKind() == JsonValueKind.Number)
                return node.GetValue<double>();
            throw new RpcException(ErrorKind.InvalidParams, $"'{name}' must be a number");
        }

        public static long Id(this JsonObject p, string name)
        {
            string text = p.String(name, required: true);
            if (!long.TryParse(text, out long id))
                throw new RpcException(ErrorKind.InvalidParams, $"'{name}' must be an entity id");
            return id;
        }
    }
}
