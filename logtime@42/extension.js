const { St, GLib, Clutter, Soup, GObject, Gio, GdkPixbuf } = imports.gi;
const Main = imports.ui.main;
const PanelMenu = imports.ui.panelMenu;
const PopupMenu = imports.ui.popupMenu;
const ExtensionUtils = imports.misc.extensionUtils;
const Me = ExtensionUtils.getCurrentExtension();
const ByteArray = imports.byteArray;

let _httpSession;
try {
    _httpSession = new Soup.SessionAsync();
    _httpSession.user_agent = 'LogtimeExtension/19.0';
    _httpSession.timeout = 10;
} catch (e) {
    _httpSession = new Soup.Session();
}

const DashboardIndicator = GObject.registerClass(
class DashboardIndicator extends PanelMenu.Button {
    _init() {
        super._init(0.0, "42 Dashboard", false);
        this._settings = ExtensionUtils.getSettings('org.gnome.shell.extensions.logtime');
        
        this._currentLogtimeMs = 0;
        this._friendsCache = {};

        // Listeners
        this._settings.connect('changed::friends-list', () => this._refresh());
        this._settings.connect('changed::gift-days', () => this._refresh()); 
        this._settings.connect('changed::username', () => this._refresh());
        
        for(let i=0; i<7; i++) {
            this._settings.connect(`changed::day-${i}`, () => this._updateTimeLabel()); 
        }

        // --- TOP BAR (Panel) ---
        let topBox = new St.BoxLayout({ y_align: Clutter.ActorAlign.CENTER, style_class: 'lgt-top-box' });
        
        this.onlineBadge = new St.Label({ text: "0", y_align: Clutter.ActorAlign.CENTER, style_class: 'lgt-online-badge' });
        this.onlineBadge.hide();
        topBox.add_child(this.onlineBadge);

        this.buttonLabel = new St.Label({ text: "...", y_align: Clutter.ActorAlign.CENTER, style_class: 'lgt-button-label' });
        topBox.add_child(this.buttonLabel);

        this.add_child(topBox);

        // --- MENU ---
        this.menu.box.style_class = 'lgt-popup-menu';

        this.timeDisplay = new St.Label({ text: "...", style_class: 'lgt-main-time', x_align: Clutter.ActorAlign.CENTER });
        this.menu.box.add_child(this.timeDisplay);

        // STATS BOX (Corrigé, plus de doublons)
        this.statsBox = new St.BoxLayout({ vertical: false, style_class: 'lgt-stats-grid', x_expand: true });
        this.walletLbl = this._createStatBox(this.statsBox, "Wallet", "-");
        this.evalLbl = this._createStatBox(this.statsBox, "Eval", "-");
        this.todayLbl = this._createStatBox(this.statsBox, "Aujourd'hui", "-");
        this.targetDailyLbl = this._createStatBox(this.statsBox, "Cible/J", "-"); 
        this.menu.box.add_child(this.statsBox);

        // NOUVEAU : SCALES BOX
        this.menu.box.add_child(new St.Label({ 
            text: "PROCHAINES DÉFENSES", 
            style_class: 'lgt-stat-label', 
            style: 'margin-top: 10px; font-weight: bold; margin-bottom: 5px; text-align: center;' 
        }));
        
        this.scalesBox = new St.BoxLayout({ vertical: true, style_class: 'lgt-stats-grid', x_expand: true });
        this.menu.box.add_child(this.scalesBox);

        this.menu.box.add_child(new PopupMenu.PopupSeparatorMenuItem());
        
        // --- ACTION ROW ---
        let actionRow = new St.BoxLayout({ vertical: false, style_class: 'lgt-action-row' });
        this.titleLbl = new St.Label({ text: "FRIENDS STATUS", style_class: 'lgt-title', x_expand: true, y_align: Clutter.ActorAlign.CENTER });
        actionRow.add_child(this.titleLbl);

        this.backBtn = new St.Button({ style_class: 'lgt-icon-btn', can_focus: true, visible: false });
        this.backBtn.set_child(new St.Icon({ icon_name: 'go-previous-symbolic', icon_size: 18 }));
        this.backBtn.connect('clicked', () => this._showFriendsView());
        actionRow.add_child(this.backBtn);

        let updateBtn = new St.Button({ style_class: 'lgt-icon-btn warn', can_focus: true });
        updateBtn.set_child(new St.Icon({ icon_name: 'dialog-warning-symbolic.svg', icon_size: 18 }));
        updateBtn.connect('clicked', () => {
            try {
                let cmd = `bash -c "cd ~/goinfre && rm -rf logtime@42 && git clone https://github.com/BalkamFR/logtime.git logtime@42 && cd logtime@42 && chmod +x install.sh && ./install.sh"`;
                GLib.spawn_command_line_async(cmd);
                Main.notify("Logtime", "Réinstallation via ~/goinfre lancée...");
                
                let oldText = this.titleLbl.text;
                this.titleLbl.set_text("INSTALLING...");
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 5000, () => {
                    this.titleLbl.set_text(oldText);
                    return GLib.SOURCE_REMOVE;
                });
            } catch (e) {
                Main.notify("Logtime Error", e.message);
            }
        });
        actionRow.add_child(updateBtn);

        let myProfileBtn = new St.Button({ style_class: 'lgt-icon-btn', can_focus: true });
        myProfileBtn.set_child(new St.Icon({ icon_name: 'avatar-default-symbolic', icon_size: 18 })); 
        myProfileBtn.connect('clicked', () => {
            let user = this._settings.get_string('username');
            if (user) Gio.AppInfo.launch_default_for_uri(`https://profile.intra.42.fr/users/${user}`, null);
            else Main.notify("Logtime", "Configure ton login d'abord !");
        });
        actionRow.add_child(myProfileBtn);

        let calBtn = new St.Button({ style_class: 'lgt-icon-btn', can_focus: true });
        calBtn.set_child(new St.Icon({ icon_name: 'x-office-calendar-symbolic', icon_size: 18 }));
        calBtn.connect('clicked', () => {
             if (this.calendarBox.visible && this.titleLbl.text.includes("MY HISTORY")) this._showFriendsView();
             else this._processHistory(this._myLocsRaw || [], "MY HISTORY");
        });
        actionRow.add_child(calBtn);

        let refreshBtn = new St.Button({ style_class: 'lgt-icon-btn', can_focus: true });
        refreshBtn.set_child(new St.Icon({ icon_name: 'view-refresh-symbolic', icon_size: 18 }));
        refreshBtn.connect('clicked', () => this._refresh());
        actionRow.add_child(refreshBtn);

        let settingsBtn = new St.Button({ style_class: 'lgt-icon-btn', can_focus: true });
        settingsBtn.set_child(new St.Icon({ icon_name: 'emblem-system-symbolic', icon_size: 18 }));
        settingsBtn.connect('clicked', () => ExtensionUtils.openPrefs());
        actionRow.add_child(settingsBtn);

        this.menu.box.add_child(actionRow);

        // --- STACK ---
        this.stack = new St.Widget({ layout_manager: new Clutter.BinLayout(), x_expand: true });
        this.menu.box.add_child(this.stack);

        this.friendsBox = new St.BoxLayout({ vertical: true, x_expand: true });
        this.stack.add_child(this.friendsBox);

        this.calendarBox = new St.BoxLayout({ vertical: true, visible: false, x_expand: true, x_align: Clutter.ActorAlign.FILL });
        this.stack.add_child(this.calendarBox);

        this._refresh();
        this._timeout = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 300, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });
    }

    async _getCookie() {
        let cookiePath = GLib.get_home_dir() + '/.local/share/gnome-shell/extensions/logtime@42/.intra42_cookies.json';
        let file = Gio.File.new_for_path(cookiePath);
        
        if (!file.query_exists(null)) return null;

        try {
            let [success, contents] = file.load_contents(null);
            if (success && contents.length > 0) {
                return ByteArray.toString(contents).trim();
            }
        } catch (e) {
            return null;
        }
        return null;
    }

    _launchCookieCapture() {
        if (this._isCapturing) return;
        this._isCapturing = true;
        
        this.scalesBox.destroy_all_children();
        this.scalesBox.add_child(new St.Label({ text: "⏳ Connexion en cours...", style_class: 'lgt-stat-value', x_align: Clutter.ActorAlign.CENTER }));

        let scriptPath = GLib.get_home_dir() + '/.local/share/gnome-shell/extensions/logtime@42/capture_cookies.py';
        
        try {
            let [success, pid] = GLib.spawn_async(null, ['python3', scriptPath], null, GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.DO_NOT_REAP_CHILD, null);
            if (success) {
                GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid, (pid, status) => {
                    GLib.spawn_close_pid(pid);
                    this._isCapturing = false;
                    this._refresh(); // On relance le rafraîchissement une fois le cookie récupéré
                });
            }
        } catch (e) {
            this._isCapturing = false;
        }
    }

    _createStatBox(parent, title, value) {
        let box = new St.BoxLayout({ vertical: true, style_class: 'lgt-stat-box', x_expand: true, x_align: Clutter.ActorAlign.FILL });
        box.add_child(new St.Label({ text: title, style_class: 'lgt-stat-label', x_align: Clutter.ActorAlign.CENTER }));
        let valLabel = new St.Label({ text: value, style_class: 'lgt-stat-value', x_align: Clutter.ActorAlign.CENTER });
        box.add_child(valLabel);
        parent.add_child(box);
        return valLabel;
    }

    _wait(ms) {
        return new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {
            resolve();
            return GLib.SOURCE_REMOVE;
        }));
    }

    async _getToken() {
        if (this.token && this.tokenExpire > (Date.now()/1000)) return this.token;
        let uid = this._settings.get_string('api-uid');
        let secret = this._settings.get_string('api-secret');
        if (!uid || !secret) { this.buttonLabel.set_text("CONFIG!"); return null; }

        let msg = Soup.Message.new('POST', 'https://api.intra.42.fr/oauth/token');
        let bodyObj = { grant_type: 'client_credentials', client_id: uid, client_secret: secret };
        let bodyStr = JSON.stringify(bodyObj);
        
        if (msg.set_request) {
            msg.set_request('application/json', 2, bodyStr, bodyStr.length);
        } else {
            let bytes = GLib.Bytes.new(ByteArray.fromString(bodyStr));
            msg.set_request_body_from_bytes('application/json', bytes);
        }
        
        let response = await this._send_async(msg);
        if (!response) return null;
        try {
            let data = JSON.parse(response);
            this.token = data.access_token;
            this.tokenExpire = (Date.now()/1000) + data.expires_in;
            return this.token;
        } catch (e) { return null; }
    }

    async _refresh() {
        let username = this._settings.get_string('username');
        if (!username) { this.timeDisplay.set_text("Set Login!"); return; }

        let token = await this._getToken();
        if (!token) return;

        let now = new Date();
        let start = new Date(now.getFullYear(), now.getMonth(), 1).toISOString();
        let end = new Date(now.getFullYear(), now.getMonth() + 1, 0).toISOString();
        
        this._myLocsRaw = await this._fetchJsonPromise(`https://api.intra.42.fr/v2/users/${username}/locations?range[begin_at]=${start},${end}&per_page=100`, token);
        
        if (Array.isArray(this._myLocsRaw)) {
            this._currentLogtimeMs = this._myLocsRaw.reduce((a, l) => a + ((l.end_at ? new Date(l.end_at) : new Date()) - new Date(l.begin_at)), 0);
            
            let todayStr = new Date().toDateString();
            let todayMs = 0;
            this._myLocsRaw.forEach(l => {
                let s = new Date(l.begin_at);
                if (s.toDateString() === todayStr) {
                    let e = l.end_at ? new Date(l.end_at) : new Date();
                    todayMs += (e - s);
                }
            });
            let th = Math.floor(todayMs/3600000);
            let tm = Math.floor((todayMs%3600000)/60000);
            this.todayLbl.set_text(`${th}h${tm.toString().padStart(2,'0')}`);
            this._updateTimeLabel();
        }

        let myStats = await this._fetchJsonPromise(`https://api.intra.42.fr/v2/users/${username}`, token);
        if (myStats) {
            if (myStats.wallet !== undefined) this.walletLbl.set_text(`${myStats.wallet}₳`);
            if (myStats.correction_point !== undefined) this.evalLbl.set_text(`${myStats.correction_point}`);
        }

        await this._updateScales(token, username);

        if (!this.calendarBox.visible) {
            await this._updateFriendsList(token);
        }
    }

    async _updateScales(token, username) {
        this.scalesBox.destroy_all_children();

        // 1. Vérification du cookie
        let cookie = await this._getCookie();
        
        // Si pas de cookie, on affiche le bouton
        if (!cookie) {
            let loginBtn = new St.Button({ style_class: 'lgt-icon-btn', x_align: Clutter.ActorAlign.CENTER, reactive: true, can_focus: true });
            loginBtn.set_child(new St.Label({ text: "🔑 Connexion (Cookie)", style: 'font-weight: bold; padding: 4px;' }));
            loginBtn.connect('clicked', () => this._launchCookieCapture());
            this.scalesBox.add_child(loginBtn);
            return;
        }

        // 2. Si on a le cookie, on tente la requête
        let url = `https://api.intra.42.fr/v2/users/${username}/scale_teams?filter%5Bfuture%5D=true`;
        
        // Création d'une requête manuelle pour y injecter le Cookie EN PLUS du token API
        let msg = Soup.Message.new('GET', url);
        if (msg.request_headers) {
            msg.request_headers.append('Authorization', `Bearer ${token}`);
            msg.request_headers.append('Cookie', `_intra_42_session_production=${cookie}`);
        } else {
            msg.get_request_headers().append('Authorization', `Bearer ${token}`);
            msg.get_request_headers().append('Cookie', `_intra_42_session_production=${cookie}`);
        }
        
        let response = await this._send_async(msg);
        let scales = response ? JSON.parse(response) : null;

        // Si l'API refuse ou que le cookie a expiré (erreur 401 simulée par null)
        if (scales === null || scales.error) {
            // On supprime le cookie expiré
            let cookieFile = Gio.File.new_for_path(GLib.get_home_dir() + '/.local/share/gnome-shell/extensions/logtime@42/.intra42_cookies.json');
            if (cookieFile.query_exists(null)) cookieFile.delete(null);
            
            let btn = new St.Button({ style_class: 'lgt-icon-btn', x_align: Clutter.ActorAlign.CENTER, reactive: true, can_focus: true });
            btn.set_child(new St.Label({ text: "🔄 Cookie expiré - Reconnexion", style: 'color: #ff4757; font-weight: bold; padding: 4px;' }));
            btn.connect('clicked', () => this._launchCookieCapture());
            this.scalesBox.add_child(btn);
            return;
        }

        if (!Array.isArray(scales) || scales.length === 0) {
            this.scalesBox.add_child(new St.Label({ text: "Aucune défense prévue", style_class: 'lgt-stat-value', x_align: Clutter.ActorAlign.CENTER }));
            return;
        }

        // Affichage des corrections
        scales.slice(0, 3).forEach(scale => {
            let date = new Date(scale.begin_at);
            let hours = date.getHours().toString().padStart(2, '0');
            let mins = date.getMinutes().toString().padStart(2, '0');
            let day = date.getDate().toString().padStart(2, '0');
            let month = (date.getMonth() + 1).toString().padStart(2, '0');
            
            let correctorLogin = scale.corrector ? scale.corrector.login : "Anonyme";
            let isCorrector = (correctorLogin === username);
            
            let type = isCorrector ? "💪 Corriger" : "🎓 Être corrigé";
            let color = isCorrector ? '#ff9f43' : '#54a0ff';
            
            let projectName = (scale.scale && scale.scale.name) ? scale.scale.name : "Projet";
            if (scale.team && scale.team.name && projectName === "Projet") projectName = scale.team.name;
            
            let label = new St.Label({ 
                text: `${day}/${month} à ${hours}h${mins}\n${type} (${projectName})`,
                style_class: 'lgt-stat-label',
                style: `color: ${color}; text-align: center; margin-bottom: 6px;`
            });
            this.scalesBox.add_child(label);
        });
    }

    _updateTimeLabel() {
        let h = Math.floor(this._currentLogtimeMs/3600000);
        let m = Math.floor((this._currentLogtimeMs%3600000)/60000);
        let giftDays = this._settings.get_int('gift-days');
        let targetHours = Math.max(0, 154 - (giftDays * 7));
        
        this.buttonLabel.set_text(`${h}h ${m}m / ${targetHours}h`);
        this.timeDisplay.set_text(`Logtime ${h}h ${m}m`);
        this._calculateDailyTarget(targetHours, this._currentLogtimeMs);
    }

    _calculateDailyTarget(targetHours, currentMs) {
        let currentHours = currentMs / 3600000.0;
        let remainingHours = targetHours - currentHours;

        if (remainingHours <= 0) {
            this.targetDailyLbl.set_text("Fini!");
            this.targetDailyLbl.style = "color: #2ed573;";
            return;
        }

        let now = new Date();
        let endOfMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0);
        let todayDate = now.getDate();
        let lastDate = endOfMonth.getDate();
        let workableDaysCount = 0;

        for (let d = todayDate + 1; d <= lastDate; d++) {
            let tempDate = new Date(now.getFullYear(), now.getMonth(), d);
            let dayIndex = tempDate.getDay();
            let isWorkingDay = this._settings.get_boolean(`day-${dayIndex}`);
            if (isWorkingDay) {
                workableDaysCount++;
            }
        }

        let dailyAvg = workableDaysCount > 0 ? (remainingHours / workableDaysCount) : remainingHours;
        let dh = Math.floor(dailyAvg);
        let dm = Math.floor((dailyAvg - dh) * 60);

        this.targetDailyLbl.set_text(`${dh}h${dm.toString().padStart(2,'0')}`);
        this.targetDailyLbl.style = "";
    }

    _processHistory(locations, title) {
        if (!Array.isArray(locations)) locations = [];
        let days = {};
        locations.forEach(l => {
            let start = new Date(l.begin_at);
            let end = l.end_at ? new Date(l.end_at) : new Date();
            let dateKey = start.getDate();
            let dur = end - start;
            if (!days[dateKey]) days[dateKey] = 0;
            days[dateKey] += dur;
        });

        this.friendsBox.hide();
        this.calendarBox.show();
        this.calendarBox.destroy_all_children();
        this.titleLbl.set_text(title.toUpperCase());
        this.backBtn.show();

        let grid = new St.Widget({ layout_manager: new Clutter.GridLayout(), style_class: 'lgt-cal-grid' });
        let layout = grid.layout_manager;
        
        layout.set_column_spacing(2); 
        layout.set_row_spacing(2);

        let now = new Date();
        let daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
        let col = 0; let row = 0;

        for (let d = 1; d <= daysInMonth; d++) {
            let ms = days[d] || 0;
            let hours = ms / 3600000;
            
            let styleClass = 'lgt-cal-day-empty';
            if (hours > 0 && hours < 2) styleClass = 'lgt-cal-day-1';
            else if (hours >= 2 && hours < 5) styleClass = 'lgt-cal-day-2';
            else if (hours >= 5 && hours < 7) styleClass = 'lgt-cal-day-3';
            else if (hours >= 7 && hours < 9) styleClass = 'lgt-cal-day-4';
            else if (hours >= 9) styleClass = 'lgt-cal-day-5';

            let box = new St.BoxLayout({ vertical: true, style_class: `lgt-cal-day ${styleClass}` });
            box.add_child(new St.Label({ text: `${d}`, style_class: 'lgt-cal-day-num', x_align: Clutter.ActorAlign.CENTER }));
            
            if (hours > 0) {
                 let hInt = Math.floor(hours);
                 let mInt = Math.floor((ms % 3600000) / 60000);
                 box.add_child(new St.Label({ text: `${hInt}h${mInt.toString().padStart(2,'0')}`, style_class: 'lgt-cal-day-val', x_align: Clutter.ActorAlign.CENTER }));
            }
            layout.attach(box, col, row, 1, 1);
            col++;
            if (col > 6) { col = 0; row++; }
        }
        this.calendarBox.add_child(grid);
    }

    _showFriendsView() {
        this.calendarBox.hide();
        this.friendsBox.show();
        this.titleLbl.set_text("FRIENDS STATUS");
        this.backBtn.hide();
    }

    async _updateFriendsList(token) {
        this.friendsBox.destroy_all_children();
        let friends = this._settings.get_strv('friends-list');
        
        if (friends.length === 0) {
            this.friendsBox.add_child(new St.Label({ text: "Ajoute tes amis...", style_class: 'lgt-stat-label' }));
            this.onlineBadge.hide();
            return;
        }

        let now = new Date();
        let start = new Date(now.getFullYear(), now.getMonth(), 1).toISOString();
        let end = new Date(now.getFullYear(), now.getMonth() + 1, 0).toISOString();
        let loadedRows = [];
        let onlineCount = 0;

        for (const login of friends) {
            let isActive = false;
            let mainContainer = new St.BoxLayout({ vertical: true, style_class: 'lgt-friend-container', x_expand: true });
            let headerBtn = new St.Button({ style_class: 'lgt-friend-btn', reactive: true, x_expand: true });
            let row = new St.BoxLayout({ vertical: false, x_expand: true });
            
            let iconBin = new St.Bin({ style_class: 'lgt-friend-avatar', y_align: Clutter.ActorAlign.CENTER, width: 54, height: 54 });
            iconBin.set_child(new St.Icon({ icon_name: 'avatar-default-symbolic', icon_size: 54 }));
            row.add_child(iconBin);

            let infoBox = new St.BoxLayout({ vertical: true, style_class: 'lgt-friend-info', x_expand: true, y_align: Clutter.ActorAlign.CENTER });
            let nameLbl = new St.Label({ text: login, style_class: 'lgt-friend-name' });
            let timeLbl = new St.Label({ text: "...", style_class: 'lgt-friend-logtime' });
            infoBox.add_child(nameLbl);
            infoBox.add_child(timeLbl);
            row.add_child(infoBox);

            let statusLbl = new St.Label({ text: "⚫", style_class: 'lgt-friend-status', y_align: Clutter.ActorAlign.CENTER });
            row.add_child(statusLbl);

            headerBtn.set_child(row);
            mainContainer.add_child(headerBtn);

            let detailsBox = new St.BoxLayout({ vertical: true, style_class: 'lgt-friend-details', visible: false, x_expand: true });
            let grid = new St.BoxLayout({ vertical: false, x_expand: true });
            let col1 = new St.BoxLayout({ vertical: true, x_expand: true });
            let detWallet = new St.Label({ text: "Wallet: -", style_class: 'lgt-detail-item' });
            let detPoints = new St.Label({ text: "Eval: -", style_class: 'lgt-detail-item' });
            col1.add_child(detWallet); col1.add_child(detPoints);
            let col2 = new St.BoxLayout({ vertical: true, x_expand: true });
            let detLevel = new St.Label({ text: "Lvl: -", style_class: 'lgt-detail-item' });
            let detPool = new St.Label({ text: "Pool: -", style_class: 'lgt-detail-item' });
            col2.add_child(detLevel); col2.add_child(detPool);
            grid.add_child(col1); grid.add_child(col2);
            detailsBox.add_child(grid);

            let actionsBox = new St.BoxLayout({ vertical: false, style_class: 'lgt-actions-box', x_expand: true });
            let linkBtn = new St.Button({ style_class: 'lgt-link-btn', x_expand: true, x_align: Clutter.ActorAlign.CENTER });
            linkBtn.set_child(new St.Label({ text: "Profil", style_class: 'lgt-link-text' }));
            linkBtn.connect('clicked', () => Gio.AppInfo.launch_default_for_uri(`https://profile.intra.42.fr/users/${login}`, null));
            actionsBox.add_child(linkBtn);

            let friendCalBtn = new St.Button({ style_class: 'lgt-link-btn', x_expand: true, x_align: Clutter.ActorAlign.CENTER });
            friendCalBtn.set_child(new St.Label({ text: "Calendrier", style_class: 'lgt-link-text' }));
            friendCalBtn.connect('clicked', () => {
                let locs = this._friendsCache[login] || [];
                this._processHistory(locs, login);
            });
            actionsBox.add_child(friendCalBtn);

            detailsBox.add_child(actionsBox);
            mainContainer.add_child(detailsBox);
            headerBtn.connect('clicked', () => { detailsBox.visible = !detailsBox.visible; });
            this.friendsBox.add_child(mainContainer);

            try {
                let user = await this._fetchJsonPromise(`https://api.intra.42.fr/v2/users/${login}`, token);
                if (user) {
                    if (user.image?.versions?.small) this._downloadAndSetAvatar(user.image.versions.small, login, iconBin);
                    if (user.wallet !== undefined) detWallet.set_text(`💰 ${user.wallet}₳`);
                    if (user.correction_point !== undefined) detPoints.set_text(`⚖️ ${user.correction_point}`);
                    if (user.pool_year) detPool.set_text(`🏊 ${user.pool_year}`);
                    let c = user.cursus_users.find(x => x.cursus.slug === "42cursus");
                    if (c) detLevel.set_text(`🎓 ${Number(c.level).toFixed(2)}`);
                }
                
                await this._wait(600); 

                let locs = await this._fetchJsonPromise(`https://api.intra.42.fr/v2/users/${login}/locations?range[begin_at]=${start},${end}&per_page=100`, token);
                this._friendsCache[login] = Array.isArray(locs) ? locs : [];

                if (Array.isArray(locs)) {
                    let ams = locs.reduce((a, l) => a + ((l.end_at ? new Date(l.end_at) : new Date()) - new Date(l.begin_at)), 0);
                    let ah = Math.floor(ams/3600000);
                    let am = Math.floor((ams%3600000)/60000);
                    timeLbl.set_text(`${ah}h ${am}m`);

                    let activeSession = locs.find(l => l.end_at === null);
                    if (activeSession) {
                        isActive = true;
                        onlineCount++;
                        statusLbl.set_text(`🟢 ${activeSession.host}`);
                        statusLbl.set_style("color: #2ed573; font-weight: bold;");
                    } else {
                        statusLbl.set_text("🔴 Off");
                        statusLbl.set_style("color: #ff4757;");
                    }
                }
            } catch (err) {
                timeLbl.set_text("Err");
            }

            loadedRows.push({ widget: mainContainer, isOnline: isActive });
            await this._wait(100);
        }

        if (onlineCount > 0) {
            this.onlineBadge.set_text(`${onlineCount}`);
            this.onlineBadge.show();
        } else {
            this.onlineBadge.hide();
        }

        loadedRows.sort((a, b) => (a.isOnline === b.isOnline) ? 0 : (a.isOnline ? -1 : 1));
        loadedRows.forEach((item, index) => this.friendsBox.set_child_at_index(item.widget, index));
    }

    _downloadAndSetAvatar(url, login, iconBin) {
        let tmpPath = GLib.get_tmp_dir() + `/42_avatar_${login}.jpg`;
        let tmpRoundPath = GLib.get_tmp_dir() + `/42_avatar_${login}_round.png`;
        let roundFile = Gio.File.new_for_path(tmpRoundPath);
        if (roundFile.query_exists(null)) {
            iconBin.set_child(new St.Icon({ gicon: new Gio.FileIcon({ file: roundFile }), icon_size: 54 }));
            return;
        }
        let msg = Soup.Message.new('GET', url);
        
        if (_httpSession.queue_message) {
            // Compatibilité Soup 2
            _httpSession.queue_message(msg, (s, m) => {
                if (m.status_code === 200) {
                    try {
                        GLib.file_set_contents(tmpPath, m.response_body.flatten().get_data());
                        this._createRoundImage(tmpPath, tmpRoundPath);
                        iconBin.set_child(new St.Icon({ gicon: new Gio.FileIcon({ file: roundFile }), icon_size: 54 }));
                    } catch(e) {}
                }
            });
        } else {
            // Compatibilité Soup 3 (Ubuntu 22.04 / GNOME 42+)
            _httpSession.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, null, (session, res) => {
                try {
                    let bytes = session.send_and_read_finish(res);
                    if (msg.get_status() === 200) {
                        GLib.file_set_contents(tmpPath, bytes.get_data());
                        this._createRoundImage(tmpPath, tmpRoundPath);
                        iconBin.set_child(new St.Icon({ gicon: new Gio.FileIcon({ file: roundFile }), icon_size: 54 }));
                    }
                } catch(e) {}
            });
        }
    }
    
    _createRoundImage(inputPath, outputPath) {
        const Cairo = imports.cairo;
        const Gdk = imports.gi.Gdk;
        try {
            let pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(inputPath, 54, 54, false);
            let width = pixbuf.get_width(); let height = pixbuf.get_height();
            if (width != height) {
                let size = Math.min(width, height);
                pixbuf = GdkPixbuf.Pixbuf.new_subpixbuf(pixbuf, (width-size)/2, (height-size)/2, size, size);
                pixbuf = pixbuf.scale_simple(54, 54, GdkPixbuf.InterpType.BILINEAR);
            }
            let surface = new Cairo.ImageSurface(Cairo.Format.ARGB32, 54, 54);
            let cr = new Cairo.Context(surface);
            cr.setOperator(Cairo.Operator.CLEAR); cr.paint();
            cr.setOperator(Cairo.Operator.OVER); cr.arc(27, 27, 27, 0, 2 * Math.PI); cr.clip();
            Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0); cr.paint();
            surface.writeToPNG(outputPath);
        } catch(e) {}
    }

    _fetchJsonPromise(url, token) {
        return new Promise(resolve => {
            let msg = Soup.Message.new('GET', url);
            if (msg.request_headers) {
                 msg.request_headers.append('Authorization', `Bearer ${token}`);
            } else {
                 msg.get_request_headers().append('Authorization', `Bearer ${token}`);
            }
            
            this._send_async(msg).then(data => {
                try { resolve(JSON.parse(data)); } catch(e) { resolve(null); }
            });
        });
    }

    _send_async(msg) {
        return new Promise((resolve) => {
            if (_httpSession.queue_message) {
                // Compatibilité Soup 2
                _httpSession.queue_message(msg, (s, m) => resolve((m.response_body && m.response_body.data) ? m.response_body.data : null));
            } else {
                // Compatibilité Soup 3 (Ubuntu 22.04 / GNOME 42+)
                _httpSession.send_and_read_async(msg, GLib.PRIORITY_DEFAULT, null, (session, res) => {
                    try {
                        let bytes = session.send_and_read_finish(res);
                        if (bytes) {
                            resolve(ByteArray.toString(bytes.get_data()));
                        } else {
                            resolve(null);
                        }
                    } catch (e) {
                        resolve(null);
                    }
                });
            }
        });
    }
    
    destroy() {
        if (this._timeout) GLib.source_remove(this._timeout);
        super.destroy();
    }
});

let _indicator;
function init() { return new Extension(); }
class Extension {
    enable() { _indicator = new DashboardIndicator(); Main.panel.addToStatusArea('logtime-indicator', _indicator); }
    disable() { if (_indicator) { _indicator.destroy(); _indicator = null; } }
}