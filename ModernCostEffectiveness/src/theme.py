#!/usr/bin/env python3
"""Light/dark palettes, live font scaling, and ttk styling.

Everything visual in the app resolves through here so the theme can flip
at runtime: ttk chrome via styles, tk widgets via palette lookups at build
(the main views rebuild on every refresh), and fonts via shared named
tk.font.Font objects (resizing one rescales every widget using it).
"""
from tkinter import ttk

THEMES = {
    "light": {
        "bg": "#ffffff", "fg": "#333333", "muted": "#666666",
        "faint": "#767676", "axis": "#555555",
        "accent": "#0a6e0a", "link": "#1a5fb4",
        "canvas": "#ffffff",
        "row_done": "#dff5df", "row_need": "#fde8e8",
        "row_section": "#d9e6f2", "row_swapped": "#e3f0fc",
        "meta_done": "#e2f2e2", "meta_partial": "#fff6df",
        "meta_plain": "#ffffff", "meta_sel": "#d9ecfc",
        "trend_up": "#dff5df", "trend_down": "#fde8e8",
        "trend_new": "#e7f3ff", "trend_out": "#f0f0f0",
        "trend_flat": "#ffffff",
        "bar_done": "#4e79a7", "bar_idle": "#bab0ac",
        "tree_bg": "#ffffff", "tree_fg": "#333333",
        "tree_field": "#ffffff", "tree_sel_bg": "#cfe4f7",
        "tree_sel_fg": "#000000",
        "head_bg": "#f0f0f0", "head_fg": "#333333",
        "tab_bg": "#f0f0f0", "tab_fg": "#333333", "tab_sel": "#ffffff",
        "btn_bg": "#f0f0f0", "btn_fg": "#000000",
        "entry_bg": "#ffffff", "entry_fg": "#000000",
    },
    "dark": {
        "bg": "#1e1e1e", "fg": "#f2f2f2", "muted": "#c6c6c6",
        "faint": "#9a9a9a", "axis": "#e0e0e0",
        "accent": "#8fe08f", "link": "#8ab8ff",
        "canvas": "#1e1e1e",
        "row_done": "#1d3a24", "row_need": "#4a2222",
        "row_section": "#233448", "row_swapped": "#1f3a52",
        "meta_done": "#1d3a24", "meta_partial": "#4a3d16",
        "meta_plain": "#1e1e1e", "meta_sel": "#2f4b6e",
        "trend_up": "#1d3a24", "trend_down": "#4a2222",
        "trend_new": "#1f3a52", "trend_out": "#2b2b2b",
        "trend_flat": "#1e1e1e",
        "bar_done": "#4e79a7", "bar_idle": "#4a4a4a",
        "tree_bg": "#1e1e1e", "tree_fg": "#ececec",
        "tree_field": "#1e1e1e", "tree_sel_bg": "#2f5b8f",
        "tree_sel_fg": "#ffffff",
        "head_bg": "#2b2b2b", "head_fg": "#f0f0f0",
        "tab_bg": "#2b2b2b", "tab_fg": "#ededed", "tab_sel": "#1e1e1e",
        "btn_bg": "#333333", "btn_fg": "#f2f2f2",
        "entry_bg": "#2b2b2b", "entry_fg": "#f0f0f0",
    },
}

VALID_THEMES = tuple(THEMES)
_current = "light"


def set_theme(name: str) -> str:
    """Select the active palette. Returns the name actually set."""
    global _current
    _current = name if name in THEMES else "light"
    return _current


def current() -> str:
    return _current


def palette() -> dict:
    return THEMES[_current]


def C(key: str) -> str:
    """A palette color by key (falls back to light)."""
    return THEMES[_current].get(key, THEMES["light"].get(key, "#ffffff"))


# ---- live-scaling named fonts ----
BASE_SIZES = {"xs": 8, "s": 9, "m": 10, "l": 11}
FONTS = {}
_scale = 1.0


def init_fonts(scale: float = 1.0) -> None:
    """Create (or rescale) the shared named fonts. Needs a live Tk root."""
    import tkinter.font as tkfont
    set_scale(scale)
    defs = [("xs", False), ("s", False), ("m", False), ("l", False),
            ("xs_b", True), ("s_b", True), ("m_b", True), ("l_b", True)]
    for name, bold in defs:
        base = BASE_SIZES[name.rstrip("_b")]
        size = max(6, round(base * _scale))
        if name in FONTS:
            FONTS[name].configure(size=size)
        else:
            FONTS[name] = tkfont.Font(family="Segoe UI", size=size,
                                      weight="bold" if bold else "normal")


