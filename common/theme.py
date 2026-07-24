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

FONT = "Segoe UI"
FONT_MONO = "Cascadia Mono"
FONT_SIZE = 13
FONT_SIZE_SMALL = 11
FONT_SIZE_TITLE = 15

RADIUS = 8
PAD = 12
PAD_SMALL = 6
ROW_HEIGHT = 32


def apply(ctk):
    """Put customtkinter into the suite's appearance mode.

    Passed the module rather than imported so this file stays importable in
    processes that never build a GUI (the dock's metric poller, for instance).
    """
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")


def font(ctk, size=FONT_SIZE, weight="normal"):
    return ctk.CTkFont(family=FONT, size=size, weight=weight)
