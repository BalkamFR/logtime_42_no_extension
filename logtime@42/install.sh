#!/bin/bash

# --- CONFIGURATION ---
UUID="logtime@42"
EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$UUID"

# Couleurs
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}🚀 Installation de 42 Dashboard Ultimate (Mode Cluster)...${NC}"

# 0. Dépendances Python pour le Cookie
echo -e "${YELLOW}📦 Installation des dépendances Python (Selenium)...${NC}"
pip3 install --user selenium webdriver-manager psutil

# 1. Nettoyage et Installation
if [ ! -d "$EXT_DIR" ]; then
    mkdir -p "$EXT_DIR"
else
    rm -rf "$EXT_DIR"/*
fi

cp -r * "$EXT_DIR" 2>/dev/null
rm "$EXT_DIR/install.sh" "$EXT_DIR/README.md" 2>/dev/null

# Rendre le script de capture exécutable
chmod +x "$EXT_DIR/capture_cookies.py"

# 2. Compilation et Activation
echo -e "⚙️  Configuration..."
glib-compile-schemas "$EXT_DIR"
gnome-extensions enable "$UUID"

echo -e "${GREEN}✅ Fichiers installés.${NC}"

# 3. LE REDÉMARRAGE (Méthode 42)
echo -e "${RED}🔄 RESET DU SHELL EN COURS...${NC}"
sleep 1

# On vérifie qu'on ne fait pas ça si par hasard tu es sur Wayland (ce qui te déconnecterait)
if [ "$XDG_SESSION_TYPE" == "x11" ]; then
    killall -9 gnome-shell
else
    echo -e "⚠️  Tu n'es pas sous X11. Fais Alt+F2, r, Entrée."
fi

echo -e "\n${YELLOW}⚠️  ACTIONS REQUISES :${NC}"
echo -e "   1. Configure ta clé API (UID/Secret) dans les paramètres de l'extension."
echo -e "   2. Clique sur 'Connexion (Cookie)' dans l'extension pour lier tes évaluations."
echo -e "   👉 ${CYAN}Lis le fichier README.md pour les instructions détaillées !${NC}"