#!/usr/bin/env python3
"""
42 Dashboard — Panel Indicator (Standalone, pas une extension GNOME)
Affiche le logtime + badge amis dans le panel GNOME + popup dashboard.
Usage: python3 logtime42.py
"""

import os
import sys

# ── TYPELIB HACK (sans sudo) ─────────────────────────────────────────────
# On ajoute le dossier .typelibs/ pour charger AyatanaAppIndicator3
_typelib_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".typelibs")
_existing = os.environ.get("GI_TYPELIB_PATH", "")
os.environ["GI_TYPELIB_PATH"] = f"{_typelib_dir}:{_existing}" if _existing else _typelib_dir

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('WebKit2', '4.1')
gi.require_version('AyatanaAppIndicator3', '0.1')

from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, WebKit2, AyatanaAppIndicator3

import threading
import time
import json
import calendar
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from config import DEFAULT_CONFIG, CONFIG_PATH, COOKIE_PATH, PORT
from flask import Flask, jsonify, request, render_template_string
import requests as req_lib

# ── FLASK APP ────────────────────────────────────────────────────────────
flask_app = Flask(__name__)

# ── CONFIG ───────────────────────────────────────────────────────────────
def load_config():
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))

def load_cookie():
    return COOKIE_PATH.read_text().strip() if COOKIE_PATH.exists() else None

# ── API 42 ───────────────────────────────────────────────────────────────
_tok = {"v": None, "exp": 0}

def get_token(uid, secret):
    if _tok["v"] and time.time() < _tok["exp"] - 60:
        return _tok["v"]
    try:
        r = req_lib.post("https://api.intra.42.fr/oauth/token", json={
            "grant_type": "client_credentials",
            "client_id": uid, "client_secret": secret
        }, timeout=15)
        d = r.json()
        _tok["v"] = d["access_token"]
        _tok["exp"] = time.time() + d.get("expires_in", 7200)
        return _tok["v"]
    except:
        return None

def api_get(path, token, params=None, cookie=None):
    headers = {"Authorization": f"Bearer {token}"}
    if cookie:
        headers["Cookie"] = f"_intra_42_session_production={cookie}"
    try:
        r = req_lib.get(f"https://api.intra.42.fr/v2/{path}",
                        headers=headers, params=params, timeout=15)
        return r.json() if r.status_code == 200 else None
    except:
        return None

# ── CALCULS ──────────────────────────────────────────────────────────────
def month_range():
    n = datetime.now(timezone.utc)
    s = datetime(n.year, n.month, 1, tzinfo=timezone.utc).isoformat()
    last = calendar.monthrange(n.year, n.month)[1]
    e = datetime(n.year, n.month, last, 23, 59, 59, tzinfo=timezone.utc).isoformat()
    return s, e

def logtime_ms(locs):
    now = datetime.now(timezone.utc)
    total = 0
    for l in locs:
        b = datetime.fromisoformat(l["begin_at"].replace("Z", "+00:00"))
        e = datetime.fromisoformat(l["end_at"].replace("Z", "+00:00")) if l.get("end_at") else now
        total += (e - b).total_seconds() * 1000
    return total

def today_ms(locs):
    now = datetime.now(timezone.utc)
    today = now.date()
    total = 0
    for l in locs:
        b = datetime.fromisoformat(l["begin_at"].replace("Z", "+00:00"))
        if b.date() != today:
            continue
        e = datetime.fromisoformat(l["end_at"].replace("Z", "+00:00")) if l.get("end_at") else now
        total += (e - b).total_seconds() * 1000
    return total

