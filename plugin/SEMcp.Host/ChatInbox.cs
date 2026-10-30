using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using Sandbox.ModAPI;
using SEMcp.Core;
using VRage.Game.ModAPI;

namespace SEMcp.Host
{
    // Private in-game chat with the assistant: lines starting with the prefix (e.g. "/ai") never reach other players,
    // and wait here until the runner collects them with chat.poll.
    // The one game event the host subscribes to (the hot-reload rule in HostModule), so Stop() must unsubscribe.
    internal sealed class ChatInbox
    {
        // A runner that hasn't polled for this long is treated as gone, and messages are refused instead of queued.
        private const int ListenerGraceMs = 45000;
        private const int MaxWaitMs = 30000;
        private const int MaxQueued = 20;

        private readonly string prefix;
        private readonly Action<string> log;
        private readonly ConcurrentQueue<JsonObject> pending = new ConcurrentQueue<JsonObject>();
        private readonly SemaphoreSlim arrived = new SemaphoreSlim(0);
        private readonly Stopwatch sincePoll = new Stopwatch();
        private long nextId;
        private bool subscribed;

        public ChatInbox(string prefix, Action<string> log)
        {
            this.prefix = prefix.Trim();
            this.log = log;
        }

        public void Start()
        {
            MyAPIGateway.Utilities.MessageEntered += OnMessage;
            subscribed = true;
            log($"Chat: '{prefix} <message>' goes to the assistant");
        }

        public void Stop()
        {
            if (subscribed)
                MyAPIGateway.Utilities.MessageEntered -= OnMessage;
            subscribed = false;
            arrived.Release();
        }

        // Background thread. Waits up to waitMs for a message, then returns everything queued.
        public async Task<JsonNode> Poll(JsonObject p)
        {
            lock (sincePoll)
                sincePoll.Restart();
            int wait = (int)Math.Max(0, Math.Min((p ?? new JsonObject()).Number("waitMs", 0), MaxWaitMs));
            if (pending.IsEmpty && wait > 0)
                await arrived.WaitAsync(wait).ConfigureAwait(false);
            lock (sincePoll)
                sincePoll.Restart();

            var messages = new JsonArray();
            while (pending.TryDequeue(out var message))
                messages.Add(message);
            // Signals for messages taken now, so the next poll waits instead of waking to nothing.
            while (arrived.Wait(0))
            {
            }
            return new JsonObject { ["prefix"] = prefix, ["messages"] = messages };
        }

        private bool Listening
        {
            get
            {
                lock (sincePoll)
                    return sincePoll.IsRunning && sincePoll.ElapsedMilliseconds < ListenerGraceMs + MaxWaitMs;
            }
        }

        // Game thread (the chat screen).
        private void OnMessage(string text, ref bool sendToOthers)
        {
            if (text == null)
                return;
            string trimmed = text.Trim();
            bool ours = trimmed.Equals(prefix, StringComparison.OrdinalIgnoreCase)
                || trimmed.StartsWith(prefix + " ", StringComparison.OrdinalIgnoreCase);
            if (!ours)
                return;
            sendToOthers = false;

            try
            {
                Accept(trimmed.Substring(prefix.Length).Trim());
            }
            catch (Exception e)
            {
                log($"Chat message failed: {e}");
            }
        }

        private void Accept(string body)
        {
            var player = MyAPIGateway.Session?.Player;
            string name = player?.DisplayName ?? "You";
            // Keeping it private also hides it from the local chat, so echo it back.
            MyAPIGateway.Utilities.ShowMessage(name, $"{prefix} {body}".TrimEnd());

            var controlled = MyAPIGateway.Session?.ControlledObject;
            var seat = controlled as IMyCubeBlock;
            string persona = seat != null ? "ship" : "suit";
            string ai = persona == "ship" ? "Ship AI" : "Suit AI";

            if (body.Length == 0)
            {
                MyAPIGateway.Utilities.ShowMessage(ai, $"Type '{prefix}' and a message to talk to me. '{prefix} reset' starts a fresh conversation.");
                return;
            }
            if (!Listening)
            {
                MyAPIGateway.Utilities.ShowMessage(ai, "Nobody is listening: start the runner (uv run se-mcp-runner in SE-MCP/server).");
                return;
            }
            if (pending.Count >= MaxQueued)
            {
                MyAPIGateway.Utilities.ShowMessage(ai, "Still working through earlier messages; try again in a moment.");
                return;
            }

            pending.Enqueue(new JsonObject
            {
                ["id"] = Interlocked.Increment(ref nextId),
                ["text"] = body,
                ["player"] = name,
                ["persona"] = persona,
                ["ship"] = seat?.CubeGrid?.CustomName,
                ["shipId"] = seat?.CubeGrid?.EntityId.ToString(),
                ["at"] = DateTime.UtcNow.ToString("o"),
            });
            arrived.Release();
        }
    }
}
