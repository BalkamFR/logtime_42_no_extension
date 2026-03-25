const { Adw, Gtk, Gio } = imports.gi; 
const ExtensionUtils = imports.misc.extensionUtils;

function init() {}

function fillPreferencesWindow(window) {
    const settings = ExtensionUtils.getSettings('org.gnome.shell.extensions.logtime');
    const page = new Adw.PreferencesPage();
    
    // --- GROUPE 0 : CREDENTIALS (API & USER) ---
    const groupCreds = new Adw.PreferencesGroup({ title: 'Identification 42' });
    page.add(groupCreds);

    // Username
    const userRow = new Adw.ActionRow({ title: 'Ton Login' });
    const userEntry = new Gtk.Entry({ placeholder_text: 'ex: papilaz', hexpand: true });
    settings.bind('username', userEntry, 'text', 0);
    userRow.add_suffix(userEntry);
    groupCreds.add(userRow);

    // UID
    const uidRow = new Adw.ActionRow({ title: 'API UID' });
    const uidEntry = new Gtk.Entry({ placeholder_text: 'u-s4t2ud...', hexpand: true });
    settings.bind('api-uid', uidEntry, 'text', 0);
    uidRow.add_suffix(uidEntry);
    groupCreds.add(uidRow);

    // Secret (Mode Password)
    const secretRow = new Adw.ActionRow({ title: 'API Secret' });
    const secretEntry = new Gtk.Entry({ 
        placeholder_text: 's-s4t2ud...', 
        hexpand: true,
        visibility: false, // Masquer les caractères
        input_purpose: Gtk.InputPurpose.PASSWORD 
    });
    settings.bind('api-secret', secretEntry, 'text', 0);
    secretRow.add_suffix(secretEntry);
    groupCreds.add(secretRow);


    // --- GROUPE 1 : CONFIGURATION LOGTIME ---
    const groupConfig = new Adw.PreferencesGroup({ title: 'Configuration Logtime' });
    page.add(groupConfig);

    const giftRow = new Adw.ActionRow({ title: 'Jours Offerts (Gift Days)', subtitle: '1 jour = -7h sur l\'objectif' });
    const spinButton = new Gtk.SpinButton();
    spinButton.set_range(0, 31);
    spinButton.set_increments(1, 1);
    spinButton.set_valign(Gtk.Align.CENTER);
    settings.bind('gift-days', spinButton, 'value', 0);
    giftRow.add_suffix(spinButton);
    groupConfig.add(giftRow);

    // --- GROUPE PLANNING ---
    const groupPlanning = new Adw.PreferencesGroup({ title: 'Mon Planning Type' });
    page.add(groupPlanning);

    const daysNames = ['Dimanche', 'Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi'];
    
    // On crée une ligne pour chaque jour
    daysNames.forEach((dayName, index) => {
        const dayRow = new Adw.ActionRow({ title: dayName });
        const toggle = new Gtk.Switch({ valign: Gtk.Align.CENTER });
        
        // Bind settings: day-0, day-1, etc.
        settings.bind(`day-${index}`, toggle, 'active', Gio.SettingsBindFlags.DEFAULT);
        
        dayRow.add_suffix(toggle);
        groupPlanning.add(dayRow);
    });

    // --- GROUPE 2 : GESTION AMIS ---
    const groupAdd = new Adw.PreferencesGroup({ title: 'Gestion des Amis' });
    page.add(groupAdd);

    const addRow = new Adw.ActionRow({ title: 'Ajouter un login' });
    const entry = new Gtk.Entry({ placeholder_text: 'ex: norminet', hexpand: true });
    const addButton = new Gtk.Button({ label: 'Ajouter' });
    
    const box = new Gtk.Box({ spacing: 10 });
    box.append(entry);
    box.append(addButton);
    addRow.add_suffix(box);
    groupAdd.add(addRow);

    // Liste dynamique
    const listGroup = new Adw.PreferencesGroup({ title: 'Mes Amis' });
    page.add(listGroup);

    let currentRows = [];
    const refreshList = () => {
        currentRows.forEach(row => listGroup.remove(row));
        currentRows = [];
        const friends = settings.get_strv('friends-list');
        friends.forEach((friend) => {
            const row = new Adw.ActionRow({ title: friend });
            const delBtn = new Gtk.Button({ icon_name: 'user-trash-symbolic' });
            delBtn.add_css_class('destructive-action');
            delBtn.connect('clicked', () => {
                const newFriends = settings.get_strv('friends-list').filter(f => f !== friend);
                settings.set_strv('friends-list', newFriends);
                refreshList();
            });
            row.add_suffix(delBtn);
            listGroup.add(row);
            currentRows.push(row);
        });
    };

    addButton.connect('clicked', () => {
        const login = entry.get_text().trim();
        if (login.length > 0) {
            const current = settings.get_strv('friends-list');
            if (!current.includes(login)) {
                current.push(login);
                settings.set_strv('friends-list', current);
                entry.set_text('');
                refreshList();
            }
        }
    });

    refreshList();
    window.add(page);
}