using System.Text.Json.Nodes;
using VRageMath;

namespace SEMcp.Core
{
    // "#RRGGBB", "#RRGGBBAA" or {r, g, b, a?}: the color format every method takes. Public so the host can use it.
    public static class ColorParam
    {
        public static Color Parse(JsonNode value) => Services.ControlService.ToColor(value);
    }
}
