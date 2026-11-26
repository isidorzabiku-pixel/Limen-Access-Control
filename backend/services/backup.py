# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Backup e ripristino.

Il backup e' fatto di DUE file, da tenere separati:
  - dati  (`limen-dati-<data>.zip`): il database SENZA segreti (chiavi dei varchi sostituite, chiave d'impianto tolta,
    chiavi degli inviti in corso cancellate). Da solo non basta a far funzionare un impianto o a clonarlo.
  - chiavi (`limen-chiavi-<data>.limenkey`): chiave d'impianto e chiavi dei varchi (services/chiavi_impianto.py).
Per ripristinare servono entrambi, dello stesso impianto. I backup di prima (un solo zip con i segreti dentro) si
ripristinano anche da soli.

Lo snapshot usa l'API di backup nativa di SQLite (sqlite3.Connection.backup), consistente anche se il backend sta
scrivendo. Non contiene `.env` / `session_secret` ne' i certificati: si rigenerano da soli.
"""
import io
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime

from database import DATABASE_URL, engine
from services import chiavi_impianto

NOME_DB = "access_control.db"
SEGNAPOSTO_CHIAVE = "da-ripristinare-"   # chiave di un varco tolta dal backup dei dati
FORMATO_DATI = 2                         # 1 = backup di prima, con i segreti nel database


def _percorso_db() -> str:
    # DATABASE_URL = "sqlite:///./access_control.db" (relativo alla cartella di uvicorn) o dentro LIMEN_DATA.
    return DATABASE_URL.removeprefix("sqlite:///")


def _snapshot() -> str:
    """Copia consistente del database in un file temporaneo (da cancellare dopo)."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    src = sqlite3.connect(_percorso_db())
    try:
        dest = sqlite3.connect(tmp)
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()
    return tmp


def _togli_segreti(percorso: str) -> None:
    con = sqlite3.connect(percorso)
    try:
        con.execute(f"UPDATE varchi SET api_key = '{SEGNAPOSTO_CHIAVE}' || id")
        con.execute("DELETE FROM impostazioni WHERE chiave = ?", (chiavi_impianto.CHIAVE,))
        con.execute("UPDATE inviti_ble SET chiave = NULL")
        con.execute("INSERT OR REPLACE INTO impostazioni (chiave, valore) VALUES ('backup_formato', ?)", (str(FORMATO_DATI),))
        con.commit()
        con.execute("VACUUM")                     # i valori tolti non restano nelle pagine libere del file
    finally:
        con.close()


def _data() -> str:
    return datetime.now().strftime("%Y-%m-%d-%H%M")


def crea_backup_dati() -> tuple[str, bytes]:
    """Ritorna (nome_file, zip) con il database senza segreti."""
    tmp = _snapshot()
    try:
        _togli_segreti(tmp)
        with open(tmp, "rb") as f:
            db_bytes = f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    nome = f"limen-dati-{_data()}.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(NOME_DB, db_bytes)
        zf.writestr(
            "LEGGIMI.txt",
            "Backup dei dati di Limen (senza chiavi).\n"
            f"Creato: {datetime.now().isoformat(timespec='minutes')}\n\n"
            "Per ripristinare: Proprietà di sistema > Ripristino, trascinando questo file insieme al file\n"
            "chiavi (.limenkey) dello stesso backup. Senza il file chiavi i dati non bastano.\n",
        )
    return nome, buf.getvalue()


def crea_file_chiavi(db) -> tuple[str, bytes]:
    return f"limen-chiavi-{_data()}.limenkey", chiavi_impianto.file_chiavi(db)


# ---------- ripristino ----------

def _leggi_impostazioni(percorso: str) -> dict:
    con = sqlite3.connect(percorso)
    try:
        return dict(con.execute("SELECT chiave, valore FROM impostazioni").fetchall())
    finally:
        con.close()


def _conta(percorso: str) -> dict:
    con = sqlite3.connect(percorso)
    try:
        def n(sql):
            try:
                return con.execute(sql).fetchone()[0]
            except sqlite3.Error:
                return 0
        return {
            "persone": n("SELECT COUNT(*) FROM persone"),
            "varchi": n("SELECT COUNT(*) FROM varchi"),
            "telefoni": n("SELECT COUNT(*) FROM access_media WHERE tipo = 'ble'"),
            "tessere": n("SELECT COUNT(*) FROM access_media WHERE tipo = 'desfire'"),
            "varchi_ids": [r[0] for r in con.execute("SELECT id FROM varchi").fetchall()],
        }
    finally:
        con.close()


