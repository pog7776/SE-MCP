using System;
using System.IO;
using System.Security.Cryptography;
using System.Text.Json;

namespace SEMcp.Host
{
    // %AppData%\SpaceEngineers\SEMcp\config.json, created with a random token on first run.
    // The Python server reads the same file to get the token.
    public sealed class Config
    {
        public int Port { get; set; } = 8765;
        public string Token { get; set; }
        public bool ReadOnly { get; set; }
        public bool AllowPbCodePush { get; set; } = true;
        public bool AllowAutopilot { get; set; } = true;
        public bool AllowScreenshots { get; set; } = true;
        // Desktop windows shown on LCDs, on this PC only.
        public bool AllowWindowMirror { get; set; } = true;
        // Off by default: firing weapons needs an explicit opt-in.
        public bool AllowWeapons { get; set; }
        public double MaxRangeMeters { get; set; } = 50000;
        // Chat lines starting with this prefix go privately to the assistant (see ChatInbox); empty turns it off.
        public string ChatPrefix { get; set; } = "/ai";

        private static readonly JsonSerializerOptions JsonOptions = new JsonSerializerOptions
        {
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
            WriteIndented = true,
        };

        public static string FilePath
        {
            get
            {
                string appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
                return Path.Combine(appData, "SpaceEngineers", "SEMcp", "config.json");
            }
        }

        public static Config LoadOrCreate()
        {
            string path = FilePath;
            Config config = File.Exists(path)
                ? JsonSerializer.Deserialize<Config>(File.ReadAllText(path), JsonOptions) ?? new Config()
                : new Config();

            // Also fills in options added since the file was written.
            if (string.IsNullOrEmpty(config.Token))
                config.Token = NewToken();
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, JsonSerializer.Serialize(config, JsonOptions));
            return config;
        }

        private static string NewToken()
        {
            var bytes = new byte[24];
            using (var rng = RandomNumberGenerator.Create())
                rng.GetBytes(bytes);
            return Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        }
    }
}