def hm(ms):
    return int(ms // 3_600_000), int((ms % 3_600_000) // 60_000)

def daily_target(target_h, current_ms, working_days):
    rem = target_h - current_ms / 3_600_000
    if rem <= 0:
        return None, None
    now = datetime.now()
    last = calendar.monthrange(now.year, now.month)[1]
    work = sum(1 for d in range(now.day + 1, last + 1)
               if working_days[(datetime(now.year, now.month, d).weekday() + 1) % 7])
    daily = rem / work if work else rem
    return int(daily), int((daily - int(daily)) * 60)

def cal_data(locs):
    now = datetime.now(timezone.utc)
    days = {}
    for l in locs:
        b = datetime.fromisoformat(l["begin_at"].replace("Z", "+00:00"))
        e = datetime.fromisoformat(l["end_at"].replace("Z", "+00:00")) if l.get("end_at") else now
        days[b.day] = days.get(b.day, 0) + (e - b).total_seconds() / 3600
    return days

# ── FLASK ROUTES ─────────────────────────────────────────────────────────
@flask_app.route("/")
def index():
    return render_template_string(HTML)

@flask_app.route("/api/data")
def api_data():
    cfg = load_config()
    uid, secret, username = cfg["api_uid"], cfg["api_secret"], cfg["username"]
    if not uid or not secret or not username:
        return jsonify({"error": "config"})
    token = get_token(uid, secret)
    if not token:
        return jsonify({"error": "token"})

    s, e = month_range()
    locs = api_get(f"users/{username}/locations", token,
                   {"range[begin_at]": f"{s},{e}", "per_page": 100}) or []
    total = logtime_ms(locs)
    tod = today_ms(locs)
    h, m = hm(total)
    th, tm = hm(tod)
    gift = cfg["gift_days"]
    target_h = max(0, 154 - gift * 7)
    dh, dm = daily_target(target_h, total, cfg["working_days"])
    pct = min(100, total / (target_h * 3_600_000) * 100) if target_h else 100

    me = api_get(f"users/{username}", token) or {}

    cookie = load_cookie()
    cookie_status = "missing"
    scales_out = []
    if cookie:
        raw = api_get(f"users/{username}/scale_teams", token,
                      {"filter[future]": "true"}, cookie=cookie)
        if raw is None or (isinstance(raw, dict) and raw.get("error")):
            cookie_status = "expired"
        else:
            cookie_status = "ok"
            for sc in (raw or [])[:3]:
                dt = datetime.fromisoformat(sc["begin_at"].replace("Z", "+00:00")).astimezone()
                corr = (sc.get("corrector") or {}).get("login", "")
                is_c = corr == username
                scales_out.append({
                    "date": dt.strftime("%d/%m à %Hh%M"),
                    "label": "💪 Corriger" if is_c else "🎓 Être corrigé",
                    "type": "corriger" if is_c else "corrige",
                    "project": (sc.get("scale") or {}).get("name", "Projet"),
                })

    return jsonify({
        "h": h, "m": m, "th": th, "tm": tm,
        "dh": dh, "dm": dm,
        "pct": round(pct, 1), "target_h": target_h,
        "wallet": me.get("wallet", "-"),
        "eval": me.get("correction_point", "-"),
        "scales": scales_out,
        "cookie_status": cookie_status,
        "my_calendar": cal_data(locs),
        "month_days": calendar.monthrange(datetime.now().year, datetime.now().month)[1],
        "current_day": datetime.now().day,
    })

@flask_app.route("/api/friends")
def api_friends():
    cfg = load_config()
    token = get_token(cfg["api_uid"], cfg["api_secret"])
    if not token:
        return jsonify([])
    s, e = month_range()
    out = []
    for login in cfg["friends"]:
        locs = api_get(f"users/{login}/locations", token,
                       {"range[begin_at]": f"{s},{e}", "per_page": 100}) or []
        ms = logtime_ms(locs)
        fh, fm = hm(ms)
        active = next((l for l in locs if not l.get("end_at")), None)
        u = api_get(f"users/{login}", token) or {}
        cursus = next((c for c in u.get("cursus_users", [])
                       if c.get("cursus", {}).get("slug") == "42cursus"), None)
        out.append({
            "login": login, "h": fh, "m": fm,
            "online": bool(active),
            "host": active["host"] if active else None,
            "wallet": u.get("wallet", "-"),
            "eval": u.get("correction_point", "-"),
            "level": round(cursus["level"], 2) if cursus else "-",
            "pool": u.get("pool_year", "-"),
            "avatar": (u.get("image") or {}).get("versions", {}).get("small", ""),
            "calendar": cal_data(locs),
        })
        time.sleep(0.3)
    out.sort(key=lambda x: (not x["online"], x["login"]))
    return jsonify(out)

@flask_app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        cfg = load_config()
        cfg.pop("api_secret", None)
        return jsonify(cfg)
    data = request.json or {}
    cfg = load_config()
    for k in ["username", "api_uid", "api_secret", "gift_days", "friends", "working_days"]:
        if k in data:
            cfg[k] = data[k]
    save_config(cfg)
    _tok["v"] = None
    return jsonify({"ok": True})

@flask_app.route("/api/friend/add", methods=["POST"])
def add_friend():
    login = (request.json or {}).get("login", "").strip()
    if not login:
        return jsonify({"ok": False})
    cfg = load_config()
    if login not in cfg["friends"]:
        cfg["friends"].append(login)
    save_config(cfg)
    return jsonify({"ok": True})

@flask_app.route("/api/friend/remove", methods=["POST"])
def remove_friend():
    login = (request.json or {}).get("login", "").strip()
    cfg = load_config()
    cfg["friends"] = [f for f in cfg["friends"] if f != login]
    save_config(cfg)
    return jsonify({"ok": True})

@flask_app.route("/api/launch_cookie")
def launch_cookie():
    candidates = [
        Path(__file__).parent / "capture_cookies.py",
    ]
    for p in candidates:
        if p.exists():
            subprocess.Popen(["python3", str(p)])
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "capture_cookies.py introuvable"})

@flask_app.route("/api/style", methods=["GET", "POST"])
def api_style():
    if request.method == "GET":
        cfg = load_config()
        return jsonify({
            "accent_color": cfg.get("accent_color", "#2ed573"),
            "font_size": cfg.get("font_size", 14),
            "popup_width": cfg.get("popup_width", 420),
            "popup_height": cfg.get("popup_height", 700),
            "badge_size": cfg.get("badge_size", 32),
        })
    data = request.json or {}
    cfg = load_config()
    for k in ["accent_color", "font_size", "popup_width", "popup_height", "badge_size"]:
        if k in data:
            cfg[k] = data[k]
    save_config(cfg)
    return jsonify({"ok": True})


# ── ICONS (SVG → PNG for AppIndicator) ──────────────────────────────────
_ICON_DIR = Path(__file__).parent / ".icons"
_ICON_DIR.mkdir(exist_ok=True)


def _write_svg_icon(name, svg_content):
    """Write an SVG icon and convert to PNG for AppIndicator compatibility."""
    svg_path = _ICON_DIR / f"{name}.svg"
    svg_path.write_text(svg_content)
    return str(svg_path)


def create_badge_icon(count=0, size=None, color=None):
    """Create the tray icon: green circle with friends online count, always visible."""
    if size is None:
        try:
            cfg = load_config()
            size = cfg.get("badge_size", 32)
        except:
            size = 32
    if color is None:
        try:
            cfg = load_config()
            color = cfg.get("accent_color", "#2ed573")
        except:
            color = "#2ed573"
    r = size // 2
    r2 = r - 2
    ty = int(size * 0.69)
    fs = int(size * 0.56)
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">
  <circle cx="{r}" cy="{r}" r="{r-1}" fill="{color}"/>
  <circle cx="{r}" cy="{r}" r="{r2}" fill="none" stroke="rgba(0,0,0,0.15)" stroke-width="1.5"/>
  <text x="{r}" y="{ty}" text-anchor="middle" font-family="sans-serif" font-weight="bold"
        font-size="{fs}" fill="#000">{count}</text>
