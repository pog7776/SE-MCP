using System;

namespace SEMcp.Core
{
    // Error kinds sent to the client in error.data.kind. Keep in sync with protocol.md.
    public static class ErrorKind
    {
        public const string NoSession = "no_session";
        public const string NotFound = "not_found";
        public const string Ambiguous = "ambiguous";
        public const string AccessDenied = "access_denied";
        public const string OutOfRange = "out_of_range";
        public const string ReadOnly = "read_only";
        public const string InvalidParams = "invalid_params";
        public const string FeatureDisabled = "feature_disabled";
        public const string ScriptsDisabled = "scripts_disabled";
        public const string UnknownMethod = "unknown_method";
        public const string GameError = "game_error";
        public const string Timeout = "timeout";
    }

    public class RpcException : Exception
    {
        public string Kind { get; }

        public RpcException(string kind, string message) : base(message)
        {
            Kind = kind;
        }
    }
}
