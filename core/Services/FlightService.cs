using System;
using System.Collections.Generic;
using System.Text.Json.Nodes;
using Sandbox.ModAPI;
using Sandbox.ModAPI.Interfaces;

namespace SEMcp.Core.Services
{
    // Batched thruster and gyro overrides for closed-loop flying from the client (docking). One call per control
    // tick instead of one per block. Overrides go through terminal properties, so they sync like a PB's would.
    internal sealed class FlightService
    {
        private readonly Dispatcher dispatcher;
        private readonly Access access;

        public FlightService(Dispatcher dispatcher, Access access)
        {
            this.dispatcher = dispatcher;
            this.access = access;
        }

        public void Register()
        {
            dispatcher.Register("flight.set", Set);
        }

        // thrusters: {id: fraction 0..1} (0 = stop overriding); gyros: {id: [pitch, yaw, roll] in RPM} or null to
        // release; dampeners: bool on the ship's controllers; release: drop every override on the ship.
        private object Set(JsonObject p)
        {
            dispatcher.RequireWritable();
            if (!dispatcher.Context.AllowAutopilot)
                throw new RpcException(ErrorKind.FeatureDisabled, "Flying is turned off (allowAutopilot in config.json)");
            int thrusters = 0, gyros = 0;
            if (p["thrusters"] is JsonObject t)
                foreach (var kv in t)
                {
                    if (!(access.Block(long.Parse(kv.Key)) is IMyThrust thrust))
                        continue;
                    thrust.ThrustOverridePercentage = (float)Math.Max(0, Math.Min(1, kv.Value.GetValue<double>()));
                    thrusters++;
                }
            if (p["gyros"] is JsonObject g)
                foreach (var kv in g)
                {
                    if (!(access.Block(long.Parse(kv.Key)) is IMyGyro gyro))
                        continue;
                    if (kv.Value is JsonArray rpm && rpm.Count == 3)
                    {
                        gyro.GyroOverride = true;
                        gyro.SetValueFloat("Pitch", rpm[0].GetValue<float>());
                        gyro.SetValueFloat("Yaw", rpm[1].GetValue<float>());
                        gyro.SetValueFloat("Roll", rpm[2].GetValue<float>());
                    }
                    else
                    {
                        gyro.SetValueFloat("Pitch", 0);
                        gyro.SetValueFloat("Yaw", 0);
                        gyro.SetValueFloat("Roll", 0);
                        gyro.GyroOverride = false;
                    }
                    gyros++;
                }
            if (p["gridId"] != null)
            {
                var grid = access.Grid(p.Id("gridId"));
                var grids = grid.GetGridGroup(VRage.Game.ModAPI.GridLinkTypeEnum.Mechanical)?.GetGrids(new List<VRage.Game.ModAPI.IMyCubeGrid>())
                    ?? new List<VRage.Game.ModAPI.IMyCubeGrid> { grid };
                foreach (var gr in grids)
                {
                    if (p["thrustersOn"] != null)
                        foreach (var th in gr.GetFatBlocks<IMyThrust>())
                            if (access.CanUse(th)) th.Enabled = p.Bool("thrustersOn", true);
                    if (p["dampeners"] != null)
                        foreach (var c in gr.GetFatBlocks<IMyShipController>())
                            if (access.CanUse(c))
                                c.DampenersOverride = p.Bool("dampeners", true);
                    if (p.Bool("release", false))
                    {
                        foreach (var th in gr.GetFatBlocks<IMyThrust>())
                            if (access.CanUse(th)) th.ThrustOverridePercentage = 0;
                        foreach (var gy in gr.GetFatBlocks<IMyGyro>())
                            if (access.CanUse(gy))
                            {
                                gy.SetValueFloat("Pitch", 0); gy.SetValueFloat("Yaw", 0); gy.SetValueFloat("Roll", 0);
                                gy.GyroOverride = false;
                            }
                    }
                }
            }
            return new { thrusters, gyros };
        }
    }
}
