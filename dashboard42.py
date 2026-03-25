#!/usr/bin/env python3
"""
42 Dashboard — Flask edition
Usage: python3 dashboard42.py
Ouvre automatiquement http://localhost:7242 dans ton navigateur.
"""
from config import DEFAULT_CONFIG, CONFIG_PATH, COOKIE_PATH, PORT
from flask import Flask, jsonify, request, render_template_string
import requests as req
import json, threading, time, calendar, webbrowser, subprocess
from datetime import datetime, timezone
from pathlib import Path
# ── CONFIG ──────────────────────────────────────────────────────────────


def load_config():
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except: pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))

def load_cookie():
    return COOKIE_PATH.read_text().strip() if COOKIE_PATH.exists() else None

# ── API 42 ──────────────────────────────────────────────────────────────
_tok = {"v": None, "exp": 0}

def get_token(uid, secret):
    if _tok["v"] and time.time() < _tok["exp"] - 60:
        return _tok["v"]
    try:
        r = req.post("https://api.intra.42.fr/oauth/token", json={
            "grant_type": "client_credentials",
            "client_id": uid, "client_secret": secret
        }, timeout=15)
        d = r.json()
        _tok["v"] = d["access_token"]
        _tok["exp"] = time.time() + d.get("expires_in", 7200)
        return _tok["v"]
    except: return None

def api_get(path, token, params=None, cookie=None):
    headers = {"Authorization": f"Bearer {token}"}
    if cookie:
        headers["Cookie"] = f"_intra_42_session_production={cookie}"
    try:
        r = req.get(f"https://api.intra.42.fr/v2/{path}",
                    headers=headers, params=params, timeout=15)
        return r.json() if r.status_code == 200 else None
    except: return None

# ── CALCULS ─────────────────────────────────────────────────────────────
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
        if b.date() != today: continue
        e = datetime.fromisoformat(l["end_at"].replace("Z", "+00:00")) if l.get("end_at") else now
        total += (e - b).total_seconds() * 1000
    return total

def hm(ms):
    return int(ms // 3_600_000), int((ms % 3_600_000) // 60_000)

def daily_target(target_h, current_ms, working_days):
    rem = target_h - current_ms / 3_600_000
    if rem <= 0: return None, None
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

# ── FLASK ────────────────────────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/data")
def api_data():
    cfg = load_config()
    uid, secret, username = cfg["api_uid"], cfg["api_secret"], cfg["username"]
    if not uid or not secret or not username:
        return jsonify({"error": "config"})
    token = get_token(uid, secret)
    if not token: return jsonify({"error": "token"})

    s, e = month_range()
    locs = api_get(f"users/{username}/locations", token,
                   {"range[begin_at]": f"{s},{e}", "per_page": 100}) or []
    total = logtime_ms(locs)
    tod   = today_ms(locs)
    h, m  = hm(total)
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

@app.route("/api/friends")
def api_friends():
    cfg = load_config()
    token = get_token(cfg["api_uid"], cfg["api_secret"])
    if not token: return jsonify([])
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

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        cfg = load_config()
        cfg.pop("api_secret", None)
        return jsonify(cfg)
    data = request.json or {}
    cfg = load_config()
    for k in ["username", "api_uid", "api_secret", "gift_days", "friends", "working_days"]:
        if k in data: cfg[k] = data[k]
    save_config(cfg)
    _tok["v"] = None
    return jsonify({"ok": True})

@app.route("/api/friend/add", methods=["POST"])
def add_friend():
    login = (request.json or {}).get("login", "").strip()
    if not login: return jsonify({"ok": False})
    cfg = load_config()
    if login not in cfg["friends"]:
        cfg["friends"].append(login)
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/friend/remove", methods=["POST"])
def remove_friend():
    login = (request.json or {}).get("login", "").strip()
    cfg = load_config()
    cfg["friends"] = [f for f in cfg["friends"] if f != login]
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/launch_cookie")
def launch_cookie():
    candidates = [
        Path(__file__).parent / "capture_cookies.py",
        Path.home() / ".local/share/gnome-shell/extensions/logtime@42/capture_cookies.py",
    ]
    for p in candidates:
        if p.exists():
            subprocess.Popen(["python3", str(p)])
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "capture_cookies.py introuvable"})

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
}
body{background:var(--bg);color:var(--white);font-family:var(--font);min-height:100vh;font-size:14px}

