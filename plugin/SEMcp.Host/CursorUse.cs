using System;
using System.Collections.Generic;
using Sandbox.Game.Entities;
using Sandbox.Game.Entities.Character;
using Sandbox.Game.Entities.Cube;
using Sandbox.Game.GUI;
using Sandbox.Game.World;
using SEScreens;
using VRage.Audio;
using VRage.Game;
using VRage.Game.Components;
using VRage.Game.Entity.UseObject;
using VRage.ModAPI;
using VRage.Utils;
using VRageMath;

namespace SEMcp.Host
{
    // Left-click in cursor mode (off every screen) uses what's under the cursor, as F uses what's under the crosshair:
    // button panel buttons, doors, seats, cargo. The game's own interaction steps (MyCharacterRaycastDetectorComponent
    // and MyCharacter.Use) along the cursor's line instead of the crosshair's: the mesh hit, then the hit entity's use
    // detectors, then Use with its primary action, so each use object syncs itself in multiplayer as it does for F.
    // The character's own detector (the crosshair, the F prompt, highlights) is left alone.
    internal static class CursorUse
    {
        public static void Start() => CursorMode.AddClickHandler(Click);

        public static void Stop() => CursorMode.RemoveClickHandler(Click);

        private static bool Click(CursorClick click)
        {
            if (click.Button != MouseButton.Left)
                return false;
            var character = MySession.Static?.LocalCharacter;
            if (character == null || character.IsDead || character.IsOnLadder || MySession.Static.ControlledEntity != character)
                return false;
            var use = Find(character, click.Line.Direction);
            if (use == null)
                return false;
            var action = use.PrimaryAction != UseActionEnum.None ? use.PrimaryAction : use.SecondaryAction;
            if (action == UseActionEnum.None)
                return false;
            if (use.PlayIndicatorSound)
                MyGuiAudio.PlaySound(MyGuiSounds.HudUse);
            use.Use(action, character);
            return true;
        }

        // From the head's plane, as far as F reaches, along the cursor's direction.
        private static IMyUseObject Find(MyCharacter character, Vector3D direction)
        {
            var camera = MySector.MainCamera;
            if (camera == null)
                return null;
            var head = character.GetHeadMatrix(includeY: false);
            var from = MyUtils.LinePlaneIntersection(head.Translation - head.Forward * 0.3, (Vector3)camera.ForwardVector, camera.Position, (Vector3)direction);
            var to = from + direction * MyConstants.DEFAULT_INTERACTIVE_DISTANCE;
            // Blocks that let you use what's behind them (a glass door's frame, say) pass the line on; a few at most.
            for (int pass = 0; pass < 3; pass++)
            {
                var line = new LineD(from, to);
                var hit = MyEntities.GetIntersectionWithLine(ref line, character, null, ignoreChildren: false, ignoreFloatingObjects: false);
                if (hit == null)
                    return null;
                IMyEntity entity = hit.Value.Entity;
                var cube = (hit.Value.UserObject as MyCube)?.CubeBlock;
                if (entity is MyCubeGrid && cube?.FatBlock != null)
                    entity = cube.FatBlock;
                var position = hit.Value.IntersectionPointInWorldSpace;
                var found = UseObjectAt(entity, from, to, position);
                if (found != null)
                    return found;
                if (cube?.FatBlock?.BlockDefinition.AllowInteractionThroughBlock != true)
                    return null;
                from = position + direction * 0.05;
            }
            return null;
        }

        // The entity itself if it's a use object, else the nearest use detector on it or its parents the line enters
        // before the mesh it hit.
        private static IMyUseObject UseObjectAt(IMyEntity entity, Vector3D from, Vector3D to, Vector3D hitPosition)
        {
            var components = new List<MyUseObjectsComponentBase>();
            for (var e = entity; e != null; e = e.Parent)
            {
                var component = e.Components.Get<MyUseObjectsComponentBase>();
                if (component != null)
                    components.Add(component);
            }
            IMyUseObject best = entity as IMyUseObject;
            float bestDistance = float.MaxValue;
            double reach = Vector3D.Distance(from, hitPosition);
            double length = Vector3D.Distance(from, to);
            foreach (var component in components)
            {
                var use = component.RaycastDetectors(from, to, out float parameter);
                float distance = (float)(parameter * length);
                if (use != null && Math.Abs(distance) < Math.Abs(bestDistance) && distance < reach)
                {
                    bestDistance = distance;
                    best = use;
                }
            }
            return best != null && best.SupportedActions != UseActionEnum.None ? best : null;
        }
    }
}
