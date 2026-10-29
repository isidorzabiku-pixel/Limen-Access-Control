# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Presenze: chi e' dentro adesso.

Si ricava dai passaggi CONCESSI sui soli varchi con l'opzione "Conta le presenze" e dalla
loro direzione (ingresso/uscita). Non e' un sistema di rilevazione orari ufficiale: senza
almeno un varco di uscita che conta le presenze non si puo' sapere chi e' uscito, quindi
"chi e' dentro" non viene mostrato.
"""
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import Persona, Varco
from services import funzionamento

SOGLIA_SOSPETTO_ORE = 12      # "dentro" da piu' di cosi': probabile uscita non registrata


def configurazione(db: Session) -> dict:
    """Quali varchi contano le presenze e se bastano per sapere chi e' dentro.
    'puo_dentro' = tra i varchi che le contano c'e' almeno un lettore di ingresso E uno di uscita
    (anche sullo stesso varco: un lettore per lato)."""
    varchi = db.query(Varco).filter(Varco.conta_presenze == True).order_by(Varco.nome).all()  # noqa: E712
    n_uscita = n_ingresso = 0
    for v in varchi:
        d = funzionamento.direzioni(v)
        n_ingresso += "ingresso" in d
        n_uscita += "uscita" in d
    return {
        "varchi": varchi, "n_ingresso": n_ingresso, "n_uscita": n_uscita,
        "attive": bool(varchi), "puo_dentro": n_ingresso > 0 and n_uscita > 0,
    }


def chi_e_dentro(db: Session, adesso: datetime | None = None) -> list[dict]:
    """Persone attive con ultima posizione nota 'dentro', dalle piu' recenti."""
    adesso = adesso or datetime.utcnow()
    persone = (
        db.query(Persona)
        .filter(Persona.attivo == True, Persona.presenza_stato == "dentro")  # noqa: E712
        .all()
    )
    out = []
    for p in persone:
        da = p.presenza_aggiornato_at
        ore = (adesso - da).total_seconds() / 3600 if da else None
        out.append({"persona": p, "da": da, "ore": ore, "sospetto": ore is not None and ore >= SOGLIA_SOSPETTO_ORE})
    out.sort(key=lambda r: (r["da"] is None, -(r["da"].timestamp()) if r["da"] else 0))
    return out


def conta_dentro(db: Session) -> int:
    """Quante persone attive risultano dentro (una sola query, per la dashboard)."""
    return db.query(func.count(Persona.id)).filter(
        Persona.attivo == True, Persona.presenza_stato == "dentro"  # noqa: E712
    ).scalar() or 0


def riallinea(db: Session) -> int:
    """Dopo aver cambiato quali varchi contano le presenze (o i loro lettori): se non si puo' piu'
    sapere chi e' dentro, le posizioni ricordate non valgono piu' e si azzerano, altrimenti
    riaccendendo l'opzione settimane dopo comparirebbero persone «dentro» da allora.
    Ritorna quante persone sono state azzerate."""
    if configurazione(db)["puo_dentro"]:
        return 0
    n = db.query(Persona).filter(Persona.presenza_stato.isnot(None)).update(
        {Persona.presenza_stato: None, Persona.presenza_aggiornato_at: None}, synchronize_session=False)
    db.commit()
    return n