def F(name: str):
    """A shared named font for font= options (updates live on rescale)."""
    return FONTS[name]


def set_scale(scale: float) -> float:
    """Resize every shared font. Returns the clamped scale actually used."""
    global _scale
    from settings import MIN_SCALE, MAX_SCALE  # local import: no cycle
    _scale = min(MAX_SCALE, max(MIN_SCALE, float(scale)))
    for name, base in (("xs", 8), ("s", 9), ("m", 10), ("l", 11),
                       ("xs_b", 8), ("s_b", 9), ("m_b", 10), ("l_b", 11)):
        if name in FONTS:
            FONTS[name].configure(size=max(6, round(base * _scale)))
    return _scale


def get_scale() -> float:
    return _scale


# Row-tag palettes for the deck popup + trend trees.
DECK_TAGS = {"done": "row_done", "need": "row_need",
             "section": "row_section", "swapped": "row_swapped"}
TREND_TAGS = {"up": "trend_up", "down": "trend_down", "new": "trend_new",
              "out": "trend_out", "flat": "trend_flat"}


def apply_tree_tags(tree, mapping) -> None:
    """(Re)color a Treeview's row tags from the active palette."""
    for tag, key in mapping.items():
        try:
            tree.tag_configure(tag, background=C(key))
        except Exception:
            pass


def _cfg(style, widget, **options) -> None:
    """style.configure, one option at a time so an unknown option on some
    Tk build can't abort the whole widget's styling."""
    for opt, val in options.items():
        try:
            style.configure(widget, **{opt: val})
        except Exception:
            pass


def _map(style, widget, **maps) -> None:
    for state, opts in maps.items():
        try:
            style.map(widget, **{state: opts})
        except Exception:
            pass


def apply(root, name: str) -> str:
    """Switch theme live: ttk styles now, palette for everything built after.
    Returns the active theme name."""
    set_theme(name)
    p = palette()
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    _cfg(style, ".", background=p["bg"], foreground=p["fg"],
         font=F("s"), troughcolor=p["bg"])
    _cfg(style, "TFrame", background=p["bg"])
    _cfg(style, "TLabel", background=p["bg"], foreground=p["fg"], font=F("s"))
    _cfg(style, "TButton", background=p["btn_bg"], foreground=p["btn_fg"],
         bordercolor=p["muted"], focuscolor=p["btn_bg"], padding=4, font=F("s"))
    _map(style, "TButton", active=[("background", p["tab_bg"])])
    _cfg(style, "TNotebook", background=p["bg"], bordercolor=p["bg"])
    _cfg(style, "TNotebook.Tab", background=p["tab_bg"], foreground=p["tab_fg"],
         padding=(10, 4), font=F("s"))
    _map(style, "TNotebook.Tab",
         selected=[("background", p["tab_sel"]), ("foreground", p["fg"])])
    _cfg(style, "Treeview", background=p["tree_bg"], foreground=p["tree_fg"],
         fieldbackground=p["tree_field"], font=F("s"), bordercolor=p["bg"])
    _map(style, "Treeview",
         selected=[("background", p["tree_sel_bg"]), ("foreground", p["tree_sel_fg"])])
    _cfg(style, "Treeview.Heading", background=p["head_bg"],
         foreground=p["head_fg"], font=F("s_b"))
    _cfg(style, "TEntry", fieldbackground=p["entry_bg"],
         foreground=p["entry_fg"], insertcolor=p["entry_fg"], font=F("s"))
    _cfg(style, "TCheckbutton", background=p["bg"], foreground=p["fg"], font=F("s"))
    _cfg(style, "TRadiobutton", background=p["bg"], foreground=p["fg"], font=F("s"))
    _cfg(style, "TProgressbar", background=p["bar_done"], troughcolor=p["bg"],
         bordercolor=p["bg"])
    _cfg(style, "TScrollbar", background=p["btn_bg"], troughcolor=p["bg"],
         bordercolor=p["bg"], arrowcolor=p["muted"])
    _cfg(style, "TSeparator", background=p["muted"])
    _cfg(style, "TScale", background=p["bg"], troughcolor=p["tab_bg"],
         foreground=p["fg"], font=F("s"))
    _cfg(style, "TMenubutton", background=p["btn_bg"], foreground=p["btn_fg"],
         font=F("s"))
    _cfg(style, "TOptionMenu", background=p["btn_bg"], foreground=p["btn_fg"])
    try:
        root.configure(bg=p["bg"])
    except Exception:
        pass
    return _current
