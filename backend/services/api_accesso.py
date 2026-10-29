# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Accesso all'API in ingresso: login con utente e password nel body -> token valido 48 ore.

- password: bcrypt (come gli utenti della webapp); token: casuale a 256 bit, nel database solo SHA-256
- limite ai tentativi sbagliati (per indirizzo + utente) per non lasciare provare password all'infinito
- account disattivato/eliminato = i suoi token smettono subito di valere
"""
import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta

import bcrypt
from sqlalchemy.orm import Session

from models import ApiAccount, ApiToken

DURATA_TOKEN = timedelta(hours=48)
MAX_TENTATIVI = 5          # sbagliati...
FINESTRA_SEC = 15 * 60     # ...in questo tempo => blocco fino allo scadere della finestra

_falliti: dict[tuple[str, str], list[float]] = {}
_lock = threading.Lock()
# Hash finto per fare il confronto bcrypt anche quando l'utente non esiste (tempo di risposta uguale).
_FINTO = bcrypt.hashpw(b"x", bcrypt.gensalt()).decode()


def impronta(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def password_casuale() -> str:
    return secrets.token_urlsafe(18)


def crea_account(db: Session, nome: str, username: str) -> tuple[ApiAccount, str]:
    """Crea l'account e restituisce (account, password in chiaro): la password si mostra una volta sola."""
    password = password_casuale()
    acc = ApiAccount(
        nome=nome.strip(), username=username.strip(),
        password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
    )
    db.add(acc)
    db.flush()
    return acc, password


def nuova_password(db: Session, acc: ApiAccount) -> str:
    """Genera una nuova password e revoca tutti i token dell'account."""
    password = password_casuale()
    acc.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    db.query(ApiToken).filter(ApiToken.account_id == acc.id).delete()
    return password


def bloccato(ip: str, username: str) -> int:
    """Secondi di attesa rimasti se ci sono troppi tentativi sbagliati, altrimenti 0."""
    adesso = time.time()
    with _lock:
        lista = [t for t in _falliti.get((ip, username.lower()), []) if adesso - t < FINESTRA_SEC]
        _falliti[(ip, username.lower())] = lista
        if len(lista) >= MAX_TENTATIVI:
            return int(FINESTRA_SEC - (adesso - lista[0])) + 1
    return 0


def _segna_fallito(ip: str, username: str) -> None:
    with _lock:
        _falliti.setdefault((ip, username.lower()), []).append(time.time())
        if len(_falliti) > 5000:      # non far crescere la memoria con nomi inventati
            _falliti.clear()


def _azzera(ip: str, username: str) -> None:
    with _lock:
        _falliti.pop((ip, username.lower()), None)


def accedi(db: Session, ip: str, username: str, password: str) -> tuple[str, datetime] | None:
    """Verifica le credenziali. Se giuste: (token in chiaro, scadenza UTC). Se no: None."""
    acc = db.query(ApiAccount).filter(ApiAccount.username == username).first()
    ok = bcrypt.checkpw(password.encode()[:72], (acc.password_hash if acc else _FINTO).encode())
    if not (acc and ok and acc.attivo):
        _segna_fallito(ip, username)
        return None
    _azzera(ip, username)
    adesso = datetime.utcnow()
    db.query(ApiToken).filter(ApiToken.scade_at < adesso).delete()      # pulizia dei token scaduti
    token = secrets.token_urlsafe(32)
    scade = adesso + DURATA_TOKEN
    db.add(ApiToken(account_id=acc.id, token_hash=impronta(token), scade_at=scade))
    acc.ultimo_uso = adesso
    db.commit()
    return token, scade


def account_da_token(db: Session, token: str) -> ApiAccount | None:
    t = db.query(ApiToken).filter(ApiToken.token_hash == impronta(token)).first()
    if t is None or t.scade_at < datetime.utcnow() or not t.account.attivo:
        return None
    return t.account
