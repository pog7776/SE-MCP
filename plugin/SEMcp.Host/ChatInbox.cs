using System;
using Sandbox.ModAPI;
using VRage.Game.ModAPI;

namespace SEMcp.Host
{
    // Private in-game chat with the assistant: lines starting with the prefix (e.g. "/ai") never reach other players,
    // and become chat events on the hub for the runner to answer.
    // One of the few game hooks the host makes (the hot-reload rule in HostModule), so Stop() must unsubscribe.
    internal sealed class ChatInbox
    {
        private readonly string prefix;
        private readonly EventHub hub;
        private readonly Action<string> log;
        private bool subscribed;

        public ChatInbox(string prefix, EventHub hub, Action<string> log)
        {
            this.prefix = prefix.Trim();
            this.hub = hub;
            this.log = log;
        }

        // MyAPIGateway.Utilities is only set once a world loads, so a host started at game launch would find it null.
        // It's always this singleton, which exists from the start and keeps its subscribers across worlds.
        private static IMyUtilities Utilities => MyAPIUtilities.Static;

        public void Start()
        {
            Utilities.MessageEntered += OnMessage;
            subscribed = true;
            log($"Chat: '{prefix} <message>' goes to the assistant");
        }

        public void Stop()
        {
            if (subscribed)
                Utilities.MessageEntered -= OnMessage;
            subscribed = false;
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
                string body = trimmed.Substring(prefix.Length).Trim();
                // Keeping it private also hides it from the local chat, so echo it back.
                MyAPIGateway.Utilities.ShowMessage(MyAPIGateway.Session?.Player?.DisplayName ?? "You", $"{prefix} {body}".TrimEnd());
                string refusal = Submit(hub, body, "chat", prefix);
                if (refusal != null)
                    MyAPIGateway.Utilities.ShowMessage(AiName(), refusal);
            }
            catch (Exception e)
            {
                log($"Chat message failed: {e}");
            }
        }

        public static string AiName() => (string)EventHub.Context()["persona"] == "ship" ? "Ship AI" : "Suit AI";

        // Game thread. Queues a player's message as a chat event; returns what to tell the player instead, if anything.
        public static string Submit(EventHub hub, string body, string source, string prefix)
        {
            if (body.Length == 0)
                return $"Type '{prefix}' and a message to talk to me. '{prefix} reset' starts a fresh conversation.";
            if (!hub.Listening)
                return "Nobody is listening: start the runner (uv run se-mcp-runner in SE-MCP/server).";
            if (hub.Publish("chat", source, body) == null)
                return "Still working through earlier messages; try again in a moment.";
            return null;
        }
    }
}
