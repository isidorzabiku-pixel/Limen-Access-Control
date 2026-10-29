# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Manutenzione periodica del database: elimina i passaggi (e, se richiesto, il log
di sistema) piu' vecchi del periodo di conservazione impostato dall'amministratore
in Proprietà di sistema > Conservazione dei dati. Di default non si cancella nulla.

Gli allarmi non ancora presi in carico non vengono mai eliminati dalla pulizia
automatica: sparirebbero senza che nessuno li abbia visti.
"""
import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from database import SessionLocal
from models import Evento, LogSistema, LivelloLog
from services import impostazioni
from services.logger import registra_log
from services.lingua import t

logger = logging.getLogger("manutenzione")

# Soglia minima quando la retention e' attiva: per non azzerare lo storico per un refuso.
MIN_GIORNI = 30
INTERVALLO_SEC = 6 * 3600
RITARDO_INIZIALE_SEC = 300

_stop = threading.Event()
_thread: threading.Thread | None = None


def pulisci_eventi(db: Session, giorni: int, adesso: datetime | None = None) -> int:
    """Elimina i passaggi piu' vecchi di `giorni` giorni (0 = niente). Ritorna quanti."""
    if giorni <= 0:
        return 0
    limite = (adesso or datetime.utcnow()) - timedelta(days=max(giorni, MIN_GIORNI))
    n = (
        db.query(Evento)
        .filter(Evento.timestamp < limite)
        .filter((Evento.allarme == False) | (Evento.gestito_at != None))  # noqa: E712,E711
        .delete(synchronize_session=False)
    )
    db.commit()
    return n


def pulisci_log(db: Session, giorni: int, adesso: datetime | None = None) -> int:
    """Elimina le righe di log di livello 'info' piu' vecchie di `giorni` (warning/error restano)."""
    if giorni <= 0:
        return 0
    limite = (adesso or datetime.utcnow()) - timedelta(days=max(giorni, MIN_GIORNI))
    n = (
        db.query(LogSistema)
        .filter(LogSistema.livello == LivelloLog.info, LogSistema.timestamp < limite)
        .delete(synchronize_session=False)
    )
    db.commit()
    return n


DEBUG_ORE = 24
DEBUG_MAX_RIGHE = 50_000


def pulisci_debug(db: Session, adesso: datetime | None = None) -> int:
    """Le righe «debug» (modalita' diagnostica) non servono a lungo: via dopo 24 h e comunque
    mai piu' di DEBUG_MAX_RIGHE, indipendentemente dalla conservazione impostata."""
    limite = (adesso or datetime.utcnow()) - timedelta(hours=DEBUG_ORE)
    n = db.query(LogSistema).filter(LogSistema.livello == LivelloLog.debug, LogSistema.timestamp < limite).delete(synchronize_session=False)
    totale = db.query(LogSistema).filter(LogSistema.livello == LivelloLog.debug).count()
    if totale > DEBUG_MAX_RIGHE:
        soglia = db.query(LogSistema.id).filter(LogSistema.livello == LivelloLog.debug).order_by(LogSistema.id.desc()).offset(DEBUG_MAX_RIGHE).limit(1).scalar()
        if soglia:
            n += db.query(LogSistema).filter(LogSistema.livello == LivelloLog.debug, LogSistema.id <= soglia).delete(synchronize_session=False)
    db.commit()
    return n


def esegui(db: Session) -> dict:
    """Applica la conservazione impostata. Ritorna {"eventi": n, "log": n}."""
    pulisci_debug(db)
    eventi = pulisci_eventi(db, impostazioni.leggi_int(db, "retention_eventi_giorni"))
    log = pulisci_log(db, impostazioni.leggi_int(db, "retention_log_giorni"))
    if eventi or log:
        registra_log(
            db, t("Pulizia automatica: %(eventi)s passaggi e %(log)s righe di log eliminati per la conservazione impostata", eventi=eventi, log=log),
            livello="warning", categoria="sistema",
        )
    return {"eventi": eventi, "log": log}


def _loop():
    _stop.wait(RITARDO_INIZIALE_SEC)
    while not _stop.is_set():
        db = SessionLocal()
        try:
            esegui(db)
        except Exception:
            logger.exception("Errore nella pulizia periodica")
        finally:
            db.close()
        _stop.wait(INTERVALLO_SEC)


def avvia() -> None:
    global _thread
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="manutenzione", daemon=True)
    _thread.start()


def arresta() -> None:
    _stop.set()
