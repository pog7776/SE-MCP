using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using IngameProgrammableBlock = Sandbox.ModAPI.Ingame.IMyProgrammableBlock;
using UpdateType = Sandbox.ModAPI.Ingame.UpdateType;

namespace SEMcp.Core.Services
{
    // Programmable blocks: read and replace the script, recompile, run. The block's DetailedInfo is its output:
    // compile errors, exceptions, or what the script Echo'd on its last run.
    internal sealed class ProgrammableBlockService
    {
        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public ProgrammableBlockService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("pb.get", Get);
            dispatcher.Register("pb.set_code", SetCode);
            dispatcher.Register("pb.recompile", Recompile);
            dispatcher.Register("pb.run", Run);
        }

        private void Log(string message) => dispatcher.Context.Log(message);

        private IMyProgrammableBlock Pb(JsonObject p)
        {
            var block = access.Block(p.Id("blockId"));
            return block as IMyProgrammableBlock
                ?? throw new RpcException(ErrorKind.InvalidParams, $"'{block.CustomName}' is not a programmable block");
        }

        private static bool ScriptsEnabled => MyAPIGateway.Session.SessionSettings?.EnableIngameScripts ?? true;

        // On a multiplayer client the server compiles and runs scripts, and the output arrives a moment later.
        private static bool IsServer => MyAPIGateway.Multiplayer == null || MyAPIGateway.Multiplayer.IsServer;

        private object State(IMyProgrammableBlock pb, bool includeCode) => new
        {
            id = Dto.Id(pb.EntityId),
            name = pb.CustomName,
            enabled = pb.Enabled,
            working = pb.IsWorking,
            scriptsEnabled = ScriptsEnabled,
            isServer = IsServer,
            hasCompileErrors = IsServer ? pb.HasCompileErrors : (bool?)null,
            terminalArgument = ((IngameProgrammableBlock)pb).TerminalRunArgument,
            codeLength = pb.ProgramData?.Length ?? 0,
            code = includeCode ? pb.ProgramData ?? "" : null,
            output = pb.DetailedInfo,
            // Errors and warnings from the last compile (host-supplied; null when unknown).
            compilerMessages = dispatcher.Context.PbCompilerErrors(pb),
        };

        private object Get(JsonObject p) => State(Pb(p), p.Bool("includeCode", true));

        private void RequireCodePush()
        {
            dispatcher.RequireWritable();
            if (!dispatcher.Context.AllowPbCodePush)
                throw new RpcException(ErrorKind.ReadOnly, "Pushing PB code is turned off (allowPbCodePush in config.json)");
        }

        // Replacing ProgramData recompiles, like pressing OK in the editor.
        private object SetCode(JsonObject p)
        {
            RequireCodePush();
            var pb = Pb(p);
            string code = p.String("code") ?? throw new RpcException(ErrorKind.InvalidParams, "Missing 'code'");
            int before = pb.ProgramData?.Length ?? 0;
            pb.ProgramData = code;
            Log($"pb code on '{pb.CustomName}' ({pb.EntityId}): {before} -> {code.Length} chars");
            return State(pb, includeCode: false);
        }

        private object Recompile(JsonObject p)
        {
            dispatcher.RequireWritable();
            var pb = Pb(p);
            pb.Recompile();
            Log($"pb recompile '{pb.CustomName}' ({pb.EntityId})");
            return State(pb, includeCode: false);
        }

        // Like pressing Run in the terminal. With no argument, uses the block's default terminal argument.
        private object Run(JsonObject p)
        {
            dispatcher.RequireWritable();
            var pb = Pb(p);
            if (!pb.IsWorking)
                throw new RpcException(ErrorKind.InvalidParams, $"'{pb.CustomName}' is off, unpowered or damaged");
            string argument = p.String("argument") ?? ((IngameProgrammableBlock)pb).TerminalRunArgument;
            pb.Run(argument, UpdateType.Terminal);
            Log($"pb run '{pb.CustomName}' ({pb.EntityId}) argument '{argument}'");
            return State(pb, includeCode: false);
        }
    }
}
