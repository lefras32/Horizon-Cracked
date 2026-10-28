from collections.abc import Callable
import hashlib
import json
import os
import random
import platform as platform_module
from pathlib import Path
import queue
import subprocess
import tarfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import webbrowser
import requests
import minecraft_launcher_lib
import sys

if getattr(sys, 'frozen', False):
    APP_ROOT = Path(sys.executable).resolve().parent
else:
    APP_ROOT = Path(__file__).resolve().parent

HORIZON_DIR = APP_ROOT / "horizon-windows-x64"

COLORS = {
    "background": "#0f111a",
    "sidebar": "#141722",
    "topbar": "#141722",
    "surface": "#1b1f2e",
    "surface_light": "#252b42",
    "border": "#2e3654",
    "text": "#f0f2f5",
    "muted": "#949fb5",
    "accent": "#7c5cff",
    "accent_hover": "#9175ff",
    "disabled": "#2d3247",
    "ok": "#4ade80",
    "warn": "#fbbf24",
    "danger": "#f87171",
}

FONT_FILE = APP_ROOT / "assets" / "pixel.ttf"
PIXEL_FONT_FAMILY = "Silkscreen"
_pixel_font = "Consolas"


def register_pixel_font():
    global _pixel_font
    if os.name == "nt" and FONT_FILE.is_file():
        try:
            import ctypes
            if ctypes.windll.gdi32.AddFontResourceExW(str(FONT_FILE), 0x10, 0):
                _pixel_font = PIXEL_FONT_FAMILY
        except Exception:
            pass


def pixel(size, weight="bold"):
    return (_pixel_font, size, weight)


def minecraft_directory():
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / ".minecraft"
    return Path.home() / "AppData" / "Roaming" / ".minecraft"


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, command, bg_color=COLORS["accent"], fg_color="#ffffff",
                 hover_color=COLORS["accent_hover"], font=("Segoe UI", 10, "bold"), padx=20, pady=10):
        approx_width = max(90, len(text) * 10 + padx * 2)
        approx_height = 30 + pady * 2

        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, cursor="hand2",
                         width=approx_width, height=approx_height)
        self.text = text
        self.command = command
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.hover_color = hover_color
        self.font = font
        self.padx = padx
        self.pady = pady
        self._enabled = True

        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _draw(self, event=None):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 10 or h < 10:
            return

        current_bg = self.current_bg if hasattr(self, "current_bg") else self.bg_color

        radius = 8
        self.create_rounded_rect(2, 2, w - 2, h - 2, radius, fill=current_bg, outline="")
        self.create_text(w / 2, h / 2, text=self.text, fill=self.fg_color, font=self.font)

    def create_rounded_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1,
            x2, y1 + r,
            x2, y2 - r,
            x2, y2,
            x2 - r, y2,
            x1 + r, y2,
            x1, y2,
            x1, y2 - r,
            x1, y1 + r,
            x1, y1
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def _on_enter(self, event):
        if not self._enabled:
            return
        self.current_bg = self.hover_color
        self._draw()

    def _on_leave(self, event):
        if not self._enabled:
            return
        self.current_bg = self.bg_color
        self._draw()

    def _on_click(self, event):
        if self._enabled and self.command:
            self.command()

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        if enabled:
            self.current_bg = self.bg_color
            self.configure(cursor="hand2")
        else:
            self.current_bg = COLORS["disabled"]
            self.configure(cursor="arrow")
        self._draw()

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        if "text" in kwargs:
            self.text = kwargs.pop("text")
        if "state" in kwargs:
            state = kwargs.pop("state")
            self.set_enabled(state != "disabled")
        super().configure(**kwargs)
        self._draw()

    def config(self, cnf=None, **kwargs):
        self.configure(cnf, **kwargs)


HERO_H = 250
PX = 10


