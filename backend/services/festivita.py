# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""Calendario delle festivita': Pasqua, festivita' italiane, e l'elenco di quelle
imminenti che il master manda agli slave con la cache."""
from datetime import date, timedelta

from sqlalchemy.orm import Session

from models import Festivita

# Festivita' nazionali a data fissa (si ripetono ogni anno).
FISSE = [
    (1, 1, "Capodanno"), (1, 6, "Epifania"), (4, 25, "Festa della Liberazione"),
    (5, 1, "Festa del Lavoro"), (6, 2, "Festa della Repubblica"), (8, 15, "Ferragosto"),
    (11, 1, "Ognissanti"), (12, 8, "Immacolata Concezione"), (12, 25, "Natale"), (12, 26, "Santo Stefano"),
]

# Quante festivita' al massimo in cache e per quanti giorni in avanti (spazio nel datagramma).
GIORNI_IN_AVANTI = 400
MAX_IN_CACHE = 60


def pasqua(anno: int) -> date:
    """Domenica di Pasqua (calendario gregoriano, algoritmo di Meeus/Jones/Butcher)."""
    a = anno % 19
    b, c = divmod(anno, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mese, giorno = divmod(h + l - 7 * m + 114, 31)
    return date(anno, mese, giorno + 1)


def festivita_italiane(anno: int) -> list[tuple[date, str, bool]]:
    """[(data, nome, ricorrente)] dell'anno: le fisse (ricorrenti) piu' Pasqua e Lunedi' dell'Angelo."""
    elenco = [(date(anno, m, g), nome, True) for m, g, nome in FISSE]
    p = pasqua(anno)
    elenco.append((p, "Pasqua", False))
    elenco.append((p + timedelta(days=1), "Lunedì dell'Angelo", False))
    return sorted(elenco)


def importa_italiane(db: Session, anno: int) -> int:
    """Aggiunge le festivita' italiane dell'anno che non ci sono gia'. Ritorna quante nuove.
    Le fisse sono ricorrenti: la prima volta valgono per tutti gli anni, quindi non si duplicano."""
    esistenti = {(f.data, f.nome) for f in db.query(Festivita).all()}
    fisse_presenti = {(f.data.month, f.data.day) for f in db.query(Festivita).filter(Festivita.ricorrente == True).all()}  # noqa: E712
    nuove = 0
    for data, nome, ricorrente in festivita_italiane(anno):
        if (data, nome) in esistenti or (ricorrente and (data.month, data.day) in fisse_presenti):
            continue
        db.add(Festivita(data=data, nome=nome, ricorrente=ricorrente))
        nuove += 1
    db.commit()
    return nuove


def date_per_cache(db: Session, oggi: date) -> list[str]:
    """Date festive ("AAAA-MM-GG") da oggi-1 per i prossimi GIORNI_IN_AVANTI giorni, risolte
    (le ricorrenti espanse), ordinate e limitate a MAX_IN_CACHE: lo slave le controlla da solo."""
    from services.policy_engine import carica_festivi
    festivi = carica_festivi(db)
    risultato = []
    for n in range(-1, GIORNI_IN_AVANTI):
        g = oggi + timedelta(days=n)
        if g in festivi:
            risultato.append(g.isoformat())
            if len(risultato) >= MAX_IN_CACHE:
                break
    return risultato
