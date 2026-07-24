# System Monitors

CPU / RAM / GPU / network readouts as notification-area icons, one per metric,
redrawn every couple of seconds. Hover any of them for the full line including
network rates.

This replaced the floating dock panel. The reasoning matters, because it is a
real Windows constraint rather than a preference:

* **Nothing can add content to the taskbar strip itself.** Windows 11 removed
  deskbands (the API that used to allow taskbar toolbars). Putting a widget
  there needs shell-level patching of Explorer, which is out of scope.
* **The notification area is part of the taskbar** and accepts as many icons as
  you like. That is where these live — the same place GNOME's Vitals puts its
  readouts.
* **The taskbar already does app icons and window buttons.** Setting
  *Combine taskbar buttons and hide labels* to **Never** gives every window its
  own labelled button, which is what the dock's window chips were for.

## Running it

```
C:\Users\c\AppData\Local\Programs\Python\Python312\pythonw.exe D:\code\winThings\monitors\app.py
```

| Flag | Purpose |
| --- | --- |
| `--settings` | Open the settings window at startup |
| `--selftest` | Print three real samples and icon sizes, then exit. No icons shown. |
| `--lifetime=N` | Quit after N seconds |

Only one instance runs at a time (named mutex).

## Using it

Left-click or right-click any icon for the menu: **Settings**, **Quit**.
Hovering shows every metric at once, e.g.
`CPU 12%   RAM 34%   GPU 0%   Up 15 KB/s   Down 608 B/s`

Windows sorts tray icons itself, so the order is not under this app's control.
If they land in the hidden overflow, drag them onto the taskbar once and Windows
remembers.

## Config

`%APPDATA%\WinThings\monitors\config.json`

| Key | Default | Meaning |
| --- | --- | --- |
| `poll_interval` | `2.0` | Seconds between samples (0.5-10) |
| `show_cpu` | `true` | |
| `show_ram` | `true` | |
| `show_gpu` | `true` | Ignored when NVML is unavailable |
| `show_net_up` | `false` | Off by default - five tray icons is a lot |
| `show_net_down` | `false` | Off by default; the rate is in every tooltip |
| `autostart` | `false` | Mirrors the HKCU `Run` entry `WinThingsMonitors` |

Toggling a readout adds or removes its icon immediately, with no restart.

## Limitations

**Icon legibility.** Windows renders tray icons at 16x16. These are drawn at
64x64 and downsampled, which keeps two digits crisp; three digits (`100`, `3.2M`)
are readable but tight. The tooltip is the precise source.

**GPU on Optimus laptops.** On this machine (RTX 3050 Laptop) NVML answers once
and then returns `NVML_ERROR_UNKNOWN` for every subsequent query, because the
discrete GPU parks. Recovery means tearing down the NVML session and
re-initialising, which costs ~575 ms, so it is rate-limited to once per 10 s and
the last good reading is shown in between. A parked GPU is genuinely idle, so a
held `0` is accurate — but the GPU number refreshes far more slowly than the
others, and behaviour under sustained GPU load is untested. With no NVIDIA card
or no working NVML, the icon is not created at all. AMD and Intel GPUs are not
supported.

**Tray icon order** is decided by Windows, not by this app.
