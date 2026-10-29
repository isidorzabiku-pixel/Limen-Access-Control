# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Impostazioni di sistema salvate nel database (tabella `impostazioni`), con un
valore di default per ognuna. Un posto solo dove sono elencate, cosi' pagina
Sistema, manutenzione periodica e il resto leggono le stesse chiavi.
"""
from sqlalchemy.orm import Session

from models import Impostazione
from services.lingua import pigro

# chiave -> (default, descrizione)
DEFAULT = {
    # Giorni di conservazione degli eventi di passaggio. 0 = conservali sempre.
    "retention_eventi_giorni": ("0", pigro("Giorni di conservazione dei passaggi (0 = per sempre)")),
    # Giorni di conservazione del log di sistema.
    "retention_log_giorni": ("0", pigro("Giorni di conservazione del log di sistema (0 = per sempre)")),
    # Lingua di chi non ne ha scelta una (it / en).
    "lingua_di_sistema": ("it", pigro("Lingua predefinita dell'interfaccia")),
}


def leggi(db: Session, chiave: str) -> str:
    riga = db.get(Impostazione, chiave)
    return riga.valore if riga else DEFAULT[chiave][0]


def leggi_int(db: Session, chiave: str) -> int:
    try:
        return max(0, int(leggi(db, chiave)))
    except ValueError:
        return int(DEFAULT[chiave][0])


def scrivi(db: Session, chiave: str, valore) -> None:
    if chiave not in DEFAULT:
        raise KeyError(chiave)
    riga = db.get(Impostazione, chiave)
    if riga is None:
        db.add(Impostazione(chiave=chiave, valore=str(valore)))
    else:
        riga.valore = str(valore)