/* TOPBAR */
#topbar{
  display:flex;align-items:center;padding:10px 18px;
  background:#08080f;border-bottom:1px solid #1a1a30;gap:10px;
  position:sticky;top:0;z-index:100;
}
#badge{
  background:var(--accent);color:#000;font-weight:900;font-size:12px;
  padding:1px 9px;border-radius:99px;display:none;box-shadow:0 0 12px rgba(46,213,115,.4);
}
#toptime{font-size:13px;font-weight:700;flex:1;letter-spacing:-.3px}
.topbtn{
  background:transparent;border:none;color:var(--gray);font-size:17px;
  cursor:pointer;padding:5px 8px;border-radius:6px;transition:all .15s;line-height:1;
}
.topbtn:hover{background:var(--bg3);color:var(--white)}

/* MAIN */
#main{max-width:580px;margin:0 auto;padding:16px 14px}

/* BIG LOGTIME */
#biglabel{
  font-size:30px;font-weight:900;text-align:center;
  padding:14px 0 4px;letter-spacing:-1px;
}

/* PROGRESS */
#progress-wrap{background:var(--bg3);border-radius:99px;height:7px;margin:8px 0 14px;overflow:hidden}
#progress-bar{
  height:100%;background:linear-gradient(90deg,var(--accent),#69ff9a);
  border-radius:99px;transition:width .7s cubic-bezier(.4,0,.2,1);
  box-shadow:0 0 14px rgba(46,213,115,.35);
}

/* STAT BOXES */
#stats{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-bottom:14px}
.stat-box{background:var(--bg3);border-radius:11px;padding:10px 6px;text-align:center;border:1px solid var(--bg4)}
.stat-label{font-size:8px;font-weight:700;color:var(--gray);text-transform:uppercase;letter-spacing:.6px}
.stat-val{font-size:17px;font-weight:900;margin-top:4px}

/* SECTION TITLES */
.section-title{
  font-size:9px;font-weight:700;color:var(--gray);letter-spacing:1.2px;
  text-transform:uppercase;margin:14px 0 7px;
}

/* SCALES */
#scales{display:flex;flex-direction:column;gap:5px;margin-bottom:4px}
.scale-item{border-radius:8px;padding:8px 12px;font-size:12px;background:var(--bg3);border:1px solid var(--bg4)}
.scale-corriger{border-left:3px solid var(--orange) !important;color:var(--orange)}
.scale-corrige{border-left:3px solid var(--blue) !important;color:var(--blue)}
.btn-cookie{
  background:var(--bg3);border:1px solid var(--bg4);color:var(--gray);
  border-radius:8px;padding:9px 16px;cursor:pointer;font-family:var(--font);
  font-size:12px;width:100%;transition:all .15s;text-align:center;
}
.btn-cookie:hover{border-color:var(--accent);color:var(--white)}
.btn-cookie.expired{border-color:var(--red);color:var(--red)}

/* SEP */
.sep{border:none;border-top:1px solid var(--bg4);margin:16px 0}

/* FRIENDS HEADER */
#friends-header{display:flex;align-items:center;gap:8px;margin-bottom:9px}
#friends-header .section-title{margin:0;flex:1}
#back-btn{display:none;background:var(--bg3);border:none;color:var(--white);
  border-radius:6px;padding:4px 12px;cursor:pointer;font-family:var(--font);
  font-size:11px;font-weight:700;}
#back-btn:hover{background:var(--bg4)}

