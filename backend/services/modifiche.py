# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Tiene traccia di QUANDO e' cambiata la configurazione che finisce nella cache dei
dispositivi (persone, badge, gruppi, policy, fasce, festivita', e per ogni varco
attivazione/modalita'/durata), per mostrare "dati non aggiornati" finche' non vengono
rimandati ai varchi con "Scarica dati".

Si aggancia alle sessioni SQLAlchemy: prima del flush guarda cosa e' cambiato, dopo il
commit scrive l'istante nella tabella `impostazioni` (chiavi `cfg_mod:globale` e
`cfg_mod:varco:<id>`, millisecondi come le versioni della cache). Cosi' sopravvive ai
riavvii e non serve toccare i singoli endpoint.
"""
import time
from typing import Optional

from sqlalchemy import event, inspect, text
from sqlalchemy.orm import Session

from database import engine
from models import (
    AccessMedia, FasciaOraria, FasciaOrariaRiga, Festivita, Gruppo, Persona, Policy, Varco,
)

CHIAVE_GLOBALE = "cfg_mod:globale"
PREFISSO_VARCO = "cfg_mod:varco:"

# Modelli la cui modifica qualunque cambia la cache di (potenzialmente) tutti i varchi.
SEMPRE_RILEVANTI = (AccessMedia, FasciaOraria, FasciaOrariaRiga, Festivita, Gruppo, Policy)
# Campi che cambiano a ogni evento o heartbeat e non toccano la cache.
CAMPI_PERSONA = {"attivo", "gruppi", "policy_dirette", "media"}
CAMPI_VARCO = {"attivo", "modalita", "sblocco_fascia_id", "sblocco_fascia", "apertura_durata_sec", "funzionamento"}


def _campi_cambiati(obj) -> set[str]:
    return {a.key for a in inspect(obj).attrs if a.history.has_changes()}


def segna_modifica_globale(session: Session) -> None:
    """Per le modifiche che non passano dal flush ORM (es. cancellazioni in blocco con query.delete())."""
    _segna(session, globale=True)


def _segna(session: Session, globale: bool = False, varco_id: Optional[int] = None) -> None:
    info = session.info.setdefault("cfg_mod", {"globale": False, "varchi": set()})
    if globale:
        info["globale"] = True
    if varco_id is not None:
        info["varchi"].add(varco_id)


@event.listens_for(Session, "before_flush")
def _prima_del_flush(session, _ctx, _istanze):
    for obj in list(session.new) + list(session.deleted):
        if isinstance(obj, SEMPRE_RILEVANTI) or isinstance(obj, Persona):
            _segna(session, globale=True)
        elif isinstance(obj, Varco):
            _segna(session, varco_id=obj.id)   # id ancora None per un varco nuovo: lo gestisce il dopo-flush
            if obj in session.new:
                session.info.setdefault("cfg_mod_nuovi", []).append(obj)
    for obj in session.dirty:
        if isinstance(obj, SEMPRE_RILEVANTI):
            if session.is_modified(obj):
                _segna(session, globale=True)
        elif isinstance(obj, Persona):
            if _campi_cambiati(obj) & CAMPI_PERSONA:
                _segna(session, globale=True)
        elif isinstance(obj, Varco):
            if _campi_cambiati(obj) & CAMPI_VARCO:
                _segna(session, varco_id=obj.id)


@event.listens_for(Session, "after_flush")
def _dopo_il_flush(session, _ctx):
    # I varchi nuovi hanno ora un id.
    for v in session.info.pop("cfg_mod_nuovi", []):
        if v.id is not None:
            _segna(session, varco_id=v.id)


@event.listens_for(Session, "after_commit")
def _dopo_il_commit(session):
    info = session.info.pop("cfg_mod", None)
    if not info:
        return
    ms = int(time.time() * 1000)
    chiavi = ([CHIAVE_GLOBALE] if info["globale"] else []) + [f"{PREFISSO_VARCO}{i}" for i in info["varchi"] if i is not None]
    if not chiavi:
        return
    with engine.begin() as conn:
        for k in chiavi:
            conn.execute(
                text("INSERT INTO impostazioni (chiave, valore) VALUES (:k, :v) "
                     "ON CONFLICT(chiave) DO UPDATE SET valore = excluded.valore"),
                {"k": k, "v": str(ms)},
            )


@event.listens_for(Session, "after_rollback")
def _dopo_il_rollback(session):
    session.info.pop("cfg_mod", None)
    session.info.pop("cfg_mod_nuovi", None)


def stato_dati(db: Session) -> dict:
    """{'da_inviare': [nomi varchi con cache vecchia]}. Vuoto = tutto allineato.
    (Gli invii non confermati non stanno qui: li mostra la pagina Alert.)"""
    righe = dict(db.execute(text("SELECT chiave, valore FROM impostazioni WHERE chiave LIKE 'cfg_mod:%'")).all())
    if not righe:
        return {"da_inviare": []}

    def ms(chiave: str) -> int:
        try:
            return int(righe.get(chiave, 0))
        except ValueError:
            return 0

    globale = ms(CHIAVE_GLOBALE)
    da_inviare = []
    for v in db.query(Varco).order_by(Varco.nome).all():
        modifica = max(globale, ms(f"{PREFISSO_VARCO}{v.id}"))
        inviata = v.ultima_cache_versione or 0
        if modifica > inviata:
            da_inviare.append(v.nome)
    return {"da_inviare": da_inviare}
