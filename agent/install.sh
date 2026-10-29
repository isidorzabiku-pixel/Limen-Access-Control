#!/bin/bash
# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
# Installa lo slave Limen su una Raspberry Pi (Raspberry Pi OS Bookworm o piu' recente):
# dipendenze, copia in /opt/limen-slave, servizio che parte da solo all'avvio.
#   ./install.sh <numero del varco>        (lo mostra la webapp)
set -e
cd "$(dirname "$0")"

VARCO_ID="$1"
if [ -z "$VARCO_ID" ]; then read -rp "Numero del varco (lo mostra la webapp): " VARCO_ID; fi
case "$VARCO_ID" in ''|*[!0-9]*) echo "Il numero del varco deve essere un numero intero."; exit 1;; esac

if ! command -v python3 >/dev/null 2>&1; then
  echo "== Python 3 non c'e': lo installo"
  sudo apt-get update -qq
  sudo apt-get install -y python3
fi
python3 - <<'PY' || { echo "Serve Python 3.9 o piu' recente (Raspberry Pi OS Bullseye o Bookworm): questa versione e' troppo vecchia."; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)
PY

echo "== Dipendenze (GPIO e YAML)"
sudo apt-get update -qq
sudo apt-get install -y python3-gpiozero python3-yaml
# libreria GPIO: lgpio su Bookworm, RPi.GPIO su Bullseye (gpiozero usa quella che trova)
sudo apt-get install -y python3-lgpio 2>/dev/null || sudo apt-get install -y python3-rpi.gpio

DEST=/opt/limen-slave
STATO=/var/lib/limen-slave
UTENTE="${SUDO_USER:-$USER}"

echo "== Copia in $DEST"
sudo mkdir -p "$DEST" "$STATO"
sudo cp -r agent.py gpio_test.py hardware services "$DEST"/
if [ ! -f "$DEST/config.yaml" ]; then
  sudo cp config.pi.yaml "$DEST/config.yaml"
fi
sudo sed -i "s/^varco_id:.*/varco_id: $VARCO_ID/" "$DEST/config.yaml"
sudo chown -R "$UTENTE":"$UTENTE" "$STATO"
sudo usermod -aG gpio "$UTENTE"

echo "== Servizio"
sudo tee /etc/systemd/system/limen-slave.service >/dev/null <<UNIT
[Unit]
Description=Limen slave (varco $VARCO_ID)
After=network-online.target
Wants=network-online.target

[Service]
User=$UTENTE
Group=gpio
WorkingDirectory=$DEST
ExecStart=/usr/bin/python3 -u $DEST/agent.py $DEST/config.yaml
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable limen-slave >/dev/null
sudo systemctl restart limen-slave

echo
echo "Fatto. Slave del varco $VARCO_ID avviato e attivo a ogni accensione."
echo "  Log:           journalctl -u limen-slave -f"
echo "  Config pin:    sudo nano $DEST/config.yaml   (poi: sudo systemctl restart limen-slave)"
echo "  Prova cablaggi: sudo systemctl stop limen-slave && cd $DEST && python3 gpio_test.py config.yaml"
echo "Ora nella webapp (da admin) compare «Dispositivo in attesa»: premi «Abbina»."
