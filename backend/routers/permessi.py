# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""Permessi effettivi di una persona e spiegazione "perche' negato?" di un passaggio."""
from datetime import datetime
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from auth import require_login
from models import Evento, Persona
from services import tempo
from services.permessi import permessi_persona, spiega_evento
from templating import templates
from services.lingua import t

router = APIRouter()


@router.get("/persone/{persona_id}/permessi", response_class=HTMLResponse)
def pagina_permessi(request: Request, persona_id: int, db: Session = Depends(get_db)):
    user = require_login(request, db)
    persona = db.query(Persona).filter(Persona.id == persona_id).first()
    if persona is None:
        return RedirectResponse("/persone", status_code=303)
    # ?quando=... (ora locale): "e se provasse a entrare in quel momento?"
    quando_txt = (request.query_params.get("quando") or "").strip()
    quando = tempo.da_input_locale(quando_txt)
    simulato = quando is not None
    quando = quando or datetime.utcnow()
    return templates.TemplateResponse(
        "permessi_persona.html",
        {
            "request": request, "user": user, "persona": persona,
            "permessi": permessi_persona(db, persona, quando), "quando": quando, "simulato": simulato,
            "quando_input": tempo.a_input_locale(quando) if simulato else "",
            "adesso": datetime.utcnow(),
        },
    )


@router.get("/eventi/{evento_id}/perche", response_class=HTMLResponse)
def perche_evento(request: Request, evento_id: int, db: Session = Depends(get_db)):
    """Frammento HTML (caricato da htmx nel drawer di Monitoraggio) con riepilogo e spiegazione di un passaggio."""
    require_login(request, db)
    evento = db.query(Evento).filter(Evento.id == evento_id).first()
    if evento is None:
        return HTMLResponse(t("<p class='muted'>Evento non trovato.</p>"))
    from routers.admin import ESITO_INFO   # import tardivo: admin importa a sua volta altri router
    etichetta, classe = ESITO_INFO.get(evento.esito, (evento.esito.value, "info"))
    return templates.TemplateResponse(
        "_perche.html",
        {"request": request, "spiegazione": spiega_evento(db, evento), "evento": evento,
         "esito_etichetta": etichetta, "esito_classe": classe},
    )
