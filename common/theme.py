"""One palette and one set of metrics for all three apps.

Dark, flat, slightly translucent panels — close to a GNOME shell aesthetic and
legible on top of arbitrary Windows wallpapers.
"""

BG = "#16181d"            # window / dock background
BG_ELEVATED = "#1e2128"   # cards, list rows, popup background
BG_HOVER = "#282c35"      # hover state
BG_ACTIVE = "#323744"     # pressed / selected
BORDER = "#2e333d"

FG = "#e6e8ec"            # primary text
FG_DIM = "#9aa1ad"        # secondary text, labels
FG_FAINT = "#666d7a"      # tertiary, placeholder

ACCENT = "#4c8dff"
ACCENT_HOVER = "#3d7ae8"
OK = "#3ecf8e"
WARN = "#e0a33e"
ERROR = "#e5484d"
ERROR_HOVER = "#c93b3f"

# Light counterparts of the colours above. customtkinter widgets take a
# (light, dark) pair and follow the appearance mode; the plain values stay the
# dark ones for tk widgets (clipboard popup, transcriber window) that can't.
LIGHT = {
    BG: "#f3f4f7", BG_ELEVATED: "#ffffff", BG_HOVER: "#e7e9ee", BG_ACTIVE: "#d9dde4",
    BORDER: "#d3d8e0", FG: "#1c1f26", FG_DIM: "#4a5160", FG_FAINT: "#6f7685",
    ACCENT: "#2f6fe4", ACCENT_HOVER: "#265fc8", OK: "#15803d", WARN: "#b45309",
    ERROR: "#dc2626", ERROR_HOVER: "#b91c1c",
}


def pair(color):
    """(light, dark) for a palette colour, as customtkinter widgets want it."""
    return (LIGHT.get(color, color), color)

FONT = "Segoe UI"
FONT_MONO = "Cascadia Mono"
FONT_SIZE = 13
FONT_SIZE_SMALL = 11
FONT_SIZE_TITLE = 15

RADIUS = 8
PAD = 12
PAD_SMALL = 6
ROW_HEIGHT = 32


def apply(ctk, mode="dark"):
    """Put customtkinter into an appearance mode: "dark", "light" or "system"
    (follow Windows).

    Passed the module rather than imported so this file stays importable in
    processes that never build a GUI (the dock's metric poller, for instance).
    """
    ctk.set_appearance_mode(mode if mode in ("dark", "light", "system") else "dark")
    ctk.set_default_color_theme("blue")


def font(ctk, size=FONT_SIZE, weight="normal"):
    return ctk.CTkFont(family=FONT, size=size, weight=weight)
