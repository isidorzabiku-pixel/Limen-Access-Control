# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Helper per registrare log di sistema (audit azioni operatore: creazioni,
modifiche, login/logout; e il diario dei dispositivi: connessioni, pacchetti
scartati, cache). Scrittura semplice e immediata: niente batching, il volume
e' basso. Le righe ripetitive (pacchetti scartati a raffica) passano da
services/connessioni.py, che le limita.

Ogni riga puo' portare:
  - `codice`   : etichetta stabile dell'evento (DEV_ONLINE, PKT_FIRMA...), per filtrare e per i tecnici;
  - `dettagli` : dati strutturati (dict) mostrati nel dettaglio della riga;
  - `utente` e `ip`: chi ha causato l'azione. Se non indicati, si prendono dalla richiesta in corso
    (impostata da require_login), cosi' ogni azione di un operatore e' attribuita senza doverlo
    ricordare a ogni chiamata. Fuori da una richiesta (thread UDP, manutenzione) restano vuoti.
"""
import contextvars
import json
from typing import Optional

from sqlalchemy.orm import Session

from models import LogSistema, LivelloLog

# (utente, ip) della richiesta web in corso; ogni richiesta lavora in un contesto proprio.
_richiesta = contextvars.ContextVar("log_richiesta", default=(None, None))

MAX_DETTAGLI = 2000


def imposta_richiesta(utente: Optional[str], ip: Optional[str]) -> None:
    _richiesta.set((utente, ip))


def _json(dettagli) -> Optional[str]:
    dettagli = {k: v for k, v in (dettagli or {}).items() if v is not None and v != ""}
    if not dettagli:
        return None
    try:
        testo = json.dumps(dettagli, ensure_ascii=False, default=str, sort_keys=False)
    except (TypeError, ValueError):
        return None
    return testo[:MAX_DETTAGLI]


def registra_log(db: Session, messaggio: str, livello: str = "info", categoria: Optional[str] = None,
                 varco_id: Optional[int] = None, commit: bool = True, codice: Optional[str] = None,
                 dettagli: Optional[dict] = None, utente: Optional[str] = None, ip: Optional[str] = None) -> None:
    u, i = _richiesta.get()
    if not codice and (utente or u) and categoria:
        codice = "OP_" + categoria.upper()[:30]      # azione di un operatore sulla webapp
    db.add(LogSistema(
        messaggio=messaggio[:500], livello=LivelloLog(livello), categoria=categoria, varco_id=varco_id,
        codice=(codice or None) and codice[:40], utente=((utente or u) or None) and (utente or u)[:80],
        origine_ip=((ip or i) or None) and (ip or i)[:45], dettagli=_json(dettagli),
    ))
    if commit:
        db.commit()
