using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;

namespace SEMcp.Loader
{
    // Loads SEMcp.Core + SEMcp.Host from bytes so new builds can be swapped in while the game runs.
    // Old generations stay in memory (no unloading), which is fine for development.
    internal sealed class HostLoader : ILoaderContext, IDisposable
    {
        private static readonly string[] AssemblyNames = { "SEMcp.Core", "SEMcp.Host" };
        private static readonly TimeSpan Debounce = TimeSpan.FromSeconds(1.5);

        private readonly string hostDir;
        private readonly FileSystemWatcher watcher;
        private readonly Dictionary<string, Assembly> current = new Dictionary<string, Assembly>(StringComparer.OrdinalIgnoreCase);

        private IHostModule host;
        private int reloadRequested = 1; // load on the first Update
        private long lastChangeTicks;

        public int Generation { get; private set; }
        public string LoaderVersion { get; } = typeof(HostLoader).Assembly.GetName().Version.ToString();

        public HostLoader(string hostDir)
        {
            this.hostDir = hostDir;
            AppDomain.CurrentDomain.AssemblyResolve += OnAssemblyResolve;

            Directory.CreateDirectory(hostDir);
            watcher = new FileSystemWatcher(hostDir, "*.dll")
            {
                NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.FileName | NotifyFilters.Size,
            };
            watcher.Changed += OnFileChanged;
            watcher.Created += OnFileChanged;
            watcher.Renamed += OnFileChanged;
            watcher.EnableRaisingEvents = true;
        }

        public void Log(string message) => Plugin.Log(message);

        public void RequestReload() => Interlocked.Exchange(ref reloadRequested, 1);

        private void OnFileChanged(object sender, FileSystemEventArgs e)
        {
            Interlocked.Exchange(ref lastChangeTicks, DateTime.UtcNow.Ticks);
        }

        public void Update()
        {
            long changed = Interlocked.Read(ref lastChangeTicks);
            if (changed != 0 && DateTime.UtcNow.Ticks - changed > Debounce.Ticks)
            {
                Interlocked.Exchange(ref lastChangeTicks, 0);
                Log("Host files changed, reloading");
                RequestReload();
            }

            if (Interlocked.Exchange(ref reloadRequested, 0) == 1)
                Reload();

            if (host == null)
                return;

            try
            {
                host.Update();
            }
            catch (Exception e)
            {
                Log($"Host Update failed, stopping host until the next reload: {e}");
                StopHost();
            }
        }

        private void Reload()
        {
            StopHost();

            try
            {
                var loaded = new Dictionary<string, Assembly>(StringComparer.OrdinalIgnoreCase);
                foreach (string name in AssemblyNames)
                {
                    string dll = Path.Combine(hostDir, name + ".dll");
                    if (!File.Exists(dll))
                    {
                        Log($"Host not found: {dll} (build the solution in Release to deploy it)");
                        return;
                    }

                    string pdb = Path.ChangeExtension(dll, ".pdb");
                    byte[] dllBytes = ReadShared(dll);
                    byte[] pdbBytes = File.Exists(pdb) ? ReadShared(pdb) : null;
                    loaded[name] = pdbBytes != null ? Assembly.Load(dllBytes, pdbBytes) : Assembly.Load(dllBytes);
                }

                lock (current)
                {
                    current.Clear();
                    foreach (var pair in loaded)
                        current[pair.Key] = pair.Value;
                }

                Type hostType = loaded["SEMcp.Host"].GetTypes()
                    .Single(t => typeof(IHostModule).IsAssignableFrom(t) && !t.IsAbstract);

                Generation++;
                host = (IHostModule)Activator.CreateInstance(hostType);
                host.Start(this);
                Log($"Host generation {Generation} started ({loaded["SEMcp.Host"].GetName().Version})");
            }
            catch (Exception e)
            {
                Log($"Host load failed: {e}");
                host = null;
            }
        }

        private void StopHost()
        {
            if (host == null)
                return;

            try
            {
                host.Stop();
            }
            catch (Exception e)
            {
                Log($"Host Stop failed: {e}");
            }
            host = null;
        }

        // The build may still be writing; read without blocking it and retry briefly.
        private static byte[] ReadShared(string path)
        {
            for (int attempt = 0; ; attempt++)
            {
                try
                {
                    using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                    using (var memory = new MemoryStream())
                    {
                        stream.CopyTo(memory);
                        return memory.ToArray();
                    }
                }
                catch (IOException) when (attempt < 10)
                {
                    Thread.Sleep(100);
                }
            }
        }

        // Byte-loaded assemblies can't be found by probing, so resolve them (and the loader itself) here.
        private Assembly OnAssemblyResolve(object sender, ResolveEventArgs args)
        {
            string name = new AssemblyName(args.Name).Name;
            if (name == typeof(HostLoader).Assembly.GetName().Name)
                return typeof(HostLoader).Assembly;

            lock (current)
                return current.TryGetValue(name, out Assembly assembly) ? assembly : null;
        }

        public void Dispose()
        {
            StopHost();
            watcher.Dispose();
            AppDomain.CurrentDomain.AssemblyResolve -= OnAssemblyResolve;
        }
    }
}
