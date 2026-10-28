using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;

namespace SEMcp.Host
{
    // Newline-delimited JSON-RPC 2.0 over TCP on 127.0.0.1. One client at a time;
    // a new connection replaces the old one. The first message must be auth.
    public sealed class TcpServer
    {
        private readonly int port;
        private readonly string token;
        private readonly Func<string, JsonObject, Task<JsonNode>> handle;
        private readonly Action<string> log;
        private readonly CancellationTokenSource stopping = new CancellationTokenSource();

        private TcpListener listener;
        private TcpClient client;

        public TcpServer(int port, string token, Func<string, JsonObject, Task<JsonNode>> handle, Action<string> log)
        {
            this.port = port;
            this.token = token;
            this.handle = handle;
            this.log = log;
        }

        public void Start()
        {
            listener = new TcpListener(IPAddress.Loopback, port);
            listener.Start();
            log($"Listening on 127.0.0.1:{port}");
            Task.Run(AcceptLoop);
        }

        public void Stop()
        {
            stopping.Cancel();
            try { listener?.Stop(); } catch { }
            Interlocked.Exchange(ref client, null)?.Close();
        }

        private async Task AcceptLoop()
        {
            while (!stopping.IsCancellationRequested)
            {
                TcpClient accepted;
                try
                {
                    accepted = await listener.AcceptTcpClientAsync().ConfigureAwait(false);
                }
                catch (Exception) when (stopping.IsCancellationRequested)
                {
                    return;
                }
                catch (Exception e)
                {
                    log($"Accept failed: {e.Message}");
                    continue;
                }

                accepted.NoDelay = true;
                Interlocked.Exchange(ref client, accepted)?.Close();
                _ = Task.Run(() => Serve(accepted));
            }
        }

        private async Task Serve(TcpClient connection)
        {
            var writeLock = new SemaphoreSlim(1, 1);
            try
            {
                var stream = connection.GetStream();
                var reader = new StreamReader(stream, new UTF8Encoding(false));
                var writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true, NewLine = "\n" };

                async Task Send(JsonNode message)
                {
                    await writeLock.WaitAsync().ConfigureAwait(false);
                    try { await writer.WriteLineAsync(message.ToJsonString()).ConfigureAwait(false); }
                    finally { writeLock.Release(); }
                }

                if (!await Authenticate(reader, Send).ConfigureAwait(false))
                    return;
                log("Client connected");

                string line;
                while ((line = await reader.ReadLineAsync().ConfigureAwait(false)) != null)
                {
                    if (line.Length == 0)
                        continue;
                    // Requests run concurrently; responses carry their id.
                    string request = line;
                    _ = Task.Run(async () => await Send(await HandleLine(request).ConfigureAwait(false)).ConfigureAwait(false));
                }
            }
            catch (Exception) when (stopping.IsCancellationRequested)
            {
            }
            catch (IOException)
            {
            }
            catch (ObjectDisposedException)
            {
            }
            catch (Exception e)
            {
                log($"Connection failed: {e}");
            }
            finally
            {
                connection.Close();
                if (Interlocked.CompareExchange(ref client, null, connection) == connection)
                    log("Client disconnected");
            }
        }

        private async Task<bool> Authenticate(StreamReader reader, Func<JsonNode, Task> send)
        {
            string line = await reader.ReadLineAsync().ConfigureAwait(false);
            JsonNode id = null;
            try
            {
                var message = JsonNode.Parse(line ?? "")?.AsObject();
                id = message?["id"]?.DeepClone();
                string given = message?["params"]?["token"]?.GetValue<string>();
                if (message?["method"]?.GetValue<string>() == "auth" && TokenEquals(given))
                {
                    await send(Rpc.Result(id, new JsonObject { ["ok"] = true })).ConfigureAwait(false);
                    return true;
                }
            }
            catch (Exception)
            {
            }

            log("Rejected a client that failed auth");
            await send(Rpc.Error(id, "access_denied", "First message must be auth with a valid token")).ConfigureAwait(false);
            return false;
        }

        private bool TokenEquals(string given)
        {
            if (given == null)
                return false;
            byte[] a = Encoding.UTF8.GetBytes(given);
            byte[] b = Encoding.UTF8.GetBytes(token);
            using (var sha = SHA256.Create())
            {
                // Compare fixed-length hashes in constant time.
                byte[] ha = sha.ComputeHash(a), hb = sha.ComputeHash(b);
                int diff = 0;
                for (int i = 0; i < ha.Length; i++)
                    diff |= ha[i] ^ hb[i];
                return diff == 0;
            }
        }

        private async Task<JsonNode> HandleLine(string line)
        {
            JsonNode id = null;
            try
            {
                var message = JsonNode.Parse(line).AsObject();
                id = message["id"]?.DeepClone();
                string method = message["method"]?.GetValue<string>();
                if (string.IsNullOrEmpty(method))
                    return Rpc.Error(id, "invalid_params", "Missing method");

                var parameters = message["params"] as JsonObject;
                JsonNode result = await handle(method, (JsonObject)parameters?.DeepClone()).ConfigureAwait(false);
                return Rpc.Result(id, result);
            }
            catch (Exception e)
            {
                return Rpc.FromException(id, e);
            }
        }
    }
}
