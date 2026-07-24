# Dock

A slim always-on-top bar along one screen edge: app icons, one chip per open
window, and a compact CPU / RAM / GPU / network readout on the far end.

This is a plain floating window. Nothing here hooks, patches, injects into or
replaces Explorer, the Start menu or the real taskbar — it just sits where the
taskbar used to be.

## Auto-hide the real taskbar first

The dock does not reserve screen space, so set the Windows taskbar to auto-hide
or the two will overlap:

**Settings → Personalisation → Taskbar → Taskbar behaviours → Automatically
hide the taskbar**

The dock positions itself against the *work area*, so before you auto-hide it
will sit just above the real taskbar, and after you auto-hide it drops flush to
the screen edge. No restart needed either way — reopen Settings from the tray
and nudge any value, or just restart the dock.

## Running it

```
C:\Users\c\AppData\Local\Programs\Python\Python312\pythonw.exe D:\code\winThings\dock\app.py
```

Use `python.exe` instead of `pythonw.exe` if you want a console for errors.
From the suite launcher: `python winthings.py dock`.

Headless check — enumerates windows, reports which icons resolved, prints three
real metric samples, and exits without showing a window:

```
python.exe D:\code\winThings\dock\app.py --selftest
```

`--lifetime=N` runs the real dock and auto-quits after N seconds. Handy for
trying a layout without committing to it.

Only one instance runs at a time (named mutex, via `common/singleton.py`).

## Using it

| Action | Result |
| --- | --- |
| Click an app icon | Focus its window; if it is already focused, minimise it |
| Click an icon with several windows open | Cycle to that app's next window |
| Click a window chip | Focus (or minimise) that specific window |
| Right-click an app icon | Pin / unpin that executable |
| Click a pinned-but-closed icon | Launch it |
| Tray icon | Settings, Refresh now, Hide dock, Quit |

Apps with two or more open windows fan their windows out as title chips beside
the icon — always visible, which is the point; it replaces Windows' hover
flyout grouping. Single-window apps show just an icon. The bar under each icon
is the running indicator: accent-coloured when running, wider when the app has
more than one window, dim when the app is pinned but closed (its icon is also
faded).

## Settings

Tray → Settings. Every control writes straight to the config file and the dock
picks it up immediately through `Config.subscribe` — there is no apply button
and no restart.

## Config

`%APPDATA%\WinThings\dock\config.json`

| Key | Default | Meaning |
| --- | --- | --- |
| `edge` | `"bottom"` | `bottom`, `top`, `left`, `right` |
| `span` | `"full"` | `full` spans the whole edge; `fit` hugs the icons and centres |
| `margin` | `0` | Gap between dock and screen edge, px (0–80) |
| `icon_size` | `32` | 16–64 px, scales every icon live |
| `show_chips` | `true` | Expand multi-window apps into title chips |
| `chip_chars` | `22` | Truncate chip titles to this many characters (8–60) |
| `window_refresh` | `1.0` | Seconds between window-list scans (0.25–5) |
| `poll_interval` | `2.0` | Seconds between metric samples (0.5–10) |
| `show_cpu` | `true` | |
| `show_ram` | `true` | |
| `show_gpu` | `true` | Ignored when NVML is unavailable — the readout is hidden |
| `show_net_up` | `true` | |
| `show_net_down` | `true` | |
| `pinned` | `[]` | Executable paths that stay on the dock when closed |
| `autostart` | `false` | Mirrors the HKCU `Run` entry `WinThingsDock` |

Out-of-range values are clamped rather than rejected, and an unparseable file
falls back to these defaults.

## Limitations

These are real and not worked around silently.

**UWP / packaged app icons.** Store apps (Calculator, Photos, Settings, Terminal
when installed from the Store) do not keep their icon inside the `.exe` — it is
a PNG asset in the package, listed in `AppxManifest.xml` under
`C:\Program Files\WindowsApps`, and that directory is ACL-locked to
`TrustedInstaller`. A normal user account gets `Access is denied` even reading
the manifest, so these apps fall back to a generated letter tile. Confirmed on
this machine with Calculator. Everything else about them works — grouping,
chips, click-to-focus — because the dock unwraps `ApplicationFrameHost.exe` to
find the real owning process. Fixing the icons properly needs
`IShellItemImageFactory` against the `shell:AppsFolder` entry, which is not
implemented.

**Elevated processes.** If a window belongs to a process running at a higher
integrity level, `psutil` cannot read its executable path. The window still
appears, still groups, and still responds to clicks, but the icon comes from
`WM_GETICON` only; if the app does not answer that, it gets a letter tile.
Nothing in the dock will ever be able to focus an elevated window from a
non-elevated dock — Windows blocks cross-integrity input.

**GPU on Optimus laptops.** On this machine (RTX 3050 Laptop) NVML initialises
and reports fine, but when the discrete GPU parks, the driver invalidates the
NVML session and every query returns `NVML_ERROR_UNKNOWN` until the library is
shut down and re-initialised — about 600 ms. The dock rate-limits that recovery
to once every 10 s and shows the last good reading in between, rather than
burning a core re-initialising every poll or flickering a dash. If NVML never
initialises at all (no NVIDIA card, no driver, AMD/Intel only), the GPU readout
is removed from the bar entirely. AMD and Intel GPUs are not supported.

**Maximised windows go under the dock.** The dock does not register as an
appbar (`SHAppBarMessage`), so Windows does not reserve space for it. Being
topmost, it covers a strip of whatever is maximised beneath it. An orphaned
appbar registration permanently shrinks the desktop work area if the process
dies, which seemed a worse failure mode than an overlapping strip.

**Primary monitor only.** The dock always positions itself on the primary
display; there is no monitor picker.

**Vertical edges are wider than you might expect.** On `left`/`right` the bar is
as wide as its widest window chip: measured at 78 px with `show_chips` off,
130 px with chips at `chip_chars: 22` and short titles, and up to ~200 px with
long ones. Lower `chip_chars` or turn `show_chips` off for a narrow strip.

**Titles are truncated from the right** with an ellipsis, and are not
deduplicated — two untitled Notepad windows both read `Untitled - Notepad`.

**DPI.** Geometry is computed in physical pixels and converted using
customtkinter's window scaling factor, so the bar is the right thickness at any
scaling. Only tested at 100% on a single 1920x1080 display.

## Files

| File | Role |
| --- | --- |
| `app.py` | Entry point: config, dock window, tray, scanner and poller threads |
| `dockwindow.py` | The bar itself — layout, positioning, window styles, clicks |
| `winlist.py` | Window enumeration/filtering and the activation ladder |
| `icons.py` | HICON extraction and conversion to Pillow images, cached per exe |
| `metrics.py` | Background CPU/RAM/GPU/network sampler |
| `scanner.py` | Builds the dock model off-thread, redraws only on real change |
| `settings.py` | Settings window (subclasses `common.ui.SettingsWindow`) |
