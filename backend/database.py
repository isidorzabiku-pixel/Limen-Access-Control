# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Setup del database. SQLite file-based: nessun servizio esterno da gestire,
leggero abbastanza per girare comodamente anche su hardware modesto.
"""
import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

# LIMEN_DATA = cartella dei dati (database, certificati, chiave delle sessioni): la imposta il container Docker.
# Senza, si lavora nella cartella corrente come prima.
DATA_DIR = os.environ.get("LIMEN_DATA")
DATABASE_URL = ("sqlite:///" + (Path(DATA_DIR).resolve() / "access_control.db").as_posix()) if DATA_DIR else "sqlite:///./access_control.db"

# check_same_thread=False serve perché FastAPI gestisce le richieste
# su thread diversi dal threadpool di Starlette.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)



@event.listens_for(engine, "connect")
def _imposta_sqlite(dbapi_connection, _record):
    """WAL: lettori e scrittore non si bloccano a vicenda (la webapp legge mentre
    il thread UDP scrive eventi) e le scritture costano meno sulle schede SD.
    synchronous=NORMAL e' il compromesso consigliato con WAL: nessuna corruzione,
    al massimo si perde l'ultima transazione in caso di mancanza di corrente.
    busy_timeout: attende fino a 5 s un lock invece di fallire subito."""
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependency FastAPI: una sessione DB per richiesta, chiusa sempre a fine richiesta."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
