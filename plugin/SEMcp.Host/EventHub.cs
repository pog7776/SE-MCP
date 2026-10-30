using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Text.Json.Nodes;
using System.Threading;
using System.Threading.Tasks;
using Sandbox.ModAPI;
using SEMcp.Core;
using VRage.Game.ModAPI;

namespace SEMcp.Host
{
    // Everything that wants the assistant's attention goes through here as one event shape:
    // {id, type, source, at, text, context: {player, persona, ship, shipId}, data}.
    // Chat lines (ChatInbox, CommsChannel) and watches publish; the runner collects them with events.poll.
    internal sealed class EventHub
    {
        // A runner that hasn't polled for this long is treated as gone.
        private const int ListenerGraceMs = 45000;
        private const int MaxWaitMs = 30000;
        private const int MaxQueued = 50;
        private const int RecentKept = 50;

        private readonly ConcurrentQueue<JsonObject> pending = new ConcurrentQueue<JsonObject>();
        private readonly SemaphoreSlim arrived = new SemaphoreSlim(0);
        private readonly Stopwatch sincePoll = new Stopwatch();
        private readonly LinkedList<JsonObject> recent = new LinkedList<JsonObject>();
        private long nextId;
        private volatile bool closed;

        public bool Listening
        {
            get
            {
                lock (sincePoll)
                    return sincePoll.IsRunning && sincePoll.ElapsedMilliseconds < ListenerGraceMs + MaxWaitMs;
            }
        }

        public int Queued => pending.Count;

        // Game thread (it reads who the player is and what they sit in). Returns the event, or null when it was
        // dropped because the queue is full.
        public JsonObject Publish(string type, string source, string text, JsonObject data = null)
        {
            var e = new JsonObject
            {
                ["id"] = Interlocked.Increment(ref nextId),
                ["type"] = type,
                ["source"] = source,
                ["at"] = DateTime.UtcNow.ToString("o"),
                ["text"] = text,
                ["context"] = Context(),
                ["data"] = data ?? new JsonObject(),
            };
            lock (recent)
            {
                recent.AddLast(e);
                while (recent.Count > RecentKept)
                    recent.RemoveFirst();
            }
            if (pending.Count >= MaxQueued)
                return null;
            pending.Enqueue((JsonObject)e.DeepClone());
            arrived.Release();
            return e;
        }

        public static JsonObject Context()
        {
            var seat = MyAPIGateway.Session?.ControlledObject as IMyCubeBlock;
            return new JsonObject
            {
                ["player"] = MyAPIGateway.Session?.Player?.DisplayName,
                ["persona"] = seat != null ? "ship" : "suit",
                ["ship"] = seat?.CubeGrid?.CustomName,
                ["shipId"] = seat?.CubeGrid?.EntityId.ToString(),
                // Creative players can't suffocate or run dry, so survival warnings mean little there.
                ["creative"] = MyAPIGateway.Session?.CreativeMode == true,
            };
        }

        // Background thread. Waits up to waitMs for an event, then returns everything queued (optionally only some types).
        public async Task<JsonNode> Poll(JsonObject p)
        {
            p = p ?? new JsonObject();
            lock (sincePoll)
                sincePoll.Restart();
            int wait = (int)Math.Max(0, Math.Min(p.Number("waitMs", 0), MaxWaitMs));
            if (pending.IsEmpty && wait > 0 && !closed)
                await arrived.WaitAsync(wait).ConfigureAwait(false);
            lock (sincePoll)
                sincePoll.Restart();

            var events = new JsonArray();
            while (pending.TryDequeue(out var e))
                events.Add(e);
            // Signals for events taken now, so the next poll waits instead of waking to nothing.
            while (arrived.Wait(0))
            {
            }
            return new JsonObject { ["events"] = events };
        }

        // The last events, delivered or not, newest last. Any thread.
        public JsonNode Recent(JsonObject p)
        {
            int limit = (int)Math.Max(1, Math.Min((p ?? new JsonObject()).Number("limit", 20), RecentKept));
            string type = p?.String("type");
            lock (recent)
            {
                var list = recent.Where(e => type == null || (string)e["type"] == type).ToList();
                return new JsonObject { ["events"] = new JsonArray(list.Skip(Math.Max(0, list.Count - limit)).Select(e => e.DeepClone()).ToArray()) };
            }
        }

        public void Close()
        {
            closed = true;
            arrived.Release();
        }
    }
}