/* FRIEND CARD */
.friend-card{
  background:var(--bg2);border-radius:12px;margin-bottom:8px;
  overflow:hidden;border:1px solid var(--bg3);transition:border-color .2s;
}
.friend-card.online{background:#0c1c12;border-color:#1d4028}
.friend-header{display:flex;align-items:center;padding:11px 14px;cursor:pointer;gap:12px;transition:background .15s}
.friend-header:hover{background:rgba(255,255,255,.03)}
.avatar{
  width:46px;height:46px;border-radius:50%;background:var(--bg4);
  display:flex;align-items:center;justify-content:center;
  font-size:22px;flex-shrink:0;overflow:hidden;border:2px solid var(--bg3);
}
.avatar img{width:100%;height:100%;object-fit:cover;border-radius:50%}
.friend-info{flex:1;min-width:0}
.friend-name{font-weight:800;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.friend-time{font-size:11px;color:var(--gray);margin-top:2px}
.friend-status{font-size:12px;font-weight:700;white-space:nowrap;padding-left:8px}
.status-on{color:var(--accent)}
.status-off{color:var(--red)}

.friend-detail{
  border-top:1px solid var(--bg3);background:rgba(0,0,0,.2);
  padding:10px 14px;display:none;
}
.friend-detail.open{display:block}
.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-bottom:10px}
.detail-item{font-size:11px;color:#ccc;padding:3px 0}
.detail-btns{display:flex;gap:8px}
.detail-btn{
  background:var(--bg4);border:none;color:var(--white);border-radius:7px;
  padding:7px 0;cursor:pointer;font-family:var(--font);font-size:11px;
  font-weight:700;transition:all .15s;flex:1;
}
.detail-btn:hover{background:var(--bg3);color:var(--accent)}

/* CALENDAR */
#cal-view{display:none}
#cal-title{font-size:12px;font-weight:900;text-align:center;margin-bottom:12px;color:var(--accent);letter-spacing:1px}
#cal-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:3px}
.cal-day{
  border-radius:6px;padding:5px 2px;text-align:center;
  min-height:42px;display:flex;flex-direction:column;
  align-items:center;justify-content:center;
}
.cal-num{font-size:8px;font-weight:700;opacity:.55}
.cal-val{font-size:10px;font-weight:800;margin-top:1px}
.c0{background:var(--bg3)}
.c1{background:rgba(46,213,115,.1)}
.c2{background:rgba(46,213,115,.28)}
.c3{background:rgba(46,213,115,.5)}
.c4{background:rgba(46,213,115,.75)}
.c5{background:#2ed573;box-shadow:0 0 8px rgba(46,213,115,.35)}
.dark-text{color:#000 !important}
.dark-text .cal-num{opacity:.5}

/* MODAL */
#modal-overlay{
  display:none;position:fixed;inset:0;background:rgba(0,0,0,.75);
  z-index:200;align-items:center;justify-content:center;padding:16px;
}
#modal-overlay.open{display:flex}
#modal{
  background:var(--bg2);border-radius:16px;padding:24px;width:100%;
  max-width:420px;border:1px solid var(--bg4);max-height:90vh;overflow-y:auto;
}
#modal h2{font-size:15px;font-weight:900;margin-bottom:18px;color:var(--accent)}
.form-section{
  font-size:9px;font-weight:700;color:var(--gray);text-transform:uppercase;
  letter-spacing:.8px;margin:16px 0 8px;
}
.form-row{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.form-row label{font-size:11px;width:100px;flex-shrink:0;color:var(--gray)}
.form-row input[type=text],
.form-row input[type=password],
.form-row input[type=number]{
  flex:1;background:var(--bg3);border:1px solid var(--bg4);border-radius:7px;
  padding:8px 10px;color:var(--white);font-family:var(--font);font-size:12px;outline:none;
  transition:border-color .15s;
}
.form-row input:focus{border-color:var(--accent)}
.days-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;margin-bottom:12px}
.day-btn{
  background:var(--bg3);border:1px solid transparent;border-radius:6px;
  padding:7px 2px;text-align:center;font-size:9px;font-weight:700;
  color:var(--gray);cursor:pointer;transition:all .15s;user-select:none;
}
.day-btn.active{background:rgba(46,213,115,.18);border-color:var(--accent);color:var(--accent)}
.save-btn{
  background:var(--accent);color:#000;border:none;border-radius:8px;
  padding:11px;width:100%;font-family:var(--font);font-weight:900;font-size:13px;
  cursor:pointer;margin-top:14px;transition:opacity .15s;
}
.save-btn:hover{opacity:.85}
.friends-manage{margin-top:6px}
.fm-row{
  display:flex;align-items:center;gap:8px;padding:7px 0;
  border-bottom:1px solid var(--bg3);
}
.fm-row span{flex:1;font-size:12px}
.del-btn{background:transparent;border:none;color:var(--red);cursor:pointer;font-size:15px;padding:2px 6px}
.add-row{display:flex;gap:8px;margin-top:10px}
.add-row input{
  flex:1;background:var(--bg3);border:1px solid var(--bg4);border-radius:7px;
  padding:8px 10px;color:var(--white);font-family:var(--font);font-size:12px;outline:none;
}
.add-row input:focus{border-color:var(--accent)}
.add-row button{
  background:var(--accent);color:#000;border:none;border-radius:7px;
  padding:8px 14px;font-family:var(--font);font-weight:700;font-size:12px;cursor:pointer;
}

/* SPINNER */
.spin{text-align:center;color:var(--gray);padding:22px;font-size:12px}

/* TOAST */
#toast{
  position:fixed;bottom:22px;left:50%;transform:translateX(-50%) translateY(70px);
  background:var(--accent);color:#000;padding:8px 22px;border-radius:99px;
  font-weight:800;font-size:12px;transition:transform .3s cubic-bezier(.34,1.56,.64,1);
  z-index:300;pointer-events:none;
}
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

  <div class="section-title">Prochaines défenses</div>
  <div id="scales"><div class="spin">...</div></div>

  <hr class="sep">

  <div id="friends-header">
    <div class="section-title" id="section-label">FRIENDS STATUS</div>
    <button id="back-btn" onclick="showFriends()">◀ Retour</button>
  </div>

  <div id="friends-view"><div class="spin">Chargement des amis...</div></div>
  <div id="cal-view">
    <div id="cal-title"></div>
    <div id="cal-grid"></div>
  </div>
</div>

<!-- SETTINGS MODAL -->
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
let myCalData = {}, myMonthDays = 31;
let currentView = 'friends';
let friendsData = [];
let cfgCache = {};
let refreshTimer = null;

async function init() {
  await loadConfig();
  refreshAll();
  refreshTimer = setInterval(refreshAll, 5 * 60 * 1000);
}

async function refreshAll() {
  fetchMainData();
  fetchFriends();
}

/* ── MAIN DATA ── */
async function fetchMainData() {
  try {
    const r = await fetch('/api/data');
    const d = await r.json();
    if (d.error === 'config') { openSettings(); return; }
    if (d.error === 'token')  { toast('❌ Token invalide — vérifie UID/Secret'); return; }

    myCalData = d.my_calendar;
    myMonthDays = d.month_days;

    document.getElementById('toptime').textContent = `${d.h}h ${pad(d.m)}m / ${d.target_h}h`;
    document.getElementById('biglabel').textContent = `Logtime ${d.h}h ${pad(d.m)}m`;
    document.getElementById('progress-bar').style.width = d.pct + '%';
    document.getElementById('s-wallet').textContent = d.wallet + '₳';
    document.getElementById('s-eval').textContent = d.eval;
    document.getElementById('s-today').textContent = `${d.th}h${pad(d.tm)}`;
    const tgt = document.getElementById('s-target');
    if (d.dh === null) { tgt.textContent = '✅'; tgt.style.color = 'var(--accent)'; }
    else { tgt.textContent = `${d.dh}h${pad(d.dm)}`; tgt.style.color = ''; }

    renderScales(d);
    if (currentView === 'calendar' && document.getElementById('cal-title').dataset.login === '__me__')
      renderCalendar(myCalData, 'MY HISTORY', '__me__');
  } catch(e) { console.error(e); }
}

function renderScales(d) {
  const el = document.getElementById('scales');
  if (d.cookie_status === 'missing') {
    el.innerHTML = '<button class="btn-cookie" onclick="launchCookie()">🔑 Connexion Cookie — Activer les défenses</button>';
  } else if (d.cookie_status === 'expired') {
    el.innerHTML = '<button class="btn-cookie expired" onclick="launchCookie()">🔄 Cookie expiré — Reconnexion</button>';
  } else if (!d.scales.length) {
    el.innerHTML = '<div style="color:var(--gray);font-size:12px;text-align:center;padding:6px">Aucune défense prévue</div>';
  } else {
    el.innerHTML = d.scales.map(s =>
      `<div class="scale-item scale-${s.type}">
        <b>${s.date}</b> &nbsp; ${s.label} &nbsp;
        <span style="color:var(--white);opacity:.7">${s.project}</span>
      </div>`
    ).join('');
  }
}

/* ── FRIENDS ── */
async function fetchFriends() {
  try {
    const r = await fetch('/api/friends');
    friendsData = await r.json();
    if (currentView === 'friends') renderFriends();
    const n = friendsData.filter(f => f.online).length;
    const b = document.getElementById('badge');
    if (n > 0) { b.textContent = n; b.style.display = 'block'; }
    else b.style.display = 'none';
  } catch(e) { console.error(e); }
}

function renderFriends() {
  const el = document.getElementById('friends-view');
  if (!friendsData.length) {
    el.innerHTML = '<div class="spin">Ajoute des amis dans ⚙️</div>';
    return;
  }
  el.innerHTML = friendsData.map((f, i) => `
    <div class="friend-card${f.online?' online':''}">
      <div class="friend-header" onclick="toggleDetail(${i})">
        <div class="avatar">
          ${f.avatar
            ? `<img src="${f.avatar}" alt="${f.login}" onerror="this.parentElement.textContent='👤'">`
            : '👤'}
        </div>
        <div class="friend-info">
          <div class="friend-name">${f.login}</div>
          <div class="friend-time">${f.h}h ${pad(f.m)}m ce mois</div>
        </div>
        <div class="friend-status ${f.online?'status-on':'status-off'}">
          ${f.online ? '🟢 '+f.host : '🔴 Off'}
        </div>
      </div>
      <div class="friend-detail" id="fd-${i}">
        <div class="detail-grid">
          <div class="detail-item">💰 ${f.wallet}₳</div>
          <div class="detail-item">🎓 Lvl ${f.level}</div>
          <div class="detail-item">⚖️ ${f.eval} pts</div>
          <div class="detail-item">🏊 Pool ${f.pool}</div>
        </div>
        <div class="detail-btns">
          <button class="detail-btn" onclick="openProfile('${f.login}')">👤 Profil</button>
          <button class="detail-btn" onclick="showFriendCal(${i})">📅 Calendrier</button>
        </div>
      </div>
    </div>
  `).join('');
}

function toggleDetail(i) {
  document.getElementById('fd-'+i).classList.toggle('open');
}

/* ── CALENDAR ── */
function colorClass(h) {
  if (h <= 0) return 'c0';
  if (h < 2)  return 'c1';
  if (h < 5)  return 'c2';
  if (h < 7)  return 'c3';
  if (h < 9)  return 'c4';
  return 'c5';
}

function renderCalendar(calData, title, loginKey) {
  currentView = 'calendar';
  document.getElementById('friends-view').style.display = 'none';
  document.getElementById('cal-view').style.display = 'block';
  document.getElementById('section-label').textContent = title.toUpperCase();
  document.getElementById('back-btn').style.display = 'inline-block';

  const calTitle = document.getElementById('cal-title');
  calTitle.textContent = title.toUpperCase();
  calTitle.dataset.login = loginKey || '';

  const grid = document.getElementById('cal-grid');
  grid.innerHTML = '';
  for (let d = 1; d <= myMonthDays; d++) {
    const h = calData[String(d)] || calData[d] || 0;
    const hh = Math.floor(h), mm = Math.round((h - hh) * 60);
    const cls = colorClass(h);
    const dark = cls === 'c4' || cls === 'c5';
    grid.innerHTML += `
      <div class="cal-day ${cls}${dark?' dark-text':''}">
        <div class="cal-num">${d}</div>
        ${h > 0 ? `<div class="cal-val">${hh}h${pad(mm)}</div>` : ''}
      </div>`;
  }
}

function toggleMyCalendar() {
  if (currentView === 'calendar' &&
      document.getElementById('cal-title').dataset.login === '__me__') {
    showFriends();
  } else {
    renderCalendar(myCalData, 'MY HISTORY', '__me__');
  }
}

function showFriendCal(i) {
  renderCalendar(friendsData[i].calendar, friendsData[i].login, friendsData[i].login);
}

function showFriends() {
  currentView = 'friends';
  document.getElementById('friends-view').style.display = 'block';
  document.getElementById('cal-view').style.display = 'none';
  document.getElementById('section-label').textContent = 'FRIENDS STATUS';
  document.getElementById('back-btn').style.display = 'none';
  renderFriends();
}

/* ── SETTINGS ── */
async function loadConfig() {
  const r = await fetch('/api/config');
  cfgCache = await r.json();
}

function openSettings() {
  document.getElementById('modal-overlay').classList.add('open');
  document.getElementById('cfg-user').value = cfgCache.username || '';
  document.getElementById('cfg-uid').value  = cfgCache.api_uid || '';
  document.getElementById('cfg-secret').value = '';
  document.getElementById('cfg-gift').value = cfgCache.gift_days || 0;
  const wd = cfgCache.working_days || [false,true,true,true,true,true,false];
  document.querySelectorAll('#days-grid .day-btn').forEach(btn => {
    btn.classList.toggle('active', wd[+btn.dataset.i]);
    btn.onclick = () => btn.classList.toggle('active');
  });
  renderFriendManage();
}

function renderFriendManage() {
  const list = document.getElementById('fm-list');
  const friends = cfgCache.friends || [];
  list.innerHTML = friends.length
    ? friends.map(f => `
        <div class="fm-row">
          <span>${f}</span>
          <button class="del-btn" onclick="removeFriend('${f}')">🗑</button>
        </div>`).join('')
    : '<div style="color:var(--gray);font-size:11px;padding:4px 0">Aucun ami ajouté</div>';
}

async function addFriend() {
  const login = document.getElementById('new-friend').value.trim();
  if (!login) return;
  await fetch('/api/friend/add', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({login})
  });
  cfgCache.friends = cfgCache.friends || [];
  if (!cfgCache.friends.includes(login)) cfgCache.friends.push(login);
  document.getElementById('new-friend').value = '';
  renderFriendManage();
}

