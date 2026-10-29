# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Presenza dei varchi (heartbeat) tenuta in memoria.

Ogni slave manda un heartbeat circa ogni 3 secondi: scriverlo su SQLite ogni
volta significava ~17.000 scritture al giorno per varco, inutili e dannose per
le schede SD e i dischi piccoli. Ora l'ultimo contatto vive qui, in RAM, e viene scritto
nel database solo quando serve:
  - al primo pacchetto dopo l'avvio del backend o dopo un periodo offline
    (cambio di stato: va sul disco subito),
  - altrimenti al massimo una volta ogni INTERVALLO_SCRITTURA_SEC (così dopo un
    riavvio del backend la pagina mostra comunque un "ultimo contatto" recente).

Lo stato online/offline mostrato dalla webapp usa il valore in memoria (vedi
_calcola_stato_online in routers/admin.py). Funziona perché ricezione UDP e
webapp girano nello stesso processo: se un giorno si usassero più worker
uvicorn, questo stato andrebbe condiviso altrove.
"""
import threading
import time
from datetime import datetime
from typing import Optional

# Un dispositivo che non manda pacchetti da più di così è "offline".
SOGLIA_OFFLINE_SEC = 12        # 4 heartbeat da 3 s persi
INTERVALLO_SCRITTURA_SEC = 60

_lock = threading.Lock()
_ultimo: dict[int, datetime] = {}        # varco_id -> istante (UTC naive) dell'ultimo pacchetto valido
_scritto: dict[int, float] = {}          # varco_id -> time.monotonic() dell'ultima scrittura su database
_coda: dict[int, int] = {}               # varco_id -> eventi ancora in coda sullo slave (dall'heartbeat)
_deriva: dict[int, tuple[float, bool]] = {}   # varco_id -> (scarto orologio dello slave in s, corretto automaticamente?)

_scarto: dict[int, tuple[str, str, float]] = {}   # varco_id -> (motivo, ip, istante monotono) dell'ultimo pacchetto scartato
SCARTO_VALIDO_SEC = 30                            # un motivo piu' vecchio non si mostra piu' (il dispositivo e' tacito o a posto)

# Oltre questo scarto l'orologio dello slave e' segnalato come sballato.
SOGLIA_DERIVA_SEC = 10


def segna_contatto(varco_id: int, ora: Optional[datetime] = None) -> bool:
    """Registra un pacchetto valido. Ritorna True se il chiamante deve scrivere
    anche Varco.ultimo_heartbeat nel database (cambio di stato o scrittura
    periodica), False se basta lo stato in memoria."""
    ora = ora or datetime.utcnow()
    adesso_mono = time.monotonic()
    with _lock:
        _scarto.pop(varco_id, None)
        precedente = _ultimo.get(varco_id)
        _ultimo[varco_id] = ora
        ultima_scrittura = _scritto.get(varco_id)
        era_offline = precedente is None or (ora - precedente).total_seconds() > SOGLIA_OFFLINE_SEC
        da_scrivere = (
            era_offline
            or ultima_scrittura is None
            or adesso_mono - ultima_scrittura >= INTERVALLO_SCRITTURA_SEC
        )
        if da_scrivere:
            _scritto[varco_id] = adesso_mono
        return da_scrivere


def segna_scarto(varco_id: int, motivo: str, ip: str) -> None:
    """Un pacchetto del varco e' stato scartato: motivo "firma" (chiave diversa) o "ip" (arriva da un altro indirizzo).
    Serve a dire all'admin PERCHE' il varco risulta offline invece di un generico «nessun pacchetto valido»."""
    with _lock:
        _scarto[varco_id] = (motivo, ip, time.monotonic())


def scarto(varco_id: int) -> Optional[tuple[str, str]]:
    """(motivo, ip) dell'ultimo pacchetto scartato se recente, altrimenti None."""
    with _lock:
        v = _scarto.get(varco_id)
        if v and time.monotonic() - v[2] <= SCARTO_VALIDO_SEC:
            return v[0], v[1]
        return None


def ultimo_contatto(varco_id: int) -> Optional[datetime]:
    with _lock:
        return _ultimo.get(varco_id)


def segna_deriva(varco_id: int, scarto_sec: float, corretta: bool) -> None:
    """Scarto dell'orologio dello slave rispetto al master (positivo = lo slave e'
    avanti). `corretta` = lo slave sta gia' compensando con l'ora ricevuta dal master."""
    with _lock:
        _deriva[varco_id] = (scarto_sec, corretta)


def deriva(varco_id: int) -> Optional[tuple[float, bool]]:
    """(scarto in secondi, corretto automaticamente) oppure None se non misurato."""
    with _lock:
        return _deriva.get(varco_id)


def segna_coda(varco_id: int, eventi_in_coda: int) -> None:
    """Eventi che lo slave non ha ancora consegnato (dichiarati nell'heartbeat)."""
    with _lock:
        _coda[varco_id] = eventi_in_coda


def coda(varco_id: int) -> Optional[int]:
    with _lock:
        return _coda.get(varco_id)


def dimentica(varco_id: int) -> None:
    """Il varco e' stato eliminato: niente stato residuo nel caso un nuovo varco riusi il numero."""
    with _lock:
        for d in (_coda, _deriva, _ultimo, _scritto, _scarto):
            d.pop(varco_id, None)


def azzera() -> None:
    """Solo per i test."""
    with _lock:
        _coda.clear()
        _deriva.clear()
        _ultimo.clear()
        _scritto.clear()
        _scarto.clear()
