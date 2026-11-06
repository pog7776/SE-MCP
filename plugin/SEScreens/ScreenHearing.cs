using System;
using Sandbox.Engine.Utils;
using Sandbox.Game.Entities;
using Sandbox.Game.Entities.Character;
using Sandbox.Game.GameSystems;
using Sandbox.Game.World;
using Sandbox.ModAPI;
using VRageMath;

namespace SEScreens
{
    // Whether the player can hear a screen, with the world's Realistic sound on. Sound needs air between the
    // screen and the player:
    //   same air (one pressurized room, or both out in a planet's air)  clear (muffled by a closed helmet)
    //   both in air, on one ship but in separate rooms                  through the wall: quieter, muffled
    //   touching the screen's ship but no shared air (vacuum, suit on)  through the hull: faint, very muffled
    //   otherwise                                                       silent
    // Rooms are the game's own (its pressurization), so opening a door between two rooms joins them.
    // Arcade sound, or a world without pressurization, keeps the old rule: hear it if you're in air or on its ship.
    internal enum Hearing { Clear, Helmet, Wall, Hull, Silent, Arcade }

    internal static class ScreenHearing
    {
        // Oxygen levels below this count as vacuum, as for the game's own sounds.
        private const float MinOxygen = 0.1f, MinAirDensity = 0.5f;

        public static float Gain(Hearing h) =>
            h == Hearing.Wall ? 0.4f : h == Hearing.Hull ? 0.25f : h == Hearing.Silent ? 0 : 1;

        // Low-pass cutoff in Hz, or 0 for none. The helmet matches the game's LowPassHelmet.
        public static float Cutoff(Hearing h) =>
            h == Hearing.Helmet ? 1800 : h == Hearing.Wall ? 500 : h == Hearing.Hull ? 300 : 0;

        public static string Describe(Hearing h) =>
            h == Hearing.Arcade ? "clear (the world uses Arcade sound)" : h == Hearing.Clear ? "clear" : h == Hearing.Helmet ? "through the helmet" : h == Hearing.Wall ? "through a wall"
            : h == Hearing.Hull ? "through the hull" : "silent (no air between)";

        // screen: the screen's world position; front: its facing in world space (or zero).
        public static Hearing For(IMyTerminalBlock block, Vector3D screen, Vector3D front)
        {
            var session = MySession.Static;
            var character = session?.LocalCharacter;
            if (session == null || !session.Settings.RealisticSound || !MyFakes.ENABLE_NEW_SOUNDS)
                return Hearing.Arcade;
            if (character == null || character.IsDead)
                return Hearing.Clear;
            var cockpit = character.Parent as MyCockpit;
            if (cockpit == block)
                return Hearing.Clear;
            var grid = (MyCubeGrid)block.CubeGrid;
            var playerGrid = cockpit?.CubeGrid ?? character.SoundComp?.StandingOnGrid;
            bool helmet = character.OxygenComponent?.HelmetEnabled == true;
            bool touching = Touching(grid, playerGrid);

            if (!session.Settings.EnableOxygen || !session.Settings.EnableOxygenPressurization || grid.GridSystems.GasSystem == null)
            {
                bool inAir = character.AtmosphereDetectorComp != null && !character.AtmosphereDetectorComp.InVoid;
                return inAir ? (helmet ? Hearing.Helmet : Hearing.Clear) : touching ? Hearing.Hull : Hearing.Silent;
            }

            // The screen's air: a point just in front of it, else its own cell.
            var source = Air.At(grid, screen + front * 0.4);
            if (source.Solid)
                source = Air.At(grid, screen);
            if (source.Solid)
                source = Air.At(grid, block.WorldMatrix.Translation);

            Vector3D head = character.GetHeadMatrix(false).Translation;
            Air listener;
            if (cockpit != null && cockpit.BlockDefinition?.IsPressurized == true)
                listener = new Air { Breathable = true, Sealed = true };
            else
            {
                listener = Air.At(grid, head);
                // Aboard another ship: its rooms are air of their own.
                if (listener.Room == null && playerGrid != null && playerGrid != grid && playerGrid.GridSystems.GasSystem != null)
                {
                    var aboard = Air.At(playerGrid, head);
                    if (aboard.Room != null)
                        listener = new Air { Breathable = aboard.Breathable, Sealed = true };
                }
            }
            touching |= listener.Room != null;

            bool shared = source.Breathable && listener.Breathable
                && (source.Room != null ? source.Room == listener.Room : listener.Room == null && !listener.Sealed && !listener.Solid);
            if (shared)
                return helmet ? Hearing.Helmet : Hearing.Clear;
            if (source.Breathable && listener.Breathable && touching)
                return Hearing.Wall;
            return touching ? Hearing.Hull : Hearing.Silent;
        }

