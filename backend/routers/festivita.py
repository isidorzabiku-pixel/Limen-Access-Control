# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""Festivita': elenco, aggiunta, import delle italiane, e fasce che le escludono."""
from datetime import datetime, date
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from auth import require_login
from models import Festivita, FasciaOraria
from services import festivita as servizio, tempo
from services.logger import registra_log
from templating import templates
from services.lingua import t

router = APIRouter()


@router.get("/festivita", response_class=HTMLResponse)
def pagina_festivita(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    oggi = tempo.adesso_locale().date()
    righe = db.query(Festivita).order_by(Festivita.data).all()
    ricorrenti = [r for r in righe if r.ricorrente]
    singole = [r for r in righe if not r.ricorrente]
    fasce = db.query(FasciaOraria).order_by(FasciaOraria.nome).all()
    return templates.TemplateResponse(
        "festivita.html",
        {
            "request": request, "user": user, "ricorrenti": ricorrenti,
            "singole_future": [r for r in singole if r.data >= oggi],
            "singole_passate": [r for r in singole if r.data < oggi],
            "anno": oggi.year, "fasce": fasce,
            "prossime": servizio.date_per_cache(db, oggi),
        },
    )


@router.post("/festivita")
def aggiungi_festivita(
    request: Request, data: str = Form(...), nome: str = Form(...), ricorrente: str = Form(""),
    db: Session = Depends(get_db),
):
    require_login(request, db)
    try:
        giorno = datetime.strptime(data.strip(), "%Y-%m-%d").date()
    except ValueError:
        return RedirectResponse("/festivita?errore=data", status_code=303)
    nome = nome.strip()
    if not nome:
        return RedirectResponse("/festivita?errore=nome", status_code=303)
    esiste = db.query(Festivita).filter(Festivita.data == giorno, Festivita.nome == nome).first()
    if esiste is None:
        db.add(Festivita(data=giorno, nome=nome[:100], ricorrente=bool(ricorrente)))
        db.commit()
        registra_log(db, t("Festività aggiunta: %(nome)s (%(p)s%(p2)s)", nome=nome, p=giorno.strftime('%d/%m/%Y'), p2=t(', ogni anno') if ricorrente else ''), categoria="fasce")
    return RedirectResponse("/festivita?aggiunta=1", status_code=303)


@router.post("/festivita/{festivita_id}/delete")
def elimina_festivita(request: Request, festivita_id: int, db: Session = Depends(get_db)):
    require_login(request, db)
    f = db.query(Festivita).filter(Festivita.id == festivita_id).first()
    if f:
        nome = f.nome
        db.delete(f)
        db.commit()
        registra_log(db, t("Festività eliminata: %(nome)s", nome=nome), categoria="fasce")
    return RedirectResponse("/festivita", status_code=303)


@router.post("/festivita/importa")
def importa_italiane(request: Request, anno: str = Form(""), db: Session = Depends(get_db)):
    require_login(request, db)
    try:
        anno_n = int(anno)
    except ValueError:
        anno_n = tempo.adesso_locale().year
    if not 2000 <= anno_n <= 2100:
        return RedirectResponse("/festivita?errore=anno", status_code=303)
    nuove = servizio.importa_italiane(db, anno_n)
    registra_log(db, t("Importate le festività italiane %(anno_n)s: %(nuove)s nuove", anno_n=anno_n, nuove=nuove), categoria="fasce")
    return RedirectResponse(f"/festivita?importate={nuove}", status_code=303)
