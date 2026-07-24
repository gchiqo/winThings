"""The Ctrl+Shift+V history picker.

Built on ttk.Treeview rather than a stack of CTk widgets: filtering happens on
every keystroke, and rebuilding a few hundred CTk frames per keypress is
visibly slow while a Treeview repopulates in a couple of milliseconds. Treeview
also gives per-row thumbnails and arrow-key navigation for free.

The window is created once and hidden between uses, so opening it is just a
deiconify plus a query. Keyboard focus stays in the search entry the whole time
- arrow keys are forwarded to the list - so the user can open the popup and
immediately start typing without ever clicking anything.
"""

import io
import time
from tkinter import ttk

import customtkinter as ctk
from PIL import Image, ImageTk

from common import theme

ROW_HEIGHT = 30
THUMB_MAX = 22
PREVIEW_CHARS = 120


def _age(stamp):
    delta = max(0, time.time() - stamp)
    if delta < 60:
        return "now"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h"
    return f"{int(delta // 86400)}d"


def _one_line(text):
    flat = " ".join(str(text).split())
    return flat[:PREVIEW_CHARS] + ("…" if len(flat) > PREVIEW_CHARS else "")


class Popup(ctk.CTkToplevel):
    WIDTH = 620
    HEIGHT = 440

    def __init__(self, master, store, on_choose):
        super().__init__(master, fg_color=theme.BG)
        self.store = store
        self.on_choose = on_choose
        self._thumbs = {}   # entry id -> PhotoImage, kept alive for Tk
        self._rows = {}     # tree iid -> entry id

        self.title("Clipboard")
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}")
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self.hide)
        self.withdraw()

        self.search = ctk.CTkEntry(
            self, placeholder_text="Search clipboard history…",
            fg_color=theme.BG_ELEVATED, border_color=theme.BORDER,
            text_color=theme.FG, height=36, font=theme.font(ctk),
        )
        self.search.pack(fill="x", padx=theme.PAD, pady=(theme.PAD, theme.PAD_SMALL))

        holder = ctk.CTkFrame(self, fg_color=theme.BG_ELEVATED, corner_radius=theme.RADIUS)
        holder.pack(fill="both", expand=True, padx=theme.PAD, pady=0)

        self._style_tree()
        self.tree = ttk.Treeview(
            holder, columns=("age",), show="tree", style="Clip.Treeview",
            selectmode="browse",
        )
        self.tree.column("#0", width=self.WIDTH - 110, stretch=True)
        self.tree.column("age", width=54, anchor="e", stretch=False)
        self.tree.tag_configure("pinned", foreground=theme.ACCENT)
        bar = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview,
                            style="Clip.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y", pady=4)
        self.tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)

        self.hint = ctk.CTkLabel(
            self, text="", anchor="w", text_color=theme.FG_FAINT,
            font=theme.font(ctk, theme.FONT_SIZE_SMALL),
        )
        self.hint.pack(fill="x", padx=theme.PAD + 2, pady=(2, theme.PAD_SMALL))

        self._bind_keys()

    # -- appearance -------------------------------------------------------
    def _style_tree(self):
        style = ttk.Style(self)
        style.theme_use("clam")  # the only stock theme that honours our colours
        style.configure(
            "Clip.Treeview", background=theme.BG_ELEVATED,
            fieldbackground=theme.BG_ELEVATED, foreground=theme.FG,
            rowheight=ROW_HEIGHT, borderwidth=0, font=(theme.FONT, theme.FONT_SIZE),
        )
        style.map(
            "Clip.Treeview",
            background=[("selected", theme.ACCENT)],
            foreground=[("selected", "#ffffff")],
        )
        style.layout("Clip.Treeview", [("Clip.Treeview.treearea", {"sticky": "nswe"})])
        style.configure(
            "Clip.Vertical.TScrollbar", background=theme.BG_HOVER,
            troughcolor=theme.BG_ELEVATED, bordercolor=theme.BG_ELEVATED,
            arrowcolor=theme.FG_DIM, borderwidth=0,
        )

    # -- keys -------------------------------------------------------------
    def _bind_keys(self):
        self.search.bind("<KeyRelease>", self._on_type)
        for widget in (self, self.search, self.tree):
            widget.bind("<Escape>", lambda e: self.hide())
            widget.bind("<Return>", lambda e: self._choose())
            widget.bind("<Down>", lambda e: self._move(1))
            widget.bind("<Up>", lambda e: self._move(-1))
            widget.bind("<Next>", lambda e: self._move(10))
            widget.bind("<Prior>", lambda e: self._move(-10))
            widget.bind("<Control-p>", lambda e: self._toggle_pin())
            widget.bind("<Control-Delete>", lambda e: self._delete())
        self.tree.bind("<Double-Button-1>", lambda e: self._choose())

    def _on_type(self, event):
        # Navigation keys are handled by their own bindings; don't re-query.
        if event.keysym in ("Up", "Down", "Return", "Escape", "Next", "Prior"):
            return
        self.refresh()

    # -- data -------------------------------------------------------------
    def refresh(self, keep_index=None):
        query = self.search.get()
        rows = self.store.search(query, limit=250)
        self.tree.delete(*self.tree.get_children())
        self._rows.clear()
        for row in rows:
            marker = "* " if row["pinned"] else "  "
            iid = self.tree.insert(
                "", "end", text=marker + _one_line(row["preview"]),
                values=(_age(row["used"]),),
                image=self._thumb(row), tags=("pinned",) if row["pinned"] else (),
            )
            self._rows[iid] = row["id"]
        items = self.tree.get_children()
        if items:
            index = 0 if keep_index is None else max(0, min(len(items) - 1, keep_index))
            self.tree.selection_set(items[index])
            self.tree.see(items[index])
        total, pinned = self.store.count()
        self.hint.configure(
            text=f"{len(rows)} shown · {total} stored · {pinned} pinned    "
                 f"Enter paste · Ctrl+P pin · Ctrl+Del remove · Esc close"
        )

    def _thumb(self, row):
        if row["kind"] != "image" or not row["thumb"]:
            return ""
        cached = self._thumbs.get(row["id"])
        if cached is None:
            img = Image.open(io.BytesIO(row["thumb"]))
            img.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)
            cached = ImageTk.PhotoImage(img, master=self)
            self._thumbs[row["id"]] = cached
        return cached

    def _selected(self):
        chosen = self.tree.selection()
        return self._rows.get(chosen[0]) if chosen else None

    def _index(self):
        chosen = self.tree.selection()
        items = self.tree.get_children()
        return items.index(chosen[0]) if chosen else 0

    # -- actions ----------------------------------------------------------
    def _move(self, delta):
        items = self.tree.get_children()
        if not items:
            return "break"
        index = max(0, min(len(items) - 1, self._index() + delta))
        self.tree.selection_set(items[index])
        self.tree.see(items[index])
        return "break"

    def _choose(self):
        entry_id = self._selected()
        if entry_id is None:
            return "break"
        self.hide()
        self.on_choose(entry_id)
        return "break"

    def _toggle_pin(self):
        entry_id = self._selected()
        if entry_id is not None:
            self.store.toggle_pin(entry_id)
            self.refresh(keep_index=self._index())
        return "break"

    def _delete(self):
        entry_id = self._selected()
        if entry_id is not None:
            index = self._index()
            self.store.delete(entry_id)
            self._thumbs.pop(entry_id, None)
            self.refresh(keep_index=index)
        return "break"

    # -- show/hide --------------------------------------------------------
    def _center(self):
        x = (self.winfo_screenwidth() - self.WIDTH) // 2
        y = (self.winfo_screenheight() - self.HEIGHT) // 3
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def show(self):
        self._center()
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)
        self.search.delete(0, "end")
        self.refresh()
        self._take_focus()

    def _take_focus(self):
        """Tk's focus_force only works once Windows lets us be foreground."""
        try:
            import win32gui

            frame = self.wm_frame()
            hwnd = int(frame, 16) if frame else self.winfo_id()
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        self.focus_force()
        self.search.focus_set()

    def hide(self):
        self.withdraw()
        return "break"
