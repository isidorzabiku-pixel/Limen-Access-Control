# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Chiave d'impianto, identificativo dell'impianto e file chiavi del backup.

La chiave d'impianto (32 byte) e' una sola per impianto e protegge sia i telefoni (BLE e NFC) sia le tessere
DESFire: da lei si ricavano, con etichette diverse, il segreto di ogni telefono (HMAC "LIMEN-PHONE") e la chiave
DESFire del sito (HMAC "LIMEN-DESFIRE-v1", vedi firmware/esp32/lib/limen/desfire.h). Il calcolo e' deterministico:
rimettendo la stessa chiave (ripristino) telefoni e tessere continuano a funzionare.
Nel database resta con il nome storico `chiave_telefoni` (vedi services/ble_inviti.py).

Il backup e' fatto di due file: i dati (database senza segreti) e le chiavi (`.limenkey`, JSON con chiave d'impianto
e chiavi dei varchi). Il formato del file chiavi ha una versione: la cifratura con password si potra' aggiungere
senza rompere i backup gia' fatti.
"""
import hashlib
import json
import secrets
from datetime import datetime

from sqlalchemy.orm import Session

from models import AccessMedia, Impostazione, InvitoBle, TipoMedia, Varco
from services import ble_abbinamento as ba

CHIAVE = "chiave_telefoni"              # nome storico nella tabella impostazioni
ID_IMPIANTO = "impianto_id"
CHIAVE_DAL = "chiave_impianto_dal"      # quando e' stata creata o rigenerata (ISO, UTC)
FORMATO_CHIAVI = 1


def _leggi(db: Session, nome: str) -> str:
    riga = db.get(Impostazione, nome)
    return riga.valore if riga is not None else ""


def _scrivi(db: Session, nome: str, valore: str) -> None:
    riga = db.get(Impostazione, nome)
    if riga is None:
        db.add(Impostazione(chiave=nome, valore=valore))
    else:
        riga.valore = valore


def _chiave_valida(valore: str) -> bool:
    try:
        return len(bytes.fromhex(valore)) == 32
    except ValueError:
        return False


def assicura(db: Session) -> None:
    """All'avvio: identificativo dell'impianto e chiave d'impianto ci sono sempre (un master nuovo crea la sua)."""
    cambiato = False
    if not _leggi(db, ID_IMPIANTO):
        _scrivi(db, ID_IMPIANTO, secrets.token_hex(8))
        cambiato = True
    if not _chiave_valida(_leggi(db, CHIAVE)):
        _scrivi(db, CHIAVE, ba.nuova_chiave_telefoni().hex())
        _scrivi(db, CHIAVE_DAL, datetime.utcnow().isoformat(timespec="seconds"))
        cambiato = True
    elif not _leggi(db, CHIAVE_DAL):
        _scrivi(db, CHIAVE_DAL, datetime.utcnow().isoformat(timespec="seconds"))
        cambiato = True
    if cambiato:
        db.commit()


def impronta(chiave: bytes) -> str:
    """Per riconoscere la chiave senza mostrarla: primi 6 byte dello SHA-256, a gruppi di 4 cifre."""
    h = hashlib.sha256(b"LIMEN-FINGERPRINT|" + chiave).hexdigest()[:12].upper()
    return " · ".join(h[i:i + 4] for i in range(0, 12, 4))


def chiave(db: Session) -> bytes:
    assicura(db)
    return bytes.fromhex(_leggi(db, CHIAVE))


def info(db: Session) -> dict:
    dal = _leggi(db, CHIAVE_DAL)
    try:
        dal_dt = datetime.fromisoformat(dal) if dal else None
    except ValueError:
        dal_dt = None
    return {
        "impronta": impronta(chiave(db)),
        "dal": dal_dt,
        "telefoni": db.query(AccessMedia).filter(AccessMedia.tipo == TipoMedia.ble).count(),
        "tessere": db.query(AccessMedia).filter(AccessMedia.tipo == TipoMedia.desfire).count(),
    }


def rigenera(db: Session) -> tuple[int, int]:
    """Chiave nuova (furto di un lettore o altra necessita'): telefoni e tessere DESFire derivano dalla vecchia,
    quindi le loro credenziali si eliminano (telefoni da invitare, tessere da riscrivere). Badge semplici e PIN
    non cambiano. Ritorna (telefoni, tessere). Chi chiama manda poi la cache."""
    telefoni = db.query(AccessMedia).filter(AccessMedia.tipo == TipoMedia.ble).all()
    tessere = db.query(AccessMedia).filter(AccessMedia.tipo == TipoMedia.desfire).all()
    for m in telefoni + tessere:
        db.delete(m)
    for inv in db.query(InvitoBle).all():
        db.delete(inv)
    _scrivi(db, CHIAVE, ba.nuova_chiave_telefoni().hex())
    _scrivi(db, CHIAVE_DAL, datetime.utcnow().isoformat(timespec="seconds"))
    db.commit()
    return len(telefoni), len(tessere)


# ---------- file chiavi del backup ----------

def file_chiavi(db: Session) -> bytes:
    """Contenuto del file .limenkey: tutti i segreti che il backup dei dati non contiene."""
    assicura(db)
    dati = {
        "limen_chiavi": FORMATO_CHIAVI,
        "cifrato": False,
        "impianto_id": _leggi(db, ID_IMPIANTO),
        "creato": datetime.utcnow().isoformat(timespec="seconds"),
        "chiave_impianto": _leggi(db, CHIAVE),
        "chiave_impianto_dal": _leggi(db, CHIAVE_DAL),
        "varchi": {str(v.id): v.api_key for v in db.query(Varco).all()},
    }
    return (json.dumps(dati, indent=2) + "\n").encode()


class FileChiaviNonValido(ValueError):
    pass


def leggi_file_chiavi(contenuto: bytes) -> dict:
    try:
        d = json.loads(contenuto.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FileChiaviNonValido("non è un file chiavi di Limen")
    if not isinstance(d, dict) or "limen_chiavi" not in d:
        raise FileChiaviNonValido("non è un file chiavi di Limen")
    if d.get("limen_chiavi") != FORMATO_CHIAVI:
        raise FileChiaviNonValido("formato del file chiavi non supportato da questa versione")
    if d.get("cifrato"):
        raise FileChiaviNonValido("file chiavi cifrato: non supportato da questa versione")
    if not _chiave_valida(str(d.get("chiave_impianto", ""))) or not isinstance(d.get("varchi"), dict):
        raise FileChiaviNonValido("file chiavi incompleto")
    return d
