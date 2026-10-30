using System;
using System.Reflection;
using System.Text;
using HarmonyLib;
using Sandbox.Game.Gui;
using Sandbox.Game.GameSystems.Chat;
using Sandbox.Game.World;
using Sandbox.Graphics.GUI;

namespace SEMcp.Host
{
    // A "Suit AI" conversation in the terminal's Comms tab. The game already lists a bot conversation there
    // (ChatChannel.ChatBot) that nothing answers in this version, so we take it over:
    // - its list entry is renamed,
    // - what the player sends in it becomes a chat event (source "comms") instead of going nowhere,
    // - replies (comms.post) are added to its history.
    // Harmony patches, removed in Dispose() so hot reload stays safe.
    internal sealed class CommsChannel : IDisposable
    {
        private const string HarmonyId = "SEMcp.CommsChannel";
        private const BindingFlags Instance = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;
        private const BindingFlags Static = BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public;

        // Internal to the game, so only reachable by name.
        private static readonly Type ControllerType = typeof(MySession).Assembly.GetType("Sandbox.Game.Gui.MyTerminalChatController");

        private static readonly FieldInfo BotItemField = ControllerType.GetField("m_chatBotItem", Instance);
        private static readonly FieldInfo PlayerListField = ControllerType.GetField("m_playerList", Instance);
        private static readonly FieldInfo ChatboxField = ControllerType.GetField("m_chatbox", Instance);
        private static readonly FieldInfo ClosedField = ControllerType.GetField("m_closed", Instance);
        private static readonly MethodInfo RefreshHistory = ControllerType.GetMethod("RefreshChatBotHistory", Instance);

        // Statics because Harmony patches are static; set by the live host only.
        private static EventHub hub;
        private static string prefix;
        private static WeakReference<object> open;

        private readonly Harmony harmony = new Harmony(HarmonyId);

        public CommsChannel(EventHub eventHub, string chatPrefix)
        {
            if (BotItemField == null || PlayerListField == null || ChatboxField == null || RefreshHistory == null)
                throw new MissingMemberException("MyTerminalChatController changed; the Comms tab Suit AI is off");
            hub = eventHub;
            prefix = chatPrefix;
            var controller = ControllerType;
            harmony.Patch(controller.GetMethod("RefreshPlayerList", Instance),
                postfix: new HarmonyMethod(typeof(CommsChannel).GetMethod(nameof(PostfixRefreshPlayerList), Static)));
            harmony.Patch(controller.GetMethod("SendMessage", Instance),
                prefix: new HarmonyMethod(typeof(CommsChannel).GetMethod(nameof(PrefixSendMessage), Static)));
        }

        public void Dispose()
        {
            harmony.UnpatchAll(HarmonyId);
            hub = null;
            open = null;
        }

        // The name in the Comms list and as the author of replies.
        public static string Name => ChatInbox.AiName();

        private static void PostfixRefreshPlayerList(object __instance)
        {
            open = new WeakReference<object>(__instance);
            if (BotItemField.GetValue(__instance) is MyGuiControlListbox.Item item)
                item.Text = new StringBuilder("-" + Name + "-");
        }

        // Returning false skips the game's own SendMessage.
        private static bool PrefixSendMessage(object __instance)
        {
            try
            {
                open = new WeakReference<object>(__instance);
                var list = (MyGuiControlListbox)PlayerListField.GetValue(__instance);
                var botItem = BotItemField.GetValue(__instance);
                if (hub == null || MySession.Static?.LocalCharacter == null || list.SelectedItems.Count == 0 || list.SelectedItems[0] != botItem)
                    return true;

                var chatbox = (MyGuiControlTextbox)ChatboxField.GetValue(__instance);
                var text = new StringBuilder();
                chatbox.GetText(text);
                chatbox.Text = string.Empty;
                string body = text.ToString().Trim();
                // "/ai ..." typed here out of habit means the same thing.
                if (body.StartsWith(prefix + " ", StringComparison.OrdinalIgnoreCase) || body.Equals(prefix, StringComparison.OrdinalIgnoreCase))
                    body = body.Substring(prefix.Length).Trim();
                if (body.Length == 0)
                    return false;

                MySession.Static.ChatSystem.ChatHistory.EnqueueMessage(body, ChatChannel.ChatBot, MySession.Static.LocalPlayerId, -1L, DateTime.UtcNow);
                string refusal = ChatInbox.Submit(hub, body, "comms", prefix);
                if (refusal != null)
                    AddReply(Name, refusal);
                Refresh();
                return false;
            }
            catch (Exception)
            {
                return true;
            }
        }

        // Game thread. Adds a line from the AI to the Suit AI conversation, and redraws it if it is on screen.
        public static void Post(string sender, string text)
        {
            if (MySession.Static?.ChatSystem == null)
                throw new InvalidOperationException("No world is loaded");
            AddReply(sender ?? Name, text);
            Refresh();
        }

        private static void AddReply(string sender, string text)
        {
            var item = MyUnifiedChatItem.CreateChatbotMessage(text, DateTime.UtcNow, 0L, MySession.Static.LocalPlayerId, sender);
            MySession.Static.ChatSystem.ChatHistory.EnqueueMessage(ref item);
        }

        private static void Refresh()
        {
            if (open == null || !open.TryGetTarget(out var controller))
                return;
            if (ClosedField?.GetValue(controller) is bool closed && closed)
                return;
            var list = (MyGuiControlListbox)PlayerListField.GetValue(controller);
            if (list.SelectedItems.Count > 0 && list.SelectedItems[0] == BotItemField.GetValue(controller))
                RefreshHistory.Invoke(controller, null);
        }
    }
}