class HorizonLauncher:
    def __init__(self, root):
        self.root = root
        self.root.title("Crackrizon Launcher")
        self.root.minsize(960, 640)
        self.root.configure(bg=COLORS["background"])
        self._center(1100, 700)

        self.events = queue.Queue()
        self.busy = False
        self.minecraft_var = tk.StringVar(value=str(minecraft_directory()))
        self.server_var = tk.StringVar()
        self.status_var = tk.StringVar(value="")
        self._hero_job = None
        self._hero_art_w = 0
        self.current_page = None

        try:
            self.lock = json.loads((HORIZON_DIR / "research-lock.json").read_text(encoding="utf-8"))
            self.version = self.lock["minecraft"]["version"]
            self.java_major = self.lock["minecraft"]["java_major"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            messagebox.showerror(
                "Horizon Load Error",
                f"Could not find a valid research-lock.json in the Horizon folder.\n\n{error}",
                parent=root,
            )
            root.destroy()
            return

        self.resources_dir = HORIZON_DIR / ".local" / "horizon-client" / self.version
        bundled_server = HORIZON_DIR / ".local" / "minecraft" / self.version / "jars" / "server.jar"
        if bundled_server.is_file():
            self.server_var.set(str(bundled_server))

        self._setup_styles()
        self._build_interface()
        self._show_page("home")
        self._refresh_state()
        if self._is_prepared():
            self._set_status("Ready to play", "ok")
        else:
            self._set_status("Click 'Prepare' to automatically download and set up the game", "warn")
        self.root.after(100, self._poll_events)

    def _center(self, width, height):
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - width) // 2
        y = (self.root.winfo_screenheight() - height) // 3
        self.root.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")

    @staticmethod
    def _setup_styles():
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Horizon.Horizontal.TProgressbar",
            troughcolor=COLORS["surface"],
            background=COLORS["accent"],
            bordercolor=COLORS["surface"],
            lightcolor=COLORS["accent"],
            darkcolor=COLORS["accent"],
            thickness=3,
        )

    def _build_interface(self):
        self._build_topbar()
        tk.Frame(self.root, bg=COLORS["border"], height=1).pack(fill="x")

        body = tk.Frame(self.root, bg=COLORS["background"])
        body.pack(fill="both", expand=True)
        self._build_sidebar(body)
        tk.Frame(body, bg=COLORS["border"], width=1).pack(side="left", fill="y")

        content = tk.Frame(body, bg=COLORS["background"])
        content.pack(side="left", fill="both", expand=True)
        self.pages = {
            "home": self._build_home(content),
            "settings": self._build_settings(content),
            "log": self._build_log(content),
        }
        self.action_buttons = [
            self.play_button, self.prepare_button,
            self.sounds_button, self.download_libs_button,
        ]

    def _build_topbar(self):
        bar = tk.Frame(self.root, bg=COLORS["topbar"])
        bar.pack(fill="x")

        brand = tk.Frame(bar, bg=COLORS["topbar"], padx=18)
        brand.pack(side="left")
        tk.Label(brand, text="■", font=("Segoe UI", 12), fg=COLORS["accent"],
                 bg=COLORS["topbar"]).pack(side="left", padx=(0, 8))
        tk.Label(brand, text="Horizon", font=pixel(12), fg=COLORS["text"],
                 bg=COLORS["topbar"]).pack(side="left")

        self.tabs = {}
        for key, title in (("home", "Home"), ("settings", "Settings"), ("log", "Log")):
            self._make_tab(bar, key, title)

    def _make_tab(self, parent, key, title):
        holder = tk.Frame(parent, bg=COLORS["topbar"])
        holder.pack(side="left", padx=(8, 0))
        label = tk.Label(holder, text=title, font=("Segoe UI Semibold", 10),
                         fg=COLORS["muted"], bg=COLORS["topbar"], padx=16, pady=15, cursor="hand2")
        label.pack()
        underline = tk.Frame(holder, bg=COLORS["topbar"], height=2)
        underline.pack(fill="x")
        label.bind("<Button-1>", lambda _e: self._show_page(key))
        label.bind("<Enter>", lambda _e: label.configure(fg=COLORS["text"]))
        label.bind("<Leave>", lambda _e: label.configure(
            fg=COLORS["text"] if self.current_page == key else COLORS["muted"]))
        self.tabs[key] = (label, underline)

    def _show_page(self, key):
        self.current_page = key
        for name, page in self.pages.items():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        for name, (label, underline) in self.tabs.items():
            active = name == key
            label.configure(fg=COLORS["text"] if active else COLORS["muted"])
            underline.configure(bg=COLORS["accent"] if active else COLORS["topbar"])

    def _build_sidebar(self, parent):
        side = tk.Frame(parent, bg=COLORS["sidebar"], width=240, padx=14, pady=16)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        tk.Label(side, text="BUILD", font=("Segoe UI", 8, "bold"),
                 fg=COLORS["muted"], bg=COLORS["sidebar"]).pack(anchor="w", pady=(0, 8))

        build = tk.Frame(side, bg=COLORS["surface_light"])
        build.pack(fill="x")
        tk.Frame(build, bg=COLORS["accent"], width=3).pack(side="left", fill="y")
        inner = tk.Frame(build, bg=COLORS["surface_light"], padx=10, pady=10)
        inner.pack(side="left", fill="x", expand=True)

        avatar = tk.Canvas(inner, width=42, height=42, bg=COLORS["sidebar"],
                           highlightthickness=1, highlightbackground=COLORS["border"])
        avatar.create_text(21, 21, text="H", font=pixel(16), fill=COLORS["text"])
        avatar.pack(side="left")

        texts = tk.Frame(inner, bg=COLORS["surface_light"])
        texts.pack(side="left", padx=(10, 0))
        tk.Label(texts, text="Horizon", font=pixel(11), fg=COLORS["text"],
                 bg=COLORS["surface_light"]).pack(anchor="w")
        state_row = tk.Frame(texts, bg=COLORS["surface_light"])
        state_row.pack(anchor="w", pady=(2, 0))
        self.side_dot = tk.Label(state_row, text="■", font=("Segoe UI", 7),
                                 fg=COLORS["warn"], bg=COLORS["surface_light"])
        self.side_dot.pack(side="left")
        self.side_state = tk.Label(state_row, text="", font=("Segoe UI", 9),
                                   fg=COLORS["muted"], bg=COLORS["surface_light"])
        self.side_state.pack(side="left", padx=(5, 0))

        hint = tk.Frame(side, bg=COLORS["surface"], highlightbackground=COLORS["border"],
                        highlightthickness=1, padx=12, pady=10)
        hint.pack(side="bottom", fill="x")
        tk.Label(hint, text="Auto Setup", font=("Segoe UI Semibold", 9),
                 fg=COLORS["text"], bg=COLORS["surface"]).pack(anchor="w")
        tk.Label(
            hint,
            text=f"The launcher automatically downloads Minecraft {self.version} and components without the official client.",
            font=("Segoe UI", 9), fg=COLORS["muted"], bg=COLORS["surface"],
            justify="left", wraplength=188,
        ).pack(anchor="w", pady=(4, 0))

    def _build_home(self, parent):
        page = tk.Frame(parent, bg=COLORS["background"])

        self.hero = tk.Canvas(page, height=HERO_H, bg="#1b1433", highlightthickness=0)
        self.hero.pack(fill="x")

        c = self.hero
        self.badge_bg = c.create_rectangle(0, 0, 0, 0, fill="#0e0c16", outline=COLORS["border"])
        self.badge_marker = c.create_rectangle(0, 0, 0, 0, fill=COLORS["warn"], outline="")
        self.badge_mc = c.create_text(0, 0, anchor="nw", fill=COLORS["text"],
                                      font=("Segoe UI", 9, "bold"),
                                      text=f"MINECRAFT {self.version}   ▪")
        self.badge_state = c.create_text(0, 0, anchor="nw", fill=COLORS["warn"],
                                         font=("Segoe UI", 9, "bold"), text="")
        self.hero_title = c.create_text(0, 0, anchor="nw", text="Crackrizon",
                                        font=pixel(40), fill="#ffffff")
        self.hero_sub = c.create_text(0, 0, anchor="nw", text="bla bla bla pal laich.",
                                      font=("Segoe UI", 12), fill="#d6cff2")
        self.play_button = RoundedButton(
            c, "▶   Play", self._play, bg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"], font=("Segoe UI", 14, "bold"), padx=24, pady=8,
        )
        self.play_window = c.create_window(0, 0, window=self.play_button, anchor="se")
        c.bind("<Configure>", self._on_hero_configure)

        self.progress = ttk.Progressbar(page, mode="indeterminate",
                                        style="Horizon.Horizontal.TProgressbar")
        self.progress.pack(fill="x")

        body = tk.Frame(page, bg=COLORS["background"], padx=28, pady=18)
        body.pack(fill="both", expand=True)

        self.status_label = tk.Label(
            body, textvariable=self.status_var, font=("Segoe UI Semibold", 11),
            fg=COLORS["ok"], bg=COLORS["background"], anchor="w",
        )
        self.status_label.pack(fill="x", pady=(0, 12))

        _card, self.resources_desc = self._card(
            body, "Resources", "",
        )
        self.prepare_button = RoundedButton(_card, "Prepare", self._prepare, padx=16, pady=6)
        self.prepare_button.pack(side="right")

        card, _ = self._card(
            body, "Sounds",
            "Copies sounds from the Minecraft folder (~380 MB). Available after preparing resources.",
        )
        self.sounds_button = RoundedButton(card, "Add", self._prepare_sounds, padx=16, pady=6)
        self.sounds_button.pack(side="right")

        card, _ = self._card(
            body, "Libraries",
            "Additional libraries: Vineflower, Jadx, and Java runtime.",
        )
        self.download_libs_button = RoundedButton(card, "Download", self._download_all_libs, padx=16, pady=6)
        self.download_libs_button.pack(side="right")
        return page

    def _card(self, parent, title, description):
        card = tk.Frame(parent, bg=COLORS["surface"], highlightbackground=COLORS["border"],
                        highlightthickness=1, padx=20, pady=14)
        card.pack(fill="x", pady=(0, 10))
        left = tk.Frame(card, bg=COLORS["surface"])
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=title, font=pixel(11), fg=COLORS["text"],
                 bg=COLORS["surface"]).pack(anchor="w")
        desc = tk.Label(left, text=description, font=("Segoe UI", 10), fg=COLORS["muted"],
                        bg=COLORS["surface"], justify="left", wraplength=520, anchor="w")
        desc.pack(anchor="w", pady=(3, 0))
        return card, desc

    def _build_settings(self, parent):
        page = tk.Frame(parent, bg=COLORS["background"], padx=28, pady=24)
        tk.Label(page, text="Settings", font=pixel(16), fg=COLORS["text"],
                 bg=COLORS["background"]).pack(anchor="w", pady=(0, 16))

        self._path_card(
            page, "Minecraft Directory",
            "Catalog of installed Minecraft with game files.",
            self.minecraft_var, self._choose_minecraft_directory,
        )
        return page

    def _path_card(self, parent, title, hint, variable, browse):
        card = tk.Frame(parent, bg=COLORS["surface"], highlightbackground=COLORS["border"],
                        highlightthickness=1, padx=20, pady=16)
        card.pack(fill="x", pady=(0, 10))
        tk.Label(card, text=title, font=pixel(11), fg=COLORS["text"],
                 bg=COLORS["surface"]).pack(anchor="w")
        tk.Label(card, text=hint, font=("Segoe UI", 10), fg=COLORS["muted"],
                 bg=COLORS["surface"]).pack(anchor="w", pady=(3, 10))
        row = tk.Frame(card, bg=COLORS["surface"])
        row.pack(fill="x")
        entry = tk.Entry(
            row, textvariable=variable, font=("Segoe UI", 10),
            fg=COLORS["text"], bg=COLORS["background"], insertbackground=COLORS["text"],
            relief="flat", highlightthickness=1,
            highlightbackground=COLORS["border"], highlightcolor=COLORS["accent"],
        )
        entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 8))
        RoundedButton(row, "Browse...", browse, padx=16, pady=6).pack(side="right")
        return card

    def _build_log(self, parent):
        page = tk.Frame(parent, bg=COLORS["background"], padx=28, pady=24)
        tk.Label(page, text="Log", font=pixel(16), fg=COLORS["text"],
                 bg=COLORS["background"]).pack(anchor="w", pady=(0, 16))
        panel = tk.Frame(page, bg=COLORS["surface"], highlightbackground=COLORS["border"],
                         highlightthickness=1)
        panel.pack(fill="both", expand=True)
        self.log = tk.Text(
            panel, bg=COLORS["surface"], fg=COLORS["muted"],
            insertbackground=COLORS["text"], selectbackground=COLORS["border"],
            relief="flat", wrap="word", font=("Consolas", 9), padx=14, pady=12,
            state="disabled", borderwidth=0,
        )
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("error", foreground=COLORS["danger"])
        self.log.tag_configure("success", foreground=COLORS["ok"])
        self._append_log("Horizon Launcher is ready.")
        return page

    def _on_hero_configure(self, _event):
        if self._hero_job:
            self.root.after_cancel(self._hero_job)
        self._hero_job = self.root.after(20, self._redraw_hero)

    def _redraw_hero(self):
        self._hero_job = None
        w = self.hero.winfo_width()
        if w < 50:
            return
        if w != self._hero_art_w:
            self.hero.delete("art")
            self._draw_art(w)
            self._hero_art_w = w
        self._layout_hero(w)

    def _draw_art(self, w):
        c = self.hero
        h = HERO_H
        rows = h // PX
        horizon = rows - 5
        cols = w // PX + 1
        sky = ["#1b1433", "#241a45", "#2f2058", "#42296a", "#5d3174",
               "#7b3a79", "#9d477b", "#c25f7b", "#e5806f"]

        def block(col, row, color):
            c.create_rectangle(col * PX, row * PX, col * PX + PX, row * PX + PX,
                               fill=color, outline="", tags="art")

        prev = None
        for r in range(horizon + 1):
            color = sky[min(r * len(sky) // (horizon + 1), len(sky) - 1)]
            if color != prev:
                c.create_rectangle(0, r * PX, w, (horizon + 1) * PX, fill=color,
                                   outline="", tags="art")
                if prev is not None and r > 0:
                    for col in range(0, cols, 2):
                        block(col + (r % 2), r - 1, color)
                prev = color

        for cx, cy, widths in (
                (0.14, 6, (3, 6, 4)), (0.52, 3, (4, 8, 5)), (0.90, 9, (3, 5, 3)),
        ):
            for i, wd in enumerate(widths):
                start = int(cols * cx) - wd // 2
                for k in range(wd):
                    block(start + k, cy + i, "#7e4f90")

        sun_col, sun_row, radius = int(cols * 0.8), horizon - 2, 5
        for dx in range(-radius - 2, radius + 3):
            for dy in range(-radius - 2, radius + 3):
                d2 = dx * dx + dy * dy
                row = sun_row + dy
                if row > horizon:
                    continue
                if d2 <= radius * radius:
                    block(sun_col + dx, row, "#f6b86c")
                elif d2 <= (radius + 2) ** 2 and (dx + dy) % 2 == 0:
                    block(sun_col + dx, row, "#d9707a")

        rng = random.Random(3)
        height = 2
        for col in range(cols):
            height = max(1, min(5, height + rng.choice((-1, 0, 0, 1))))
            for k in range(height):
                block(col, horizon - k, "#2c1b47")

        c.create_rectangle(0, (horizon + 1) * PX - PX, w, h, fill="#150e27", outline="", tags="art")
        height = 1
        for col in range(cols):
            height = max(0, min(2, height + rng.choice((-1, 0, 0, 1))))
            for k in range(height):
                block(col, horizon - k, "#150e27")

        col = rng.randint(2, 6)
        while col < cols:
            for k in range(2):
                block(col, horizon - k, "#0e0a1b")
            for k, half in enumerate((0, 1, 2, 3)):
                for dx in range(-half, half + 1):
                    block(col + dx, horizon - 2 - (3 - k), "#0e0a1b")
            col += rng.randint(8, 15)

        c.tag_lower("art")

    def _layout_hero(self, w):
        c = self.hero
        x0, y0, pad = 28, 34, 10
        c.coords(self.badge_marker, x0 + pad, y0 + pad + 3, x0 + pad + 8, y0 + pad + 11)
        c.coords(self.badge_mc, x0 + pad + 16, y0 + pad)
        bx = c.bbox(self.badge_mc)
        c.coords(self.badge_state, bx[2] + 10, y0 + pad)
        bs = c.bbox(self.badge_state) or (bx[2], 0, bx[2], bx[3])
        c.coords(self.badge_bg, x0, y0, bs[2] + pad, bs[3] + pad)
        c.coords(self.hero_title, 28, 80)
        c.coords(self.hero_sub, 31, 142)
        c.coords(self.play_window, w - 28, HERO_H - 26)

    def _set_status(self, text, tone="ok"):
        color = {
            "ok": COLORS["ok"], "warn": COLORS["warn"],
            "danger": COLORS["danger"], "accent": COLORS["accent_hover"],
        }[tone]
        self.status_var.set(text)
        self.status_label.configure(fg=color)

    def _refresh_state(self):
        prepared = self._is_prepared()
        if self.busy:
            label, color = "Preparing...", COLORS["accent_hover"]
        elif prepared:
            label, color = "Ready to play", COLORS["ok"]
        else:
            label, color = "Preparation needed", COLORS["warn"]

        self.side_state.configure(text=label)
        self.side_dot.configure(fg=color)
        self.hero.itemconfigure(self.badge_state, text=label.upper(), fill=color)
        self.hero.itemconfigure(self.badge_marker, fill=color)
        self.resources_desc.configure(
            text="Prepared and ready to launch." if prepared
            else "Click 'Prepare' to automatically download the game and resources."
        )
        self.prepare_button.configure(text="Update" if prepared else "Prepare")
        w = self.hero.winfo_width()
        if w > 50:
            self._layout_hero(w)

    def _choose_minecraft_directory(self):
        selected = filedialog.askdirectory(
            title="Select Minecraft Directory",
            initialdir=self.minecraft_var.get() or str(Path.home()),
            parent=self.root,
        )
        if selected:
            self.minecraft_var.set(selected)

    def _selected_paths(self):
        minecraft_dir = Path(self.minecraft_var.get().strip()).expanduser()
        minecraft_dir.mkdir(parents=True, exist_ok=True)
        return minecraft_dir.resolve()

    def _download_file(self, url: str, dest: Path, timeout: int = 300) -> None:
        try:
            response = requests.get(url, timeout=timeout, stream=True)
            response.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            self.events.put(("log_success", f"Downloaded: {dest.name}"))
        except requests.exceptions.Timeout:
            raise TimeoutError("Request timed out. Please try again.")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Download error: {e}")

    def _download_verified(self, key: str, title: str, dest: Path) -> None:
        url = self.lock[key]["url"]
        expected = self.lock[key]["sha256"]
        self.events.put(("log", f"Downloading {title} {self.lock[key]['version']}..."))
        self._download_file(url, dest)

        sha = hashlib.sha256()
        with open(dest, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                sha.update(block)
        actual = sha.hexdigest()
        if actual != expected:
            dest.unlink(missing_ok=True)
            raise RuntimeError(f"Hash mismatch for {title}. Expected: {expected}, got: {actual}")
        self.events.put(("log_success", f"{title} downloaded and verified."))

    def _download_vineflower(self, dest: Path) -> None:
        self._download_verified("vineflower", "Vineflower", dest)

    def _download_jadx(self, dest: Path) -> None:
        self._download_verified("jadx_headless_mcp", "Jadx", dest)

    def _get_java_runtime_path(self, platform: str) -> Path:
        java_runtimes = self.lock.get("java_runtimes", {})
        if platform == "mac-os-arm64":
            runtime = java_runtimes.get("mac-os-arm64", {}).get("java-runtime-gamma", {})
        else:
            runtime = java_runtimes.get("linux", {}).get("jre-legacy", {})

        if not runtime:
            return Path()

        manifest_url = runtime.get("manifest_url")
        manifest_sha1 = runtime.get("manifest_sha1")
        if not manifest_url or not manifest_sha1:
            raise RuntimeError("Incomplete runtime info.")

        response = requests.get(manifest_url, timeout=30)
        response.raise_for_status()
        manifest_data = json.loads(response.text)
        actual_sha1 = manifest_data.get("sha1", manifest_sha1)
        if actual_sha1 != manifest_sha1:
            raise RuntimeError("Manifest hash mismatch.")

        version = runtime.get("version")
        dest = HORIZON_DIR / ".local" / "java-runtime" / (
            f"jre-{version}.tar.gz" if version else "jre-legacy.tar.gz")
        self._download_file(manifest_url, dest)
        return dest

    def _extract_archive(self, archive_path: Path, dest_dir: Path) -> Path:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(dest_dir)
        jars = list(dest_dir.rglob("*.jar"))
        if not jars:
            raise RuntimeError("No .jar files found in the Java runtime archive.")
        return jars[0]

    def _check_or_java(self, java_version: int) -> Path:
        system_platform = platform_module.system().lower()
        java_dir = HORIZON_DIR / ".local" / "java-runtime"
        java_exe = java_dir / "bin" / "java.exe" if system_platform == "windows" else None
        if java_exe and java_exe.exists():
            return java_exe

        archive = self._get_java_runtime_path(system_platform)
        if not archive or archive == Path():
            raise RuntimeError("Failed to retrieve Java runtime.")
        return self._extract_archive(archive, java_dir)

    def _check_or_download_vineflower(self) -> Path:
        dest = HORIZON_DIR / ".local" / "vineflower" / "vineflower.jar"
        if not dest.exists():
            self._download_vineflower(dest)
        return dest

    def _check_or_download_jadx(self) -> Path:
        dest = HORIZON_DIR / ".local" / "jadx" / "jadx-headless-mcp.jar"
        if not dest.exists():
            self._download_jadx(dest)
        return dest

    def _download_all_libs(self):
        def task():
            self.events.put(("status", "Downloading libraries..."))
            self._check_or_download_vineflower()
            self._check_or_download_jadx()
            self._check_or_java(self.java_major)
            self.events.put(("status", "All libraries downloaded"))
            self.events.put(("log_success", "Ready to launch."))

        self._start_task(task)

    def _is_prepared(self):
        return (self.resources_dir / "horizon-resources.json").is_file()

    def _play(self):
        if self._is_prepared():
            self._start_task(self._launch_client)
            return

        minecraft_dir = self._selected_paths()
        self._start_task(
            lambda: self._prepare_resources(minecraft_dir, launch=True)
        )

    def _prepare(self):
        minecraft_dir = self._selected_paths()
        self._start_task(
            lambda: self._prepare_resources(minecraft_dir, launch=False)
        )

    def _prepare_sounds(self):
        if not self._is_prepared():
            messagebox.showwarning(
                "Resources Not Prepared",
                "Please click 'Play' or 'Prepare' first.",
                parent=self.root,
            )
            return
        minecraft_dir = self._selected_paths()
        self._start_task(lambda: self._copy_sounds(minecraft_dir))

    def _start_task(self, task: Callable[[], None]) -> None:
        if self.busy:
            return
        self.busy = True
        for button in self.action_buttons:
            button.set_enabled(False)
        self._set_status("Preparing...", "accent")
        self._refresh_state()
        self.progress.start(12)
        threading.Thread(target=self._run_task, args=(task,), daemon=True).start()

    def _run_task(self, task):
        try:
            task()
        except Exception as error:
            self.events.put(("error", str(error)))
        finally:
            self.events.put(("done", None))

    def _ensure_minecraft_and_server(self, minecraft_dir: Path) -> Path:
        self.events.put(("log", f"Checking for Minecraft {self.version}..."))

        version_dir = minecraft_dir / "versions" / self.version
        version_jar = version_dir / f"{self.version}.jar"
        version_json = version_dir / f"{self.version}.json"

        expected_json_url = "https://piston-meta.mojang.com/v1/packages/11f1aefe473eefba0af62e3286c33b692807b2eb/26.3.json"

        # Проверяем, есть ли и jar, и json файлы игры
        if not version_jar.is_file() or not version_json.is_file():
            self.events.put(("log", f"Minecraft {self.version} files missing. Downloading via launcher library..."))
            version_dir.mkdir(parents=True, exist_ok=True)

            cbs = {
                "setStatus": lambda t: self.events.put(("log", f"[{t}]")),
                "setProgress": lambda v: None,
                "setMax": lambda v: None
            }

            minecraft_launcher_lib.install.install_minecraft_version(self.version, minecraft_dir, callback=cbs)


            self.events.put(("log", f"Applying required Horizon manifest for {self.version}..."))
            if version_json.is_file():
                version_json.unlink()
            self._download_file(expected_json_url, version_json)

            self.events.put(("log_success", f"Minecraft {self.version} setup complete!"))
        else:
            self.events.put(("log", f"Minecraft {self.version} is already installed."))

        server_jar_dir = HORIZON_DIR / ".local" / "minecraft" / self.version / "jars"
        server_jar = server_jar_dir / "server.jar"
        if not server_jar.is_file():
            self.events.put(("log", f"Downloading server.jar for version {self.version}..."))
            try:
                manifest_url = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
                manifest = requests.get(manifest_url, timeout=30).json()
                version_url = next((v["url"] for v in manifest["versions"] if v["id"] == self.version), None)
                if version_url:
                    v_data = requests.get(version_url, timeout=30).json()
                    server_url = v_data.get("downloads", {}).get("server", {}).get("url")
                    if server_url:
                        self._download_file(server_url, server_jar)
            except Exception as e:
                raise RuntimeError(f"Failed to automatically download server.jar: {e}")

        return server_jar
    def _prepare_resources(self, minecraft_dir, launch):
        server_jar = self._ensure_minecraft_and_server(minecraft_dir)

        preparer = HORIZON_DIR / "horizon-prepare.exe"
        if not preparer.is_file():
            raise RuntimeError(f"Preparer not found: {preparer}")

        command = [
            str(preparer), "--prepare",
            "--root", str(HORIZON_DIR),
            "--minecraft-dir", str(minecraft_dir),
            "--server-jar", str(server_jar),
        ]
        self.events.put(("log", "Preparing Horizon resources..."))
        self._run_logged_command(command)
        if not self._is_prepared():
            raise RuntimeError("Preparer finished without Horizon resource manifest.")
        self.events.put(("status", "Resources prepared"))
        self.events.put(("log_success", "Preparation completed."))
        if launch:
            self._launch_client()

    def _copy_sounds(self, minecraft_dir):
        preparer = HORIZON_DIR / "horizon-prepare.exe"
        if not preparer.is_file():
            raise RuntimeError(f"Preparer not found: {preparer}")
        command = [
            str(preparer), "sounds",
            "--root", str(HORIZON_DIR),
            "--minecraft-dir", str(minecraft_dir),
        ]
        self.events.put(("log", "Copying and verifying sounds from the Minecraft folder..."))
        self._run_logged_command(command)
        self.events.put(("status", "Sounds added"))
        self.events.put(("log_success", "Sound preparation completed."))

    def _run_logged_command(self, command):
        process = subprocess.Popen(
            command, cwd=HORIZON_DIR,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        if process.stdout is not None:
            for line in process.stdout:
                self.events.put(("log", line.rstrip()))
        return_code = process.wait()
        if return_code:
            raise RuntimeError(
                f"Preparation ended with code {return_code}. Check log for details."
            )

    def _launch_client(self):
        client = HORIZON_DIR / "horizon-client.exe"
        if not client.is_file():
            raise RuntimeError(f"Horizon client not found: {client}")
        if not self._is_prepared():
            raise RuntimeError("Please prepare Horizon resources first.")

        command = [str(client), "--resources", str(self.resources_dir)]
        process = subprocess.Popen(
            command, cwd=HORIZON_DIR,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        self.events.put(("status", "Horizon launched"))
        self.events.put(("log_success", "Game launched."))
        threading.Thread(target=self._watch_client, args=(process,), daemon=True).start()

    def _watch_client(self, process):
        if process.stdout is not None:
            for line in process.stdout:
                self.events.put(("log", line.rstrip()))
        return_code = process.wait()
        if return_code:
            self.events.put(("client_error", return_code))
        else:
            self.events.put(("status", "Game closed"))

    def _append_log(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll_events(self):
        while True:
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break

            if event == "log":
                if value:
                    self._append_log(value)
            elif event == "log_success":
                self._append_log(value, "success")
            elif event == "status":
                self._set_status(value, "ok")
            elif event == "error":
                self._set_status("Action failed", "danger")
                self._append_log(value, "error")
                self._show_page("log")
                messagebox.showerror("Horizon Error", value, parent=self.root)
            elif event == "client_error":
                self._set_status(f"Game exited with code {value}", "danger")
                self._append_log(f"Client finished with code {value}.", "error")
                self._show_page("log")
            elif event == "done":
                self.busy = False
                self.progress.stop()
                for button in self.action_buttons:
                    button.set_enabled(True)
                self._refresh_state()

        self.root.after(100, self._poll_events)


if __name__ == "__main__":
    window = tk.Tk()

    icon_path = APP_ROOT / "assets" / "icon.ico"
    if icon_path.is_file():
        window.iconbitmap(str(icon_path))

    HorizonLauncher(window)
    window.mainloop()