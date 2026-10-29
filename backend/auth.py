# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Autenticazione minimale per gli utenti della webapp (operatori/admin).
Nessuna libreria di terze parti pesante: bcrypt per l'hash, sessione
cookie-based fornita da Starlette (già dentro FastAPI).
"""
import bcrypt
from fastapi import Request, HTTPException, status
from sqlalchemy.orm import Session

from models import User


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()


def login_user(request: Request, user: User):
    request.session["user_id"] = user.id
    request.session["username"] = user.username


def logout_user(request: Request):
    request.session.clear()


def get_current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


def require_login(request: Request, db: Session) -> User:
    """Da usare come dependency sulle route protette (pagine HTML e API admin)."""
    user = get_current_user(request, db)
    if not user:
        # Richiesta htmx (polling delle tabelle live): un 303 verrebbe seguito
        # dentro l'XHR e la pagina di login finirebbe incollata nel blocco
        # aggiornato. HX-Redirect fa invece navigare l'intera pagina, e il 401
        # evita comunque lo swap se l'header non venisse gestito.
        if request.headers.get("HX-Request") == "true":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, headers={"HX-Redirect": "/login"})
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    # Da qui in poi le righe di log scritte durante questa richiesta portano utente e indirizzo.
    from services.logger import imposta_richiesta
    imposta_richiesta(user.username, request.client.host if request.client else None)
    return user


def require_admin(request: Request, db: Session) -> User:
    """
    Come require_login, ma per le azioni riservate agli amministratori
    (backup, riavvio, pulizia log, gestione utenti): un utente normale che
    ci arriva — link indovinato o salvato — viene rimandato alla dashboard
    invece di vedere un errore grezzo, coerente con come il resto della
    webapp tratta un accesso non consentito.
    """
    user = require_login(request, db)
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/"})
    return user
