# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
API in ingresso (/api/v1): programmi esterni -> Limen.

Flusso: POST /api/v1/auth/token con utente e password nel body (JSON) -> token valido 48 ore;
poi `Authorization: Bearer <token>` su tutte le altre chiamate. Scaduto il token si rifà il login.
Gli account API si creano in Proprietà di sistema (solo admin) e hanno accesso completo a tutta l'API
(nessun permesso da gestire: un account o può tutto o è revocato). Documentazione: docs/limen-api.pdf (scaricabile da Proprietà di sistema).
L'API parla inglese (percorsi, campi, valori); il database e il resto del codice restano in italiano: qui sotto si traduce.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models import AccessMedia, ApiAccount, Evento, Persona, Varco
from services import api_accesso, tempo, udp_agent
from services.logger import registra_log

router = APIRouter(prefix="/api/v1", tags=["API"])


# Valori verso l'esterno in inglese; dentro il sistema restano quelli italiani (database, firmware).
RISULTATI = {
    "concesso": "granted",
    "negato_credenziale_sconosciuta": "denied_unknown_credential",
    "negato_fuori_fascia": "denied_outside_schedule",
    "negato_nessuna_policy": "denied_no_rule",
    "negato_credenziale_disattivata": "denied_credential_disabled",
    "negato_anti_passback": "denied_anti_passback",
    "negato_varco_disattivato": "denied_gate_disabled",
    "negato_credenziale_scaduta": "denied_credential_expired",
    "negato_varco_manutenzione": "denied_gate_maintenance",
    "negato_varco_non_autorizzato": "denied_gate_not_authorized",
    "negato_policy_disattivata": "denied_rule_disabled",
    "negato_dati_non_aggiornati": "denied_data_outdated",
    "porta_forzata": "door_forced",
    "porta_lasciata_aperta": "door_left_open",
    "apertura_remota": "remote_open",
    "apertura_comando_esterno": "external_open",
    "ingresso_attivato": "input_activated",
    "uscita_azionata": "output_triggered",
}
MODALITA = {"normale": "normal", "sblocco": "scheduled_unlock", "manutenzione": "maintenance", "emergenza": "emergency"}


# ---------- schemas ----------

class Credentials(BaseModel):
    username: str
    password: str