def controlla(zip_bytes: bytes, chiavi_bytes: bytes | None) -> dict:
    """Controlla i file prima del ripristino. Ritorna {"ok": bool, "controlli": [(livello, testo)], "db": percorso
    temporaneo del database da ripristinare, gia' con le chiavi dentro, o None}. livello: ok / avviso / errore.
    I testi sono chiavi da tradurre con i parametri ({"t": ..., "p": {...}})."""
    controlli = []

    def c(livello, testo, **p):
        controlli.append((livello, {"t": testo, "p": p}))

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        db_bytes = zf.read(NOME_DB)
    except (zipfile.BadZipFile, KeyError):
        c("errore", "Il file dei dati non è un backup di Limen (.zip con access_control.db).")
        return {"ok": False, "controlli": controlli, "db": None}
    fd, tmp = tempfile.mkstemp(suffix=".db")
    with os.fdopen(fd, "wb") as f:
        f.write(db_bytes)
    try:
        imp = _leggi_impostazioni(tmp)
        conti = _conta(tmp)
    except sqlite3.Error:
        os.remove(tmp)
        c("errore", "Il database del backup non si legge.")
        return {"ok": False, "controlli": controlli, "db": None}

    formato = int(imp.get("backup_formato", "1") or 1)
    if formato < FORMATO_DATI:
        # Backup di prima: segreti dentro il database, il file chiavi non serve.
        if chiavi_bytes:
            c("avviso", "Backup della versione precedente: le chiavi sono già nel file dei dati, il file chiavi non viene usato.")
        c("ok", "Backup: %(persone)s persone, %(varchi)s varchi.", persone=conti["persone"], varchi=conti["varchi"])
        c("avviso", "I dati attuali di questo master verranno sostituiti.")
        return {"ok": True, "controlli": controlli, "db": tmp}

    if not chiavi_bytes:
        os.remove(tmp)
        c("errore", "Manca il file chiavi (.limenkey).")
        return {"ok": False, "controlli": controlli, "db": None}
    try:
        k = chiavi_impianto.leggi_file_chiavi(chiavi_bytes)
    except chiavi_impianto.FileChiaviNonValido as e:
        os.remove(tmp)
        c("errore", "File chiavi non valido: %(motivo)s.", motivo=str(e))
        return {"ok": False, "controlli": controlli, "db": None}

    if k.get("impianto_id") != imp.get(chiavi_impianto.ID_IMPIANTO):
        os.remove(tmp)
        c("errore", "Dati e chiavi sono di due impianti diversi.")
        return {"ok": False, "controlli": controlli, "db": None}
    c("ok", "Stesso impianto (impronta %(impronta)s).", impronta=chiavi_impianto.impronta(bytes.fromhex(k["chiave_impianto"])))
    c("ok", "Backup: %(persone)s persone, %(varchi)s varchi, %(telefoni)s telefoni, %(tessere)s tessere DESFire.",
      persone=conti["persone"], varchi=conti["varchi"], telefoni=conti["telefoni"], tessere=conti["tessere"])

    dal_dati, dal_chiavi = imp.get(chiavi_impianto.CHIAVE_DAL, ""), k.get("chiave_impianto_dal", "")
    if dal_chiavi and dal_dati and dal_chiavi < dal_dati:
        quando = "/".join(reversed(dal_dati[:10].split("-")))
        c("avviso", "Le chiavi sono di prima di «Rigenera» del %(quando)s: potrebbero essere quelle compromesse.", quando=quando)
    elif dal_chiavi and dal_dati and dal_chiavi > dal_dati:
        c("avviso", "Le chiavi sono state rigenerate dopo questo backup dei dati: telefoni e tessere del backup vanno abbinati e scritti di nuovo.")
    senza = [i for i in conti["varchi_ids"] if str(i) not in k["varchi"]]
    if senza:
        c("avviso", "%(n)s varchi senza chiave nel file: vanno abbinati di nuovo.", n=len(senza))
    c("avviso", "I dati attuali di questo master verranno sostituiti.")

    # Rimette i segreti nel database da ripristinare.
    con = sqlite3.connect(tmp)
    try:
        for vid, api_key in k["varchi"].items():
            con.execute("UPDATE varchi SET api_key = ? WHERE id = ?", (api_key, int(vid)))
        for nome, valore in ((chiavi_impianto.CHIAVE, k["chiave_impianto"]),
                             (chiavi_impianto.CHIAVE_DAL, dal_chiavi or dal_dati),
                             (chiavi_impianto.ID_IMPIANTO, k["impianto_id"])):
            con.execute("INSERT OR REPLACE INTO impostazioni (chiave, valore) VALUES (?, ?)", (nome, valore))
        con.execute("DELETE FROM impostazioni WHERE chiave = 'backup_formato'")
        con.commit()
    finally:
        con.close()
    return {"ok": True, "controlli": controlli, "db": tmp}


def applica(percorso_tmp: str) -> None:
    """Sostituisce il database con quello preparato da controlla(). Chi chiama poi riavvia il processo: le
    connessioni aperte e i dati in memoria (cache dei varchi, sessioni) non valgono piu'."""
    dest = _percorso_db()
    engine.dispose()
    for estensione in ("-wal", "-shm"):
        try:
            os.remove(dest + estensione)
        except OSError:
            pass
    shutil.copyfile(percorso_tmp, dest + ".ripristino")
    os.replace(dest + ".ripristino", dest)
    try:
        os.remove(percorso_tmp)
    except OSError:
        pass