        // On the screen's ship, or one joined to it (rotors, pistons, connectors, landing gear).
        private static bool Touching(MyCubeGrid screenGrid, MyCubeGrid playerGrid) =>
            playerGrid != null && (playerGrid == screenGrid || MyCubeGridGroups.Static.Physical.HasSameGroup(playerGrid, screenGrid));

        private struct Air
        {
            public bool Breathable;
            public MyOxygenRoom Room;  // an airtight room of the screen's ship; null outside or open to space
            public bool Sealed;        // air of its own: a pressurized cockpit, or a room of another ship
            public bool Solid;         // inside an airtight block, so no air of its own

            // The air at a point, as the screen's ship's gas system sees it. Rooms open to outside have the
            // planet's air, if any.
            public static Air At(MyCubeGrid grid, Vector3D point)
            {
                var block = grid.GridSystems.GasSystem.GetOxygenBlock(point);
                if (block == null)
                    return new Air { Solid = true };
                var room = block.Room;
                if (room != null && room.IsAirtight)
                    return new Air { Room = room, Breathable = room.OxygenLevel(grid.GridSize) >= MinOxygen };
                return new Air { Breathable = PlanetAir(point) };
            }
        }

        private static bool PlanetAir(Vector3D point)
        {
            if (MyGravityProviderSystem.CalculateNaturalGravityInPoint(point).LengthSquared() <= 0)
                return false;
            var planet = MyGamePruningStructure.GetClosestPlanet(point);
            return planet != null && planet.HasAtmosphere && planet.GetAirDensity(point) > MinAirDensity;
        }
    }

    // Shapes a screen's 16-bit mono sound for what the player can hear: a gain that eases over about 0.3 s, so
    // stepping through a door fades rather than cuts, and a low-pass filter (two-pole, RBJ biquad). The filter's
    // history runs while it's off too, so switching it on doesn't click.
    internal sealed class SoundShaper
    {
        private const float SampleRate = 24000, FadePerSample = 1 / (0.3f * SampleRate);
        private float gain = -1, cutoff;
        private float b0, b1, b2, a1, a2, x1, x2, y1, y2;

        public float TargetGain = 1, TargetCutoff;

        // volume: the screen's own setting, as a factor.
        public void Apply(byte[] pcm, float volume)
        {
            if (gain < 0)
                gain = TargetGain;
            if (TargetCutoff != cutoff)
                SetCutoff(TargetCutoff);
            for (int i = 0; i + 1 < pcm.Length; i += 2)
            {
                if (gain < TargetGain)
                    gain = Math.Min(TargetGain, gain + FadePerSample);
                else if (gain > TargetGain)
                    gain = Math.Max(TargetGain, gain - FadePerSample);
                float x = (short)(pcm[i] | pcm[i + 1] << 8);
                float y = cutoff > 0 ? b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2 : x;
                x2 = x1; x1 = x; y2 = y1; y1 = y;
                int s = (int)Math.Max(short.MinValue, Math.Min(short.MaxValue, y * gain * volume));
                pcm[i] = (byte)s;
                pcm[i + 1] = (byte)(s >> 8);
            }
        }

        private void SetCutoff(float hz)
        {
            cutoff = hz;
            if (hz <= 0)
                return;
            const float q = 0.707f;
            double w = 2 * Math.PI * hz / SampleRate, alpha = Math.Sin(w) / (2 * q), cos = Math.Cos(w), a0 = 1 + alpha;
            b0 = (float)((1 - cos) / 2 / a0);
            b1 = (float)((1 - cos) / a0);
            b2 = b0;
            a1 = (float)(-2 * cos / a0);
            a2 = (float)((1 - alpha) / a0);
        }
    }
}
