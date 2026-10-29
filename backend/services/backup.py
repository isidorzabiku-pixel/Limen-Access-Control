# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Backup del database. Usa l'API di backup nativa di SQLite
(sqlite3.Connection.backup) per uno snapshot consistente anche se il
backend sta scrivendo in quel momento — copiare il file a byte rischierebbe
di leggere uno stato a metà scrittura (una pagina scritta e l'indice non
ancora aggiornato, per esempio).

Il backup contiene solo access_control.db: niente .env, che contiene
SESSION_SECRET e va comunque tenuto fuori da qualunque cosa finisca su un
disco esterno o venga scaricata — stesso motivo per cui non è nel git.
Un ripristino richiede comunque di rigenerarlo (o ricopiarlo) a mano.
"""
import io
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime

from database import DATABASE_URL


def _percorso_db() -> str:
    # DATABASE_URL = "sqlite:///./access_control.db" (percorso relativo alla
    # working directory di uvicorn, cioè backend/: vedi README).
    return DATABASE_URL.removeprefix("sqlite:///")


def crea_backup_zip() -> tuple[str, bytes]:
    """Ritorna (nome_file, contenuto_zip)."""
    percorso = _percorso_db()
    tmp_path = None
    src = sqlite3.connect(percorso)
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        dest = sqlite3.connect(tmp_path)
        try:
            src.backup(dest)
        finally:
            dest.close()
        with open(tmp_path, "rb") as f:
            db_bytes = f.read()
    finally:
        src.close()
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    nome_file = f"backup-access-control-{timestamp}.zip"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("access_control.db", db_bytes)
        zf.writestr(
            "LEGGIMI.txt",
            "Backup del database di Limen.\n"
            f"Creato: {timestamp} UTC\n\n"
            "Per ripristinare:\n"
            "1. Ferma il backend (systemctl stop limen, o Ctrl+C se lo esegui a mano).\n"
            "2. Sostituisci backend/access_control.db con access_control.db di questo archivio.\n"
            "3. Riavvia il backend.\n\n"
            "Non contiene backend/.env (SESSION_SECRET e altri eventuali segreti): va conservato "
            "separatamente, non finisce in questo archivio per lo stesso motivo per cui non è nel git.\n",
        )
    return nome_file, buf.getvalue()
