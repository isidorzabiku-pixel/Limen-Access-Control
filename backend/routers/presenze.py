# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""Presenze: dal momento che non c'e' piu' una pagina dedicata, restano solo i pezzi
usati dal pannello "Dentro adesso" della dashboard (CSV e "segna fuori") e il
reindirizzamento della vecchia pagina."""
import csv
import io
from datetime import datetime
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from database import get_db
from auth import require_login
from models import Persona
from services import tempo, presenze
from services.logger import registra_log
from routers.registro import _cella_sicura
from services.lingua import t

router = APIRouter()


@router.get("/presenze")
def vecchia_pagina():
    return RedirectResponse("/?elenco=1", status_code=307)


@router.get("/presenze/dentro.csv")
def esporta_dentro(request: Request, db: Session = Depends(get_db)):
    """Elenco di chi e' dentro in questo momento (utile, ad esempio, per un'evacuazione)."""
    require_login(request, db)
    cfg = presenze.configurazione(db)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    w.writerow([t("Cognome"), t("Nome"), t("Ospite"), t("Entrato"), t("Da (min)"), t("Uscita mancante?")])
    for r in (presenze.chi_e_dentro(db) if cfg["puo_dentro"] else []):
        p = r["persona"]
        w.writerow([_cella_sicura(p.cognome), _cella_sicura(p.nome), "si" if p.ospite else "",
                    tempo.formatta(r["da"]) if r["da"] else "",
                    int(round(r["ore"] * 60)) if r["ore"] is not None else "", "si" if r["sospetto"] else ""])
    nome = f"dentro-{tempo.adesso_locale().strftime('%Y%m%d-%H%M')}.csv"
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@router.post("/presenze/{persona_id}/fuori")
def segna_fuori(request: Request, persona_id: int, db: Session = Depends(get_db)):
    """Toglie una persona dall'elenco "dentro" (es. e' uscita senza strisciare il badge)."""
    user = require_login(request, db)
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if p and p.presenza_stato == "dentro":
        p.presenza_stato = "fuori"
        p.presenza_aggiornato_at = datetime.utcnow()
        db.commit()
        registra_log(db, t("%(nome)s %(cognome)s segnato come fuori da %(username)s", nome=p.nome, cognome=p.cognome, username=user.username), categoria="presenze")
    return RedirectResponse("/?elenco=1", status_code=303)
