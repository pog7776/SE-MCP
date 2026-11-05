using System;

namespace SEScreens
{
    // Anything that can drive a screen: a captured window, the test card, a browser, later streams or other
    // plugins pushing frames. Screens owns the texture and the upload; a source only makes pixels.
    //
    // Frames are RGBA, Width x Height, row 0 at the top, in an array of FrameBuffer.Bytes(Width, Height) bytes (room for
    // the mip chain, which the GPU fills). Alpha is the screen material's metalness: use 0. Each frame must be a
    // new array, because the renderer keeps it until it uploads.
    public interface IFrameSource : IDisposable
    {
        int Width { get; }
        int Height { get; }
        // True once the source can't produce any more (its window closed, its process exited).
        bool Ended { get; }
        string Error { get; }
        int Frames { get; }

        // Thread-safe. The newest frame since the last call, or null if nothing new.
        byte[] TakeLatest();
    }

    // A source with sound. Screens plays it from the screen's block, positioned in the world.
    public interface IAudioSource
    {
        // Game thread. New samples since the last call as 16-bit little-endian mono at 24 kHz (the game's
        // streamed-voice format), or null.
        byte[] TakeAudio();
    }

    [Flags]
    public enum InputModifiers
    {
        None = 0,
        Shift = 1,
        Control = 2,
        Alt = 4,
        LeftButton = 8,
        MiddleButton = 16,
        RightButton = 32,
    }

    public enum MouseButton { Left, Middle, Right }

    // A source you can use: ScreenInput sends it the player's mouse and keyboard while they're in screen input
    // mode. Positions are pixels of the source's frame (0..Width-1, 0..Height-1). Keys are Windows virtual-key
    // codes (Shift, Control and Alt as the generic 16/17/18); Char is typed text, after the keyboard layout.
    // Game thread; implementations hand off rather than block.
    public interface IInputSink
    {
        void Focus(bool focused);
        void MouseMove(int x, int y, InputModifiers modifiers);
        void MouseButton(int x, int y, MouseButton button, bool down, int clicks, InputModifiers modifiers);
        void MouseWheel(int x, int y, int delta, InputModifiers modifiers);
        void Key(int virtualKey, bool down, bool repeat, InputModifiers modifiers);
        void Char(char c, InputModifiers modifiers);
    }

    // What a screen shows, for listings.
    public sealed class SourceInfo
    {
        public string Kind;    // "window", "testcard", "browser", ...
        public string Title;
        public string Detail;  // window: process name; browser: URL
        public string Handle;  // window handle as 0x..., else null
    }

    public static class FrameBuffer
    {
        // Level 0 plus the mip chain below it, as MyRenderComponentScreenAreas.GetTextureByteCount sizes it.
        public static int Bytes(int width, int height) => (int)(width * height * 4 * 1.3333333f);
    }
}
