# SEScreens

Live content on Space Engineers screens: web pages you can click and type into, with their sound coming from the
screen. SEScreens is a client plugin, so only players who have it installed see what its screens show. Players
without it see a "needs the SEScreens plugin" card.

## Screen apps

SEScreens adds apps to the script list of any LCD, cockpit screen or other text surface. In the terminal, set the
screen's content to **Script**, then pick one:

| App | What it does |
|---|---|
| **Web Browser** | Opens the screen's home page (see [Settings](#settings)). Each screen runs its own browser and keeps its own cookies and logins. |
| **Screen Test** | A test card that draws a cross where the mouse is. Use it to check that cursor mode reaches a screen. |
| **Window Mirror** | Shows a desktop window. Only tools that use SE-MCP drive it; on its own it shows an idle card. |

The world saves which app each screen runs. The first browser screen takes about 15 seconds to start; later ones
start faster.

## Cursor mode

Cursor mode is how you use a screen with the real mouse and keyboard.

- **Enter:** tap **Alt** (press and release it without moving the mouse; holding Alt to look around still works
  as before) or press the **Menu** key. It works anywhere, not just near a screen.
- The camera holds still and the cursor shows. A screen under the cursor gets the mouse, up to 8 m away.
- The screen at your crosshair has the keyboard to start with. Click a screen to give it the keyboard; click
  off every screen to take the keyboard back.
- **Leave:** tap Alt again, press Menu, or press Esc when no screen has the keyboard. While a screen has the
  keyboard, Esc goes to the page.

## Browser keys

While a browser screen has the keyboard, keys go to the page, plus the shortcuts a browser window would give you:

| Keys | Action |
|---|---|
| Ctrl+L or F6 | Type an address. Text with spaces, or with no dot, becomes a Google search |
| Alt+Left / Alt+Right | Back / forward |
| Alt+Home | Home page |
| F5 | Reload |

A link that would open a new tab or window opens on the same screen instead.

## Settings

The Web Browser app keeps its settings in the block's **Custom Data**, in an `[SEScreens]` section. The first time a
screen runs the app, the app adds the settings with their defaults, so you can find them and edit them there:

```ini
[SEScreens]
volume0=100
brightness0=100
home0=https://www.google.com
```

| Key | Value | Default |
|---|---|---|
| `home` | The page the screen opens and Alt+Home goes to. Changing it sends the screen there | `https://www.google.com` |
| `brightness` | Percent, 0–200. Above 100 brightens dark pages; white stays white | `100` |
| `volume` | Percent, 0–200 | `100` |

The number after each key is the screen it applies to: `0` is the block's first screen, `1` its second, and so on,
in the order the terminal lists them. A key with no number (for example `home=`) applies to every screen of the block.
A key with a number wins over one without. Edits take effect straight away.

The settings save with the world and sync like any Custom Data. Programmable block scripts that keep their own
sections in the same Custom Data are left alone.

## Good to know

- Sound plays from the screen's position in the world and fades out by about 40 m.
- Some sites' video won't play, because the browser build has no H.264. YouTube works.
- A page can't crash the game: each browser runs in its own process. A browser that crashes restarts after 10
  seconds.
- Dropdown lists on pages don't open yet.