class NewVisitor(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    valid_until: datetime = Field(description="End of validity, local time ISO 8601 (e.g. 2026-11-03T18:00). Required.")
    valid_from: Optional[datetime] = Field(None, description="Start of validity, local time. Defaults to now.")
    badge_code: Optional[str] = Field(None, max_length=100, description="Badge code to attach right away (optional).")


class NewBadge(BaseModel):
    code: str = Field(min_length=1, max_length=100)


# ---------- authentication ----------

def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def account_corrente(
    request: Request, authorization: str = Header(default=""), db: Session = Depends(get_db),
) -> ApiAccount:
    schema, _, token = authorization.partition(" ")
    acc = api_accesso.account_da_token(db, token.strip()) if schema.lower() == "bearer" and token else None
    if acc is None:
        raise HTTPException(401, "Missing, invalid or expired token: log in again at /api/v1/auth/token",
                            headers={"WWW-Authenticate": "Bearer"})
    return acc


@router.post("/auth/token", summary="Log in: username and password -> token valid for 48 hours")
def login(dati: Credentials, request: Request, db: Session = Depends(get_db)):
    ip = _ip(request)
    attesa = api_accesso.bloccato(ip, dati.username)
    if attesa:
        registra_log(db, f"API: login bloccato per troppi tentativi ({dati.username})", livello="warning",
                     categoria="auth", codice="API_BLOCCO", ip=ip, utente=dati.username)
        raise HTTPException(429, f"Too many failed attempts: try again in {attesa} seconds", headers={"Retry-After": str(attesa)})
    esito = api_accesso.accedi(db, ip, dati.username, dati.password)
    if esito is None:
        registra_log(db, f"API: login fallito per «{dati.username}»", livello="warning", categoria="auth",
                     codice="API_LOGIN_KO", ip=ip, utente=dati.username)
        raise HTTPException(401, "Wrong username or password")
    token, scade = esito
    registra_log(db, f"API: login di «{dati.username}»", categoria="auth", codice="API_LOGIN", ip=ip, utente=dati.username)
    return {"access_token": token, "token_type": "bearer", "expires_in": int(api_accesso.DURATA_TOKEN.total_seconds()),
            "expires_at": scade.isoformat() + "Z"}


# ---------- read ----------

@router.get("/gates", summary="List of gates")
def list_gates(acc: ApiAccount = Depends(account_corrente), db: Session = Depends(get_db)):
    return [{"id": v.id, "name": v.nome, "enabled": v.attivo, "mode": MODALITA.get(v.modalita, v.modalita)}
            for v in db.query(Varco).order_by(Varco.nome)]


def _persona(p: Persona) -> dict:
    return {"id": p.id, "first_name": p.nome, "last_name": p.cognome, "visitor": bool(p.ospite), "active": bool(p.attivo),
            "valid_from": tempo.formatta(tempo.utc_a_locale(p.valido_da), "%Y-%m-%dT%H:%M") if p.valido_da else None,
            "valid_until": tempo.formatta(tempo.utc_a_locale(p.valido_a), "%Y-%m-%dT%H:%M") if p.valido_a else None,
            "badges": [m.codice for m in p.media]}


@router.get("/people", summary="List of people (q = words in first/last name, visitors = true/false)")
def list_people(q: str = "", visitors: Optional[bool] = None, limit: int = 200,
                acc: ApiAccount = Depends(account_corrente), db: Session = Depends(get_db)):
    qs = db.query(Persona)
    if visitors is not None:
        qs = qs.filter(Persona.ospite == visitors)
    for parola in q.split():
        qs = qs.filter((Persona.nome.ilike(f"%{parola}%")) | (Persona.cognome.ilike(f"%{parola}%")))
    return [_persona(p) for p in qs.order_by(Persona.cognome, Persona.nome).limit(max(1, min(limit, 1000)))]


@router.get("/events", summary="Latest passages (since = ISO UTC date-time, limit max 1000)")
def list_events(since: Optional[datetime] = None, gate_id: Optional[int] = None, limit: int = 100,
                acc: ApiAccount = Depends(account_corrente), db: Session = Depends(get_db)):
    qs = db.query(Evento)
    if since:
        qs = qs.filter(Evento.timestamp >= since.replace(tzinfo=None))
    if gate_id:
        qs = qs.filter(Evento.varco_id == gate_id)
    out = []
    for e in qs.order_by(Evento.id.desc()).limit(max(1, min(limit, 1000))):
        out.append({"id": e.id, "timestamp": e.timestamp.isoformat() + "Z", "gate": e.varco.nome if e.varco else e.varco_nome,
                    "result": RISULTATI.get(e.esito.value, e.esito.value), "badge": e.media_codice, "person_id": e.persona_id})
    return out


# ---------- write ----------

@router.post("/visitors", status_code=201, summary="Create a visitor (expiry required)")
def create_visitor(d: NewVisitor, acc: ApiAccount = Depends(account_corrente), db: Session = Depends(get_db)):
    da = tempo.locale_a_utc(d.valid_from.replace(tzinfo=None)) if d.valid_from else None
    a = tempo.locale_a_utc(d.valid_until.replace(tzinfo=None))
    if da and a < da:
        raise HTTPException(422, "valid_until is before valid_from")
    codice = (d.badge_code or "").strip()
    if codice and db.query(AccessMedia).filter(AccessMedia.codice == codice).first():
        raise HTTPException(409, "This badge is already assigned")
    p = Persona(nome=d.first_name.strip(), cognome=d.last_name.strip(), ospite=True, valido_da=da, valido_a=a)
    db.add(p)
    db.flush()
    if codice:
        db.add(AccessMedia(codice=codice, persona_id=p.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This badge is already assigned")
    registra_log(db, f"API: visitatore creato da «{acc.username}»: {p.nome} {p.cognome}", categoria="persone", codice="API_VISITATORE",
                 utente=acc.username)
    return _persona(p)


@router.post("/people/{person_id}/badges", status_code=201, summary="Attach a badge to a person")
def add_badge(person_id: int, d: NewBadge, acc: ApiAccount = Depends(account_corrente), db: Session = Depends(get_db)):
    p = db.get(Persona, person_id)
    if p is None:
        raise HTTPException(404, "Person not found")
    codice = d.code.strip()
    if db.query(AccessMedia).filter(AccessMedia.codice == codice).first():
        raise HTTPException(409, "This badge is already assigned")
    db.add(AccessMedia(codice=codice, persona_id=p.id))
    db.commit()
    registra_log(db, f"API: badge {codice} associato a {p.nome} {p.cognome} da «{acc.username}»", categoria="persone",
                 codice="API_BADGE", utente=acc.username)
    db.refresh(p)
    return _persona(p)


# ---------- open ----------

@router.post("/gates/{gate_id}/open", summary="Open a gate (fire-and-forget: the device must be online)")
def open_gate(gate_id: int, acc: ApiAccount = Depends(account_corrente), db: Session = Depends(get_db)):
    from routers.admin import _calcola_stato_online
    from models import EsitoEvento
    v = db.get(Varco, gate_id)
    if v is None:
        raise HTTPException(404, "Gate not found")
    if not v.attivo:
        raise HTTPException(409, "The gate is disabled")
    if v.ip_address and not _calcola_stato_online([v])[0].online:
        raise HTTPException(503, "The device is offline")
    if not udp_agent.invia_apri(v):
        raise HTTPException(503, "Command not sent: gate IP not configured or network unreachable")
    db.add(Evento(timestamp=datetime.utcnow(), varco_id=v.id, esito=EsitoEvento.apertura_remota))
    db.commit()
    registra_log(db, f"Apertura remota di '{v.nome}' via API da «{acc.username}»", categoria="varchi", varco_id=v.id,
                 codice="API_APRI", utente=acc.username)
    return {"gate_id": v.id, "sent": True}
