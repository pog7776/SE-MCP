using System;
using System.Text;
using Sandbox.ModAPI;

namespace SEScreens
{
    // Per-screen settings of our apps, in the block's CustomData under an [SEScreens] section (MyIni style, so PB
    // scripts that keep their own sections there are left alone):
    //
    //   [SEScreens]
    //   home1=https://example.com
    //
    // The number is the screen (surface) index; a plain "home" applies to every screen of the block. The world
    // saves it and it syncs like any CustomData, so the world stays the one record of what its screens show.
    internal static class ScreenSettings
    {
        public const string Section = "SEScreens";

        // The value for this screen ("key" + index, else "key"), or null.
        public static string Get(IMyTerminalBlock block, string key, int surface)
        {
            string own = null, shared = null;
            foreach (var (k, v) in Entries(block.CustomData))
            {
                if (k.Equals(key + surface, StringComparison.OrdinalIgnoreCase))
                    own = v;
                else if (k.Equals(key, StringComparison.OrdinalIgnoreCase))
                    shared = v;
            }
            return own ?? shared;
        }

        // Adds "key<surface>=value" to the section (creating it at the end). Existing entries are left as they are.
        public static void Add(IMyTerminalBlock block, string key, int surface, string value)
        {
            string data = block.CustomData ?? "";
            string line = $"{key}{surface}={value}";
            var lines = data.Replace("\r\n", "\n").Split('\n');
            int header = Array.FindIndex(lines, l => IsHeader(l, out string name) && name.Equals(Section, StringComparison.OrdinalIgnoreCase));
            var text = new StringBuilder();
            if (header < 0)
            {
                text.Append(data.TrimEnd());
                if (text.Length > 0)
                    text.Append("\n\n");
                text.Append('[').Append(Section).Append("]\n").Append(line).Append('\n');
            }
            else
            {
                for (int i = 0; i < lines.Length; i++)
                {
                    if (i > 0)
                        text.Append('\n');
                    text.Append(lines[i]);
                    if (i == header)
                        text.Append('\n').Append(line);
                }
            }
            block.CustomData = text.ToString();
        }

        private static System.Collections.Generic.IEnumerable<(string, string)> Entries(string data)
        {
            if (string.IsNullOrEmpty(data))
                yield break;
            bool inSection = false;
            foreach (var raw in data.Split('\n'))
            {
                string l = raw.Trim();
                if (IsHeader(l, out string name))
                {
                    inSection = name.Equals(Section, StringComparison.OrdinalIgnoreCase);
                    continue;
                }
                int eq = l.IndexOf('=');
                if (!inSection || eq <= 0 || l.StartsWith(";"))
                    continue;
                yield return (l.Substring(0, eq).Trim(), l.Substring(eq + 1).Trim());
            }
        }

        private static bool IsHeader(string line, out string name)
        {
            line = line.Trim();
            bool header = line.Length > 2 && line[0] == '[' && line[line.Length - 1] == ']';
            name = header ? line.Substring(1, line.Length - 2).Trim() : null;
            return header;
        }
    }
}
