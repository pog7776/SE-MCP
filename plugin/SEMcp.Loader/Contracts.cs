namespace SEMcp.Loader
{
    // The only types shared between the loader and the reloadable host.
    // Changing them means a game restart, so keep them minimal.

    public interface IHostModule
    {
        // All three are called on the game thread.
        void Start(ILoaderContext context);
        void Update();
        void Stop();
    }

    public interface ILoaderContext
    {
        // Increments on every (re)load, starting at 1.
        int Generation { get; }
        string LoaderVersion { get; }
        void Log(string message);
        // Thread-safe. The loader stops the host and loads the latest DLLs on the next frame.
        void RequestReload();
    }
}