</svg>"""
    return _write_svg_icon("badge", svg)


# ── GTK POPUP WINDOW ────────────────────────────────────────────────────
class DashboardPopup(Gtk.Window):
    def __init__(self):
        super().__init__(title="42 Dashboard")
        cfg = load_config()
        self.set_default_size(cfg.get("popup_width", 420), cfg.get("popup_height", 700))
        self.set_decorated(True)
        self.set_resizable(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)

        settings = Gtk.Settings.get_default()
        settings.set_property("gtk-application-prefer-dark-theme", True)

        css = Gtk.CssProvider()
        css.load_from_data(b"window { background-color: #0d0d14; }")
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.webview = WebKit2.WebView()
        web_settings = self.webview.get_settings()
        web_settings.set_enable_javascript(True)

        rgba = Gdk.RGBA()
        rgba.parse("rgba(13,13,20,1)")
        self.webview.set_background_color(rgba)

        scrolled = Gtk.ScrolledWindow()
        scrolled.add(self.webview)
        self.add(scrolled)

        self.connect("delete-event", self._on_close)
        self.webview.connect("notify::title", self._on_title_changed)

        self._logtime_callback = None
        self._loaded = False

    def _on_close(self, widget, event):
        self.hide()
        return True

    def _on_title_changed(self, webview, param):
        title = webview.get_title()
        if title and title.startswith("42|"):
            parts = title.split("|")
            if len(parts) >= 3 and self._logtime_callback:
                self._logtime_callback(parts[1], parts[2])

    def load_dashboard(self):
        if not self._loaded:
            self.webview.load_uri(f"http://127.0.0.1:{PORT}")
            self._loaded = True

    def toggle(self):
        if self.get_visible():
            self.hide()
        else:
            self.load_dashboard()
            self.show_all()
            screen = self.get_screen()
            if screen:
                display = Gdk.Display.get_default()
                monitor = display.get_primary_monitor() or display.get_monitor(0)
                if monitor:
                    geo = monitor.get_geometry()
                    w, h = self.get_size()
                    self.move(geo.x + geo.width - w - 10, geo.y + 32)
            self.webview.reload()

    def open_settings(self):
        if not self.get_visible():
            self.toggle()
            GLib.timeout_add(1000, lambda: self.webview.run_javascript("openSettings()", None, None, None) or False)
        else:
            self.webview.run_javascript("openSettings()", None, None, None)


# ── NATIVE GTK STYLE PREFERENCES DIALOG ──────────────────────────────────
_COLOR_PRESETS = [
    ("🟢 Vert",    "#2ed573"),
    ("🔵 Bleu",    "#54a0ff"),
    ("🟣 Violet",  "#a855f7"),
    ("🟠 Orange",  "#ff9f43"),
    ("🔴 Rouge",   "#ff4757"),
    ("🩵 Cyan",    "#00d2d3"),
    ("🩷 Rose",    "#ff6b81"),
    ("🟡 Or",      "#feca57"),
]

class PreferencesDialog(Gtk.Dialog):
    """Visual style customization: colors, sizes, fonts."""
    def __init__(self, parent=None):
        super().__init__(title="🎨 Personnalisation — 42 Dashboard", transient_for=parent)
        self.set_default_size(400, 440)
        self.set_modal(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)

        css = Gtk.CssProvider()
        css.load_from_data(b"""
            dialog, window { background-color: #12121f; }
            label { color: #ccc; }
            .section-label { color: #8b8b9b; font-weight: bold; font-size: 10px; }
            button.save-btn { background: #2ed573; color: #000; font-weight: bold;
                              border-radius: 8px; padding: 8px 20px; border: none; }
            button.save-btn:hover { background: #1aab55; }
            button.color-btn { border-radius: 50%; min-width: 36px; min-height: 36px;
                               border: 2px solid transparent; }
            button.color-btn.selected { border-color: #fff; }
            spinbutton { background: #1a1a2e; color: #fff; border: 1px solid #252540;
                         border-radius: 6px; }
            scale { color: #2ed573; }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        content = self.get_content_area()
        content.set_spacing(8)
        content.set_margin_start(20)
        content.set_margin_end(20)
        content.set_margin_top(16)
        content.set_margin_bottom(16)

        cfg = load_config()
        current_color = cfg.get("accent_color", "#2ed573")

        # ── Couleur d'accent ──
        sec1 = Gtk.Label(label="COULEUR D'ACCENT")
        sec1.get_style_context().add_class("section-label")
        sec1.set_halign(Gtk.Align.START)
        content.pack_start(sec1, False, False, 4)

        colors_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._color_btns = []
        self._selected_color = current_color
        for name, hex_color in _COLOR_PRESETS:
            btn = Gtk.Button(label="")
            btn.get_style_context().add_class("color-btn")
            # Set background color via inline CSS
            btn_css = Gtk.CssProvider()
            btn_css.load_from_data(f"button {{ background: {hex_color}; }}".encode())
            btn.get_style_context().add_provider(btn_css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
            btn.set_tooltip_text(name)
            if hex_color.lower() == current_color.lower():
                btn.get_style_context().add_class("selected")
            btn.connect("clicked", self._on_color_click, hex_color)
            colors_box.pack_start(btn, False, False, 0)
            self._color_btns.append((btn, hex_color))
        content.pack_start(colors_box, False, False, 4)

        # ── Taille police ──
        sec2 = Gtk.Label(label="TAILLE DE POLICE")
        sec2.get_style_context().add_class("section-label")
        sec2.set_halign(Gtk.Align.START)
        content.pack_start(sec2, False, False, 4)

        font_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        font_box.pack_start(Gtk.Label(label="Petit"), False, False, 0)
        adj_font = Gtk.Adjustment(value=cfg.get("font_size", 14), lower=10, upper=22, step_increment=1)
        self.scale_font = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj_font)
        self.scale_font.set_digits(0)
        self.scale_font.set_hexpand(True)
        self.scale_font.set_value_pos(Gtk.PositionType.RIGHT)
        font_box.pack_start(self.scale_font, True, True, 0)
        font_box.pack_start(Gtk.Label(label="Grand"), False, False, 0)
        content.pack_start(font_box, False, False, 4)

        # ── Taille popup ──
        sec3 = Gtk.Label(label="TAILLE DE LA FENÊTRE")
        sec3.get_style_context().add_class("section-label")
        sec3.set_halign(Gtk.Align.START)
        content.pack_start(sec3, False, False, 4)

        size_grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        size_grid.attach(Gtk.Label(label="Largeur", halign=Gtk.Align.END), 0, 0, 1, 1)
        adj_w = Gtk.Adjustment(value=cfg.get("popup_width", 420), lower=300, upper=800, step_increment=10)
        self.spin_w = Gtk.SpinButton(adjustment=adj_w, climb_rate=1, digits=0)
        size_grid.attach(self.spin_w, 1, 0, 1, 1)
        size_grid.attach(Gtk.Label(label="px"), 2, 0, 1, 1)

        size_grid.attach(Gtk.Label(label="Hauteur", halign=Gtk.Align.END), 0, 1, 1, 1)
        adj_h = Gtk.Adjustment(value=cfg.get("popup_height", 700), lower=400, upper=1200, step_increment=10)
        self.spin_h = Gtk.SpinButton(adjustment=adj_h, climb_rate=1, digits=0)
        size_grid.attach(self.spin_h, 1, 1, 1, 1)
        size_grid.attach(Gtk.Label(label="px"), 2, 1, 1, 1)
        content.pack_start(size_grid, False, False, 4)

        # ── Taille badge ──
        sec4 = Gtk.Label(label="TAILLE DU BADGE (ICÔNE PANEL)")
        sec4.get_style_context().add_class("section-label")
        sec4.set_halign(Gtk.Align.START)
        content.pack_start(sec4, False, False, 4)

        badge_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        badge_box.pack_start(Gtk.Label(label="Petit"), False, False, 0)
        adj_badge = Gtk.Adjustment(value=cfg.get("badge_size", 32), lower=18, upper=48, step_increment=2)
        self.scale_badge = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj_badge)
        self.scale_badge.set_digits(0)
        self.scale_badge.set_hexpand(True)
        self.scale_badge.set_value_pos(Gtk.PositionType.RIGHT)
        badge_box.pack_start(self.scale_badge, True, True, 0)
        badge_box.pack_start(Gtk.Label(label="Grand"), False, False, 0)
        content.pack_start(badge_box, False, False, 4)

        # ── Bouton Appliquer ──
        save_btn = Gtk.Button(label="✨  Appliquer le style")
        save_btn.get_style_context().add_class("save-btn")
        save_btn.set_margin_top(16)
        save_btn.connect("clicked", self._on_save)
        content.pack_start(save_btn, False, False, 0)

        self.show_all()

    def _on_color_click(self, btn, hex_color):
        self._selected_color = hex_color
        for b, c in self._color_btns:
            if c == hex_color:
                b.get_style_context().add_class("selected")
            else:
                b.get_style_context().remove_class("selected")

    def _on_save(self, btn):
        cfg = load_config()
        cfg["accent_color"] = self._selected_color
        cfg["font_size"] = int(self.scale_font.get_value())
        cfg["popup_width"] = int(self.spin_w.get_value())
        cfg["popup_height"] = int(self.spin_h.get_value())
        cfg["badge_size"] = int(self.scale_badge.get_value())
        save_config(cfg)
        self.destroy()


# ── MAIN APP ─────────────────────────────────────────────────────────────
class LogtimeIndicator:
    def __init__(self):
        self.popup = DashboardPopup()
        self.popup._logtime_callback = self._on_logtime_update
        self._menu_click_opens_dash = True  # Auto-open dashboard on first click

        # ── AppIndicator3 ──
        icon_path = create_badge_icon(0)
        self.indicator = AyatanaAppIndicator3.Indicator.new(
            "logtime42",
            icon_path,
            AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
        self.indicator.set_icon_theme_path(str(_ICON_DIR))
        self.indicator.set_icon_full(icon_path, "42 Dashboard")
        self.indicator.set_label("  ...  ", "  000h 00m / 000h  ")
        self.indicator.set_title("42 Dashboard")

        # Menu (right-click or fallback)
        menu = Gtk.Menu()

        item_dash = Gtk.MenuItem(label="Ouvrir Dashboard")
        item_dash.connect("activate", lambda w: self.popup.toggle())
        menu.append(item_dash)

        menu.append(Gtk.SeparatorMenuItem())

        item_prefs = Gtk.MenuItem(label="Personnalisation")
        item_prefs.connect("activate", lambda w: self._open_preferences())
        menu.append(item_prefs)

        item_refresh = Gtk.MenuItem(label="Rafraîchir")
        item_refresh.connect("activate", lambda w: self._manual_refresh())
        menu.append(item_refresh)

        menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="Quitter")
        item_quit.connect("activate", lambda w: Gtk.main_quit())
        menu.append(item_quit)

        menu.show_all()
        self.indicator.set_menu(menu)

        # Left-click: auto-open dashboard via menu 'show' signal
        menu.connect("show", self._on_menu_show)

        # Secondary activate (middle-click) → toggle dashboard
        self.indicator.set_secondary_activate_target(item_dash)

        self._logtime_h = 0
        self._logtime_m = 0
        self._target_h = 154
        self._friends_online = 0

    def _on_menu_show(self, menu):
        """Auto-open dashboard when the menu appears (any click on tray icon)."""
        if not self.popup.get_visible():
            GLib.idle_add(self.popup.toggle)

    def _open_preferences(self):
        """Open native GTK style preferences dialog."""
        dialog = PreferencesDialog()
        dialog.connect("destroy", lambda w: self._after_style_save())
        dialog.show_all()

    def _after_style_save(self):
        """Refresh badge icon and reload webview after style change."""
        cfg = load_config()
        icon_path = create_badge_icon(
            self._friends_online,
            size=cfg.get("badge_size", 32),
            color=cfg.get("accent_color", "#2ed573")
        )
        self.indicator.set_icon_full(icon_path, "42 Dashboard")
        # Resize popup
        self.popup.resize(cfg.get("popup_width", 420), cfg.get("popup_height", 700))
        # Reload the webview to apply new styles
        if self.popup._loaded:
            self.popup.webview.reload()

    def _on_logtime_update(self, logtime_str, target_str):
        """Called from WebKit title change with logtime info."""
        label_text = f"  {logtime_str} / {target_str}  "
        self.indicator.set_label(label_text, "  000h00m / 000h  ")

    def _manual_refresh(self):
        """Refresh data from API and update panel."""
        threading.Thread(target=self._update_panel_data, daemon=True).start()

    def _update_panel_data(self):
        """Fetch data from local Flask API and update indicator."""
        try:
            r = req_lib.get(f"http://127.0.0.1:{PORT}/api/data", timeout=10)
            d = r.json()
            if "h" in d:
                h, m = d["h"], d["m"]
                target_h = d["target_h"]
                label = f"  {h}h {str(m).zfill(2)}m / {target_h}h  "
                GLib.idle_add(self.indicator.set_label, label, "  000h 00m / 000h  ")
        except:
            pass

        try:
            r = req_lib.get(f"http://127.0.0.1:{PORT}/api/friends", timeout=30)
            friends = r.json()
            online = sum(1 for f in friends if f.get("online"))
            icon_path = create_badge_icon(online)
            GLib.idle_add(self.indicator.set_icon_full, icon_path, f"{online} amis en ligne")
        except:
            pass

    def _periodic_update(self):
        """Auto-refresh every 2 minutes."""
        while True:
            time.sleep(8)  # Wait for Flask to be ready
            self._update_panel_data()
            time.sleep(112)  # Then every ~2 min

    def start_flask(self):
        import logging
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)
        flask_app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)

    def run(self):
        print(f"\n  ╔════════════════════════════════════════════╗")
        print(f"  ║  42 Dashboard — Panel Indicator (v3)       ║")
        print(f"  ╚════════════════════════════════════════════╝\n")
        print(f"  Config  : {CONFIG_PATH}")
        print(f"  Server  : http://127.0.0.1:{PORT}")
        print(f"  → Clic icône = Ouvre le Dashboard")
        print(f"  → Menu      = Personnalisation / Rafraîchir / Quitter")
        print(f"  → Auto-refresh toutes les 2 min")
        print(f"  Ctrl+C pour quitter\n")

        # Start Flask in background
        flask_thread = threading.Thread(target=self.start_flask, daemon=True)
        flask_thread.start()
        time.sleep(0.5)

        # Start periodic updater
        updater = threading.Thread(target=self._periodic_update, daemon=True)
        updater.start()

        # Auto-open popup on first start
        GLib.timeout_add(1500, lambda: self.popup.toggle() or False)

        try:
            Gtk.main()
        except KeyboardInterrupt:
            pass


# ── HTML ─────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>42 Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700;900&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0d0d14;--bg2:#12121f;--bg3:#1a1a2e;--bg4:#252540;
  --accent:#2ed573;--blue:#54a0ff;--orange:#ff9f43;--red:#ff4757;
  --white:#fff;--gray:#8b8b9b;--font:'JetBrains Mono',monospace;
  --fsize:14px;
}
body{background:var(--bg);color:var(--white);font-family:var(--font);min-height:100vh;font-size:var(--fsize)}
#topbar{display:flex;align-items:center;padding:10px 18px;background:#08080f;border-bottom:1px solid #1a1a30;gap:10px;position:sticky;top:0;z-index:100}
#badge{background:var(--accent);color:#000;font-weight:900;font-size:12px;padding:1px 9px;border-radius:99px;display:none;box-shadow:0 0 12px rgba(46,213,115,.4)}
#toptime{font-size:13px;font-weight:700;flex:1;letter-spacing:-.3px}
.topbtn{background:transparent;border:none;color:var(--gray);font-size:17px;cursor:pointer;padding:5px 8px;border-radius:6px;transition:all .15s;line-height:1}
.topbtn:hover{background:var(--bg3);color:var(--white)}
#main{max-width:580px;margin:0 auto;padding:16px 14px}
#biglabel{font-size:30px;font-weight:900;text-align:center;padding:14px 0 4px;letter-spacing:-1px}
#progress-wrap{background:var(--bg3);border-radius:99px;height:7px;margin:8px 0 14px;overflow:hidden}
#progress-bar{height:100%;background:linear-gradient(90deg,var(--accent),#69ff9a);border-radius:99px;transition:width .7s cubic-bezier(.4,0,.2,1);box-shadow:0 0 14px rgba(46,213,115,.35)}
#stats{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-bottom:14px}
.stat-box{background:var(--bg3);border-radius:11px;padding:10px 6px;text-align:center;border:1px solid var(--bg4)}
.stat-label{font-size:8px;font-weight:700;color:var(--gray);text-transform:uppercase;letter-spacing:.6px}
.stat-val{font-size:17px;font-weight:900;margin-top:4px}
.section-title{font-size:9px;font-weight:700;color:var(--gray);letter-spacing:1.2px;text-transform:uppercase;margin:14px 0 7px}
.sep{border:none;border-top:1px solid var(--bg4);margin:16px 0}
#friends-header{display:flex;align-items:center;gap:8px;margin-bottom:9px}
#friends-header .section-title{margin:0;flex:1}
#back-btn{display:none;background:var(--bg3);border:none;color:var(--white);border-radius:6px;padding:4px 12px;cursor:pointer;font-family:var(--font);font-size:11px;font-weight:700}
#back-btn:hover{background:var(--bg4)}
.friend-card{background:var(--bg2);border-radius:12px;margin-bottom:8px;overflow:hidden;border:1px solid var(--bg3);transition:border-color .2s}
.friend-card.online{background:#0c1c12;border-color:#1d4028}
.friend-header{display:flex;align-items:center;padding:11px 14px;cursor:pointer;gap:12px;transition:background .15s}
.friend-header:hover{background:rgba(255,255,255,.03)}
.avatar{width:46px;height:46px;border-radius:50%;background:var(--bg4);display:flex;align-items:center;justify-content:center;font-size:22px;flex-shrink:0;overflow:hidden;border:2px solid var(--bg3)}
.avatar img{width:100%;height:100%;object-fit:cover;border-radius:50%}
.friend-info{flex:1;min-width:0}
.friend-name{font-weight:800;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.friend-time{font-size:11px;color:var(--gray);margin-top:2px}
.friend-status{font-size:12px;font-weight:700;white-space:nowrap;padding-left:8px}
.status-on{color:var(--accent)}
.status-off{color:var(--red)}
.friend-detail{border-top:1px solid var(--bg3);background:rgba(0,0,0,.2);padding:10px 14px;display:none}
.friend-detail.open{display:block}
.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:10px}
.detail-item{font-size:11px;color:#ccc;padding:3px 0}
.detail-btns{display:flex;gap:8px}
.detail-btn{background:var(--bg4);border:none;color:var(--white);border-radius:7px;padding:7px 0;cursor:pointer;font-family:var(--font);font-size:11px;font-weight:700;transition:all .15s;flex:1}
.detail-btn:hover{background:var(--bg3);color:var(--accent)}
#cal-view{display:none}
#cal-title{font-size:12px;font-weight:900;text-align:center;margin-bottom:12px;color:var(--accent);letter-spacing:1px}
#cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:3px}
.cal-day{border-radius:6px;padding:5px 2px;text-align:center;min-height:42px;display:flex;flex-direction:column;align-items:center;justify-content:center}
.cal-num{font-size:8px;font-weight:700;opacity:.55}
.cal-val{font-size:10px;font-weight:800;margin-top:1px}
.c0{background:var(--bg3)}.c1{background:rgba(46,213,115,.1)}.c2{background:rgba(46,213,115,.28)}.c3{background:rgba(46,213,115,.5)}.c4{background:rgba(46,213,115,.75)}
.c5{background:#2ed573;box-shadow:0 0 8px rgba(46,213,115,.35)}
.dark-text{color:#000!important}.dark-text .cal-num{opacity:.5}
#modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:200;align-items:center;justify-content:center;padding:16px}
#modal-overlay.open{display:flex}
#modal{background:var(--bg2);border-radius:16px;padding:24px;width:100%;max-width:420px;border:1px solid var(--bg4);max-height:90vh;overflow-y:auto}
#modal h2{font-size:15px;font-weight:900;margin-bottom:18px;color:var(--accent)}
.form-section{font-size:9px;font-weight:700;color:var(--gray);text-transform:uppercase;letter-spacing:.8px;margin:16px 0 8px}
.form-row{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.form-row label{font-size:11px;width:100px;flex-shrink:0;color:var(--gray)}
.form-row input[type=text],.form-row input[type=password],.form-row input[type=number]{flex:1;background:var(--bg3);border:1px solid var(--bg4);border-radius:7px;padding:8px 10px;color:var(--white);font-family:var(--font);font-size:12px;outline:none;transition:border-color .15s}
.form-row input:focus{border-color:var(--accent)}
.days-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;margin-bottom:12px}
.day-btn{background:var(--bg3);border:1px solid transparent;border-radius:6px;padding:7px 2px;text-align:center;font-size:9px;font-weight:700;color:var(--gray);cursor:pointer;transition:all .15s;user-select:none}
.day-btn.active{background:rgba(46,213,115,.18);border-color:var(--accent);color:var(--accent)}
.save-btn{background:var(--accent);color:#000;border:none;border-radius:8px;padding:11px;width:100%;font-family:var(--font);font-weight:900;font-size:13px;cursor:pointer;margin-top:14px;transition:opacity .15s}
.save-btn:hover{opacity:.85}
.friends-manage{margin-top:6px}
.fm-row{display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--bg3)}
.fm-row span{flex:1;font-size:12px}
.del-btn{background:transparent;border:none;color:var(--red);cursor:pointer;font-size:15px;padding:2px 6px}
.add-row{display:flex;gap:8px;margin-top:10px}
.add-row input{flex:1;background:var(--bg3);border:1px solid var(--bg4);border-radius:7px;padding:8px 10px;color:var(--white);font-family:var(--font);font-size:12px;outline:none}
.add-row input:focus{border-color:var(--accent)}
.add-row button{background:var(--accent);color:#000;border:none;border-radius:7px;padding:8px 14px;font-family:var(--font);font-weight:700;font-size:12px;cursor:pointer}
.spin{text-align:center;color:var(--gray);padding:22px;font-size:12px}
#toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%) translateY(70px);background:var(--accent);color:#000;padding:8px 22px;border-radius:99px;font-weight:800;font-size:12px;transition:transform .3s cubic-bezier(.34,1.56,.64,1);z-index:300;pointer-events:none}
#toast.show{transform:translateX(-50%) translateY(0)}
</style>
</head>
<body>
<div id="topbar">
  <div id="badge">0</div>
  <div id="toptime">Chargement...</div>
  <button class="topbtn" onclick="openMyProfile()" title="Mon profil">👤</button>
  <button class="topbtn" onclick="toggleMyCalendar()" title="Mon historique">📅</button>
  <button class="topbtn" onclick="refreshAll()" title="Rafraîchir">🔄</button>
  <button class="topbtn" onclick="openSettings()" title="Paramètres">⚙️</button>
</div>
<div id="main">
  <div id="biglabel">Logtime --h --m</div>
  <div id="progress-wrap"><div id="progress-bar" style="width:0%"></div></div>
  <div id="stats">
    <div class="stat-box"><div class="stat-label">Wallet</div><div class="stat-val" id="s-wallet">-</div></div>
    <div class="stat-box"><div class="stat-label">Eval</div><div class="stat-val" id="s-eval">-</div></div>
    <div class="stat-box"><div class="stat-label">Aujourd'hui</div><div class="stat-val" id="s-today">-</div></div>
    <div class="stat-box"><div class="stat-label">Cible/J</div><div class="stat-val" id="s-target">-</div></div>
  </div>

  <div id="friends-header">
    <div class="section-title" id="section-label">FRIENDS STATUS</div>
    <button id="back-btn" onclick="showFriends()">◀ Retour</button>
  </div>
  <div id="friends-view"><div class="spin">Chargement des amis...</div></div>
  <div id="cal-view"><div id="cal-title"></div><div id="cal-grid"></div></div>
</div>
<div id="modal-overlay" onclick="if(event.target===this)closeSettings()">
<div id="modal">
  <h2>⚙️ Paramètres</h2>
  <div class="form-section">Identification 42</div>
  <div class="form-row"><label>Login</label><input type="text" id="cfg-user" placeholder="ex: papilaz"></div>
  <div class="form-row"><label>API UID</label><input type="text" id="cfg-uid" placeholder="u-s4t2ud..."></div>
  <div class="form-row"><label>API Secret</label><input type="password" id="cfg-secret" placeholder="laisser vide = inchangé"></div>
  <div class="form-section">Logtime</div>
  <div class="form-row">
    <label>Jours offerts</label>
    <input type="number" id="cfg-gift" min="0" max="31" style="width:60px;flex:none">
    <span style="font-size:11px;color:var(--gray)">× −7h/objectif</span>
  </div>
  <div class="form-section">Jours travaillés</div>
  <div class="days-grid" id="days-grid">
    <div class="day-btn" data-i="0">Dim</div><div class="day-btn" data-i="1">Lun</div>
    <div class="day-btn" data-i="2">Mar</div><div class="day-btn" data-i="3">Mer</div>
    <div class="day-btn" data-i="4">Jeu</div><div class="day-btn" data-i="5">Ven</div>
    <div class="day-btn" data-i="6">Sam</div>
  </div>
  <div class="form-section">Amis</div>
  <div id="fm-list" class="friends-manage"></div>
  <div class="add-row">
    <input type="text" id="new-friend" placeholder="Login à ajouter" onkeydown="if(event.key==='Enter')addFriend()">
    <button onclick="addFriend()">➕</button>
  </div>
  <button class="save-btn" onclick="saveSettings()">💾 Enregistrer</button>
</div>
</div>
<div id="toast"></div>
<script>
let myCalData={},myMonthDays=31,currentView='friends',friendsData=[],cfgCache={},refreshTimer=null;
async function init(){await loadConfig();await applyStyle();refreshAll();refreshTimer=setInterval(refreshAll,5*60*1000)}
async function applyStyle(){try{const r=await fetch('/api/style');const s=await r.json();document.documentElement.style.setProperty('--accent',s.accent_color);document.documentElement.style.setProperty('--fsize',s.font_size+'px')}catch(e){}}
async function refreshAll(){fetchMainData();fetchFriends()}
async function fetchMainData(){try{const r=await fetch('/api/data');const d=await r.json();if(d.error==='config'){openSettings();return}if(d.error==='token'){toast('❌ Token invalide');return}myCalData=d.my_calendar;myMonthDays=d.month_days;document.getElementById('toptime').textContent=`${d.h}h ${pad(d.m)}m / ${d.target_h}h`;document.getElementById('biglabel').textContent=`Logtime ${d.h}h ${pad(d.m)}m`;document.getElementById('progress-bar').style.width=d.pct+'%';document.getElementById('s-wallet').textContent=d.wallet+'₳';document.getElementById('s-eval').textContent=d.eval;document.getElementById('s-today').textContent=`${d.th}h${pad(d.tm)}`;const tgt=document.getElementById('s-target');if(d.dh===null){tgt.textContent='✅';tgt.style.color='var(--accent)'}else{tgt.textContent=`${d.dh}h${pad(d.dm)}`;tgt.style.color=''}if(currentView==='calendar'&&document.getElementById('cal-title').dataset.login==='__me__')renderCalendar(myCalData,'MY HISTORY','__me__');document.title=`42|${d.h}h${pad(d.m)}m|${d.target_h}h`}catch(e){console.error(e)}}
async function fetchFriends(){try{const r=await fetch('/api/friends');friendsData=await r.json();if(currentView==='friends')renderFriends();const n=friendsData.filter(f=>f.online).length;const b=document.getElementById('badge');if(n>0){b.textContent=n;b.style.display='block'}else b.style.display='none'}catch(e){console.error(e)}}
function renderFriends(){const el=document.getElementById('friends-view');if(!friendsData.length){el.innerHTML='<div class="spin">Ajoute des amis dans ⚙️</div>';return}el.innerHTML=friendsData.map((f,i)=>`<div class="friend-card${f.online?' online':''}" data-idx="${i}"><div class="friend-header"><div class="avatar">${f.avatar?`<img src="${f.avatar}" alt="${f.login}" onerror="this.parentElement.textContent='👤'">`:'👤'}</div><div class="friend-info"><div class="friend-name">${f.login}</div><div class="friend-time">${f.h}h ${pad(f.m)}m ce mois</div></div><div class="friend-status ${f.online?'status-on':'status-off'}">${f.online?'🟢 '+f.host:'🔴 Off'}</div></div><div class="friend-detail" id="fd-${i}"><div class="detail-grid"><div class="detail-item">💰 ${f.wallet}₳</div><div class="detail-item">🎓 Lvl ${f.level}</div><div class="detail-item">⚖️ ${f.eval} pts</div><div class="detail-item">🏊 Pool ${f.pool}</div></div><div class="detail-btns"><button class="detail-btn" onclick="event.stopPropagation();openProfile('${f.login}')">👤 Profil</button><button class="detail-btn" onclick="event.stopPropagation();showFriendCal(${i})">📅 Calendrier</button></div></div></div>`).join('');el.querySelectorAll('.friend-header').forEach(hdr=>{hdr.addEventListener('click',function(){const card=this.closest('.friend-card');const idx=card.dataset.idx;document.getElementById('fd-'+idx).classList.toggle('open')})})}
function toggleDetail(i){document.getElementById('fd-'+i).classList.toggle('open')}
function colorClass(h){if(h<=0)return'c0';if(h<2)return'c1';if(h<5)return'c2';if(h<7)return'c3';if(h<9)return'c4';return'c5'}
function renderCalendar(calData,title,loginKey){currentView='calendar';document.getElementById('friends-view').style.display='none';document.getElementById('cal-view').style.display='block';document.getElementById('section-label').textContent=title.toUpperCase();document.getElementById('back-btn').style.display='inline-block';const calTitle=document.getElementById('cal-title');calTitle.textContent=title.toUpperCase();calTitle.dataset.login=loginKey||'';const grid=document.getElementById('cal-grid');grid.innerHTML='';for(let d=1;d<=myMonthDays;d++){const h=calData[String(d)]||calData[d]||0;const hh=Math.floor(h),mm=Math.round((h-hh)*60);const cls=colorClass(h);const dark=cls==='c4'||cls==='c5';grid.innerHTML+=`<div class="cal-day ${cls}${dark?' dark-text':''}"><div class="cal-num">${d}</div>${h>0?`<div class="cal-val">${hh}h${pad(mm)}</div>`:''}</div>`}}
function toggleMyCalendar(){if(currentView==='calendar'&&document.getElementById('cal-title').dataset.login==='__me__'){showFriends()}else{renderCalendar(myCalData,'MY HISTORY','__me__')}}
function showFriendCal(i){renderCalendar(friendsData[i].calendar,friendsData[i].login,friendsData[i].login)}
function showFriends(){currentView='friends';document.getElementById('friends-view').style.display='block';document.getElementById('cal-view').style.display='none';document.getElementById('section-label').textContent='FRIENDS STATUS';document.getElementById('back-btn').style.display='none';renderFriends()}
async function loadConfig(){const r=await fetch('/api/config');cfgCache=await r.json()}
function openSettings(){document.getElementById('modal-overlay').classList.add('open');document.getElementById('cfg-user').value=cfgCache.username||'';document.getElementById('cfg-uid').value=cfgCache.api_uid||'';document.getElementById('cfg-secret').value='';document.getElementById('cfg-gift').value=cfgCache.gift_days||0;const wd=cfgCache.working_days||[false,true,true,true,true,true,false];document.querySelectorAll('#days-grid .day-btn').forEach(btn=>{btn.classList.toggle('active',wd[+btn.dataset.i]);btn.onclick=()=>btn.classList.toggle('active')});renderFriendManage()}
function renderFriendManage(){const list=document.getElementById('fm-list');const friends=cfgCache.friends||[];list.innerHTML=friends.length?friends.map(f=>`<div class="fm-row"><span>${f}</span><button class="del-btn" onclick="removeFriend('${f}')">🗑</button></div>`).join(''):'<div style="color:var(--gray);font-size:11px;padding:4px 0">Aucun ami ajouté</div>'}
async function addFriend(){const login=document.getElementById('new-friend').value.trim();if(!login)return;await fetch('/api/friend/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({login})});cfgCache.friends=cfgCache.friends||[];if(!cfgCache.friends.includes(login))cfgCache.friends.push(login);document.getElementById('new-friend').value='';renderFriendManage()}
async function removeFriend(login){await fetch('/api/friend/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({login})});cfgCache.friends=(cfgCache.friends||[]).filter(f=>f!==login);renderFriendManage()}
async function saveSettings(){const wd=Array.from({length:7},(_,i)=>document.querySelector(`[data-i="${i}"]`).classList.contains('active'));const body={username:document.getElementById('cfg-user').value.trim(),api_uid:document.getElementById('cfg-uid').value.trim(),gift_days:parseInt(document.getElementById('cfg-gift').value)||0,working_days:wd,friends:cfgCache.friends||[]};const secret=document.getElementById('cfg-secret').value.trim();if(secret)body.api_secret=secret;await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});await loadConfig();closeSettings();toast('✓ Sauvegardé !');refreshAll()}
function closeSettings(){document.getElementById('modal-overlay').classList.remove('open')}
function pad(n){return String(n).padStart(2,'0')}
function openProfile(login){window.open(`https://profile.intra.42.fr/users/${login}`,'_blank')}
function openMyProfile(){if(cfgCache.username)openProfile(cfgCache.username);else{toast('⚙️ Configure ton login');openSettings()}}
async function launchCookie(){await fetch('/api/launch_cookie');toast('🌐 Connecte-toi à l\'Intra dans le navigateur')}
function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),3000)}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeSettings()});
init();
</script>
</body>
</html>"""

if __name__ == "__main__":
    app = LogtimeIndicator()
    app.run()
