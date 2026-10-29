# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Modalita' di funzionamento di un varco: normale, sblocco programmato,
manutenzione, emergenza. La modalita' viaggia nella cache e viene applicata dallo
slave in locale (anche se il master si spegne): per questo ogni cambio manda
subito la cache al varco interessato.
"""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from auth import require_login
from models import Varco, FasciaOraria
from services import udp_agent
from services.logger import registra_log
from routers.badge import destinazione_sicura
from services.lingua import pigro, t

router = APIRouter()

MODALITA = {
    "normale": "normale",
    "sblocco": pigro("sblocco programmato"),
    "manutenzione": "manutenzione",
    "emergenza": pigro("EMERGENZA (porta libera)"),
}


def _applica(db: Session, varco: Varco, modalita: str, fascia_id: int | None, chi: str) -> None:
    precedente = varco.modalita
    varco.modalita = modalita
    varco.sblocco_fascia_id = fascia_id if modalita == "sblocco" else None
    db.commit()
    livello = "warning" if modalita != "normale" or precedente != "normale" else "info"
    extra = ""
    if modalita == "sblocco":
        fascia = db.query(FasciaOraria).filter(FasciaOraria.id == fascia_id).first()
        extra = t(" (fascia '%(nome)s')", nome=fascia.nome) if fascia else ""
    registra_log(
        db, t("Varco '%(nome)s': modalità %(p)s → %(p2)s%(extra)s da %(chi)s", nome=varco.nome, p=MODALITA.get(precedente, precedente), p2=MODALITA[modalita], extra=extra, chi=chi),
        livello=livello, categoria="varchi",
    )


def valida_modalita(db: Session, modalita: str, sblocco_fascia_id: str) -> tuple[str | None, int | None]:
    """Controlla modalita' e fascia di sblocco arrivate da un form. Ritorna (codice_errore, fascia_id):
    codice_errore e' None se va bene, altrimenti il valore per ?errore= ("modalita", "sblocco_senza_fascia")."""
    if modalita not in MODALITA:
        return "modalita", None
    if modalita != "sblocco":
        return None, None
    try:
        fascia_id = int(sblocco_fascia_id)
    except (TypeError, ValueError):
        return "sblocco_senza_fascia", None
    if db.query(FasciaOraria).filter(FasciaOraria.id == fascia_id).first() is None:
        return "sblocco_senza_fascia", None
    return None, fascia_id


@router.post("/varchi/{varco_id}/modalita")
def imposta_modalita(
    request: Request, varco_id: int,
    modalita: str = Form(...), sblocco_fascia_id: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_login(request, db)
    v = db.query(Varco).filter(Varco.id == varco_id).first()
    if v is None:
        return RedirectResponse("/varchi", status_code=303)
    errore, fascia_id = valida_modalita(db, modalita, sblocco_fascia_id)
    if errore:
        return RedirectResponse(f"/varchi?apri={varco_id}&scheda=controllo&errore={errore}", status_code=303)
    _applica(db, v, modalita, fascia_id, user.username)
    # Subito allo slave: l'emergenza non puo' aspettare il giro delle 6 ore.
    udp_agent.invia_cache(db, solo_varco_id=v.id)
    return RedirectResponse(f"/varchi?apri={varco_id}&scheda=controllo&modalita=ok", status_code=303)


@router.post("/varchi/modalita-tutti")
def imposta_modalita_tutti(
    request: Request, modalita: str = Form(...), redirect_to: str = Form("/varchi"),
    db: Session = Depends(get_db),
):
    """Emergenza (o ritorno alla normalita') su TUTTI i varchi in un colpo."""
    user = require_login(request, db)
    destinazione = destinazione_sicura(redirect_to, "/varchi")
    sep = "&" if "?" in destinazione else "?"
    if modalita not in ("emergenza", "normale"):
        return RedirectResponse(f"{destinazione}{sep}errore=modalita", status_code=303)
    varchi = db.query(Varco).all()
    cambiati = 0
    for v in varchi:
        if v.modalita != modalita:
            _applica(db, v, modalita, None, user.username)
            cambiati += 1
    if cambiati:
        udp_agent.invia_cache(db)
    return RedirectResponse(f"{destinazione}{sep}modalita_tutti={modalita}&n={cambiati}", status_code=303)
