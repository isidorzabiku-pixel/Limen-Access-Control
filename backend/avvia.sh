#!/bin/sh
# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
# Avvia Limen in HTTPS. Al primo avvio crea un certificato autofirmato in backend/certs/
# (valido 10 anni, con nome e indirizzi IP di questo server). Il browser avvisa una volta: si accetta.
# Per usare un certificato vero metti cert.pem e key.pem in backend/certs/ (o imposta CERT_DIR).
# Porta: PORTA (default 8443). Uso: ./avvia.sh
set -e
cd "$(dirname "$0")"
CERT_DIR="${CERT_DIR:-${LIMEN_DATA:+$LIMEN_DATA/certs}}"
CERT_DIR="${CERT_DIR:-certs}"
PORTA="${PORTA:-8443}"
PY="${PYTHON:-venv/bin/python}"
[ -x "$PY" ] || PY="python3"

if [ ! -s "$CERT_DIR/cert.pem" ] || [ ! -s "$CERT_DIR/key.pem" ]; then
  command -v openssl >/dev/null 2>&1 || { echo "Serve openssl per creare il certificato (sudo apt install openssl)." >&2; exit 1; }
  mkdir -p "$CERT_DIR"
  NOME="$(hostname 2>/dev/null || echo limen)"
  SAN="DNS:localhost,DNS:$NOME,IP:127.0.0.1"
  for ip in $(hostname -I 2>/dev/null); do SAN="$SAN,IP:$ip"; done
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -sha256 \
    -keyout "$CERT_DIR/key.pem" -out "$CERT_DIR/cert.pem" \
    -subj "/CN=Limen ($NOME)" -addext "subjectAltName=$SAN" >/dev/null 2>&1
  chmod 600 "$CERT_DIR/key.pem"
  echo "Creato il certificato autofirmato in $CERT_DIR ($SAN)"
fi

export LIMEN_HTTPS=1
exec "$PY" -m uvicorn main:app --host 0.0.0.0 --port "$PORTA" --workers 1 \
  --ssl-keyfile "$CERT_DIR/key.pem" --ssl-certfile "$CERT_DIR/cert.pem"