async function removeFriend(login) {
  await fetch('/api/friend/remove', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({login})
  });
  cfgCache.friends = (cfgCache.friends||[]).filter(f => f !== login);
  renderFriendManage();
}

async function saveSettings() {
  const wd = Array.from({length:7}, (_,i) =>
    document.querySelector(`[data-i="${i}"]`).classList.contains('active'));
  const body = {
    username: document.getElementById('cfg-user').value.trim(),
    api_uid:  document.getElementById('cfg-uid').value.trim(),
    gift_days: parseInt(document.getElementById('cfg-gift').value)||0,
    working_days: wd, friends: cfgCache.friends||[],
  };
  const secret = document.getElementById('cfg-secret').value.trim();
  if (secret) body.api_secret = secret;
  await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  await loadConfig();
  closeSettings();
  toast('✓ Sauvegardé !');
  refreshAll();
}

function closeSettings() {
  document.getElementById('modal-overlay').classList.remove('open');
}

/* ── UTILS ── */
function pad(n) { return String(n).padStart(2,'0'); }

function openProfile(login) {
  window.open(`https://profile.intra.42.fr/users/${login}`, '_blank');
}

function openMyProfile() {
  if (cfgCache.username) openProfile(cfgCache.username);
  else { toast('⚙️ Configure ton login'); openSettings(); }
}

async function launchCookie() {
  await fetch('/api/launch_cookie');
  toast('🌐 Connecte-toi à l\'Intra dans le navigateur');
}

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3000);
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeSettings();
});

init();
</script>
</body>
</html>"""

# ── LAUNCH ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    url = f"http://localhost:{PORT}"
    print(f"\n  ╔════════════════════════════════════╗")
    print(f"  ║  42 Dashboard  →  {url}  ║")
    print(f"  ╚════════════════════════════════════╝\n")
    print(f"  Config : {CONFIG_PATH}")
    print(f"  Ctrl+C pour quitter\n")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)