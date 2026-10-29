# Space Engineers programmable block (PB) primer

A PB script is the *body* of a C# class (`Program : MyGridProgram`). Write members only: no `namespace`,
no `class Program`, no `using` lines. The game compiles C# 6 (no records, no `switch` expressions, no
`is not`, no `??=`).

```csharp
// Optional: runs once after each compile or world load.
public Program()
{
    Runtime.UpdateFrequency = UpdateFrequency.Update100; // None (default), Once, Update1, Update10, Update100
}

// Optional: called when the game saves. Persist state in Storage (a string).
public void Save()
{
    Storage = counter.ToString();
}

// Required. argument is the Run argument ("" for timed updates).
public void Main(string argument, UpdateType updateSource)
{
    if ((updateSource & (UpdateType.Terminal | UpdateType.Trigger)) != 0) { /* run by a player/button/timer */ }
    if ((updateSource & UpdateType.Update100) != 0) { /* every ~1.67 s */ }
    Echo("Hello");          // shows in the PB's terminal info (what pb_output returns)
}

int counter;
```

## Finding blocks

```csharp
var lcd = GridTerminalSystem.GetBlockWithName("LCD Status") as IMyTextPanel;
var group = GridTerminalSystem.GetBlockGroupWithName("Cargo");
var batteries = new List<IMyBatteryBlock>();
GridTerminalSystem.GetBlocksOfType(batteries, b => b.IsSameConstructAs(Me)); // skip docked ships
group?.GetBlocksOfType(batteries);
```

`Me` is the PB itself. Always null-check lookups and `Echo` a clear message when something is missing.
Cache block lists in `Program()` (or refresh rarely); searching every tick costs instructions.

## Common interfaces (namespace `Sandbox.ModAPI.Ingame`, all available)

- `IMyTerminalBlock`: `CustomName`, `CustomData`, `DetailedInfo`, `ApplyAction("OnOff_On")`, `GetProperty`
- `IMyFunctionalBlock`: `Enabled`
- `IMyBatteryBlock` (`CurrentStoredPower`, `MaxStoredPower` in MWh, `ChargeMode`), `IMyGasTank` (`FilledRatio`),
  `IMyCargoContainer` / any block's `GetInventory()` (`CurrentVolume`, `MaxVolume`, `GetItems`)
- `IMyThrust` (`ThrustOverridePercentage`, `MaxEffectiveThrust`), `IMyGyro` (`GyroOverride`, `Pitch`/`Yaw`/`Roll` rad/s)
- `IMyShipController` / `IMyCockpit` / `IMyRemoteControl`: `GetNaturalGravity()`, `GetShipSpeed()`,
  `CalculateShipMass()`, `MoveIndicator`, `DampenersOverride`
- `IMyTextSurface`: `WriteText(string, append)`, `ContentType = ContentType.TEXT_AND_IMAGE`, `FontSize`.
  A cockpit's screens: `(cockpit as IMyTextSurfaceProvider).GetSurface(0)`. The PB's own screen: `Me.GetSurface(0)`.
- `IMyDoor`, `IMyLightingBlock` (`Color`, `Intensity`), `IMyShipConnector` (`Status`, `Connect()`),
  `IMyMotorStator` (`TargetVelocityRPM`, `Angle` rad), `IMyPistonBase` (`Velocity`, `CurrentPosition`),
  `IMyTimerBlock`, `IMySensorBlock` (`IsActive`, `DetectedEntities`), `IMyCameraBlock` (`Raycast`)
- Inventory items: `MyInventoryItem` (`Type.TypeId`, `Type.SubtypeId`, `Amount` as `MyFixedPoint`, cast with `(double)`)
- Math: `VRageMath` (`Vector3D`, `MatrixD`, `MathHelper`). Block orientation: `block.WorldMatrix.Forward`.

## Limits

- Instruction count per run (~50,000); exceeding it throws "Script Too Complex". Spread work over ticks.
- No file IO, threads, reflection, networking or `System.Timers`. LINQ and `System.Collections.Generic` are allowed.
- `Runtime.TimeSinceLastRun`, `Runtime.LastRunTimeMs`, `Runtime.CurrentInstructionCount` help with profiling.
- An uncaught exception stops the script until it's recompiled; the PB's info shows the exception.
- Inter-grid comms: `IGC.SendBroadcastMessage(tag, data)`, `IGC.RegisterBroadcastListener(tag)` (needs antennas).

## CustomData config

A common pattern is reading settings from `Me.CustomData` with `MyIni`:

```csharp
MyIni ini = new MyIni();
if (ini.TryParse(Me.CustomData)) { string lcdName = ini.Get("config", "lcd").ToString("LCD"); }
```

## Compile errors

`write_pb_script` returns the compiler's messages. The game rewrites the script before compiling, so the
line/column in `Program(42,17)` don't match your code exactly: find the error by its message and nearby code.
