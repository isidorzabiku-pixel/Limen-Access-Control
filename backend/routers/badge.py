# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Gestione dei badge oltre la semplice associazione: validita' nel tempo (ospiti,
scadenze), assegnazione di una credenziale letta e non riconosciuta, import da CSV.
"""
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, Depends, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database import get_db
from auth import require_login
from models import AccessMedia, Persona, Evento, EsitoEvento, CredenzialeIgnorata
from services.logger import registra_log
from services import tempo, importa_persone
from templating import templates
from services.lingua import t


def _base_persona(db: Session, persona_id) -> str:
    """Persone e visitatori hanno pagine separate: dopo una modifica si torna a quella giusta."""
    p = db.query(Persona).filter(Persona.id == int(persona_id)).first() if str(persona_id).isdigit() else None
    return "/visitatori" if p is not None and p.ospite else "/persone"

router = APIRouter()


def destinazione_sicura(percorso: str, predefinita: str) -> str:
    """Solo percorsi interni: un redirect_to dal form non deve portare fuori dal sito."""
    percorso = (percorso or "").strip()
    if percorso.startswith("/") and not percorso.startswith("//") and "\\" not in percorso:
        return percorso
    return predefinita


def credenziali_sconosciute_recenti(db: Session, quante: int = 8, giorni: int = 7) -> list[dict]:
    """Ultimi codici letti ai varchi e non riconosciuti (distinti, ancora non assegnati),
    dal piu' recente: servono a collegare in un clic un badge nuovo a una persona."""
    limite = datetime.utcnow() - timedelta(days=giorni)
    eventi = (
        db.query(Evento)
        .filter(Evento.esito == EsitoEvento.negato_credenziale_sconosciuta, Evento.timestamp >= limite,
                Evento.media_codice != None)  # noqa: E711
        .order_by(Evento.timestamp.desc()).limit(300).all()
    )
    visti: dict[str, dict] = {}
    for e in eventi:
        if e.media_codice not in visti:
            visti[e.media_codice] = {"codice": e.media_codice, "timestamp": e.timestamp,
                                      "varco": e.varco.nome if e.varco else "", "volte": 0}
        visti[e.media_codice]["volte"] += 1
    if not visti:
        return []
    gia = {c for (c,) in db.query(AccessMedia.codice).filter(AccessMedia.codice.in_(list(visti))).all()}
    # «Ignora»: nascosta finche' non c'e' una lettura piu' recente dell'ignoramento
    ignorate = {c: t for c, t in db.query(CredenzialeIgnorata.codice, CredenzialeIgnorata.ignorata_at)
                .filter(CredenzialeIgnorata.codice.in_(list(visti))).all()}
    return [v for k, v in visti.items()
            if k not in gia and not (k in ignorate and v["timestamp"] <= ignorate[k])][:quante]


@router.post("/media/ignora")
def ignora_credenziale(
    request: Request,
    codice: str = Form(...),
    redirect_to: str = Form("/persone"),
    db: Session = Depends(get_db),
):
    """«Ignora» su una credenziale sconosciuta: non si vuole assegnare, via dall'elenco."""
    require_login(request, db)
    codice = codice.strip()
    if codice:
        r = db.query(CredenzialeIgnorata).filter(CredenzialeIgnorata.codice == codice).first()
        if r is None:
            db.add(CredenzialeIgnorata(codice=codice, ignorata_at=datetime.utcnow()))
        else:
            r.ignorata_at = datetime.utcnow()
        db.commit()
    return RedirectResponse(destinazione_sicura(redirect_to, "/persone"), status_code=303)


@router.post("/media/assegna")
def assegna_credenziale(
    request: Request,
    codice: str = Form(...),
    persona_id: int = Form(...),
    redirect_to: str = Form("/persone"),
    db: Session = Depends(get_db),
):
    """Associa a una persona un codice appena letto ai varchi e non riconosciuto."""
    require_login(request, db)
    destinazione = destinazione_sicura(redirect_to, "/persone")
    sep = "&" if "?" in destinazione else "?"
    codice = codice.strip()
    persona = db.query(Persona).filter(Persona.id == persona_id).first()
    if not codice or persona is None:
        return RedirectResponse(f"{destinazione}{sep}errore=badge_vuoto", status_code=303)
    if db.query(AccessMedia).filter(AccessMedia.codice == codice).first() is not None:
        return RedirectResponse(f"{destinazione}{sep}errore=badge_duplicato", status_code=303)
    db.add(AccessMedia(codice=codice, persona_id=persona.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(f"{destinazione}{sep}errore=badge_duplicato", status_code=303)
    registra_log(db, t("Credenziale '%(codice)s' assegnata a %(nome)s %(cognome)s (letta ai varchi)", codice=codice, nome=persona.nome, cognome=persona.cognome), categoria="persone")
    return RedirectResponse(f"{destinazione}{sep}assegnato={persona.id}", status_code=303)


@router.post("/media/{media_id}/validita")
def imposta_validita(
    request: Request,
    media_id: int,
    valido_da: str = Form(""),
    valido_a: str = Form(""),
    db: Session = Depends(get_db),
):
    """Periodo di validita' di un badge (ora locale; vuoto = nessun limite)."""
    require_login(request, db)
    m = db.query(AccessMedia).filter(AccessMedia.id == media_id).first()
    if m is None:
        return RedirectResponse("/persone", status_code=303)
    da, a = tempo.da_input_locale(valido_da), tempo.da_input_locale(valido_a)
    if da and a and a < da:
        return RedirectResponse(f"{_base_persona(db, m.persona_id)}?apri={m.persona_id}&scheda=titoli&errore=validita_invertita", status_code=303)
    m.valido_da, m.valido_a = da, a
    db.commit()
    if da or a:
        periodo = f"dal {tempo.formatta(da, '%d/%m/%Y %H:%M') if da else 'sempre'} al {tempo.formatta(a, '%d/%m/%Y %H:%M') if a else 'sempre'}"
    else:
        periodo = t("senza limiti di validita'")
    registra_log(db, t("Credenziale '%(codice)s': validita' %(periodo)s", codice=m.codice, periodo=periodo), categoria="persone")
    return RedirectResponse(f"{_base_persona(db, m.persona_id)}?apri={m.persona_id}&scheda=titoli", status_code=303)


@router.get("/persone/importa", response_class=HTMLResponse)
def pagina_importa(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    return templates.TemplateResponse(
        "importa_persone.html",
        {"request": request, "user": user, "report": None, "colonne": importa_persone.COLONNE,
         "max_righe": importa_persone.MAX_RIGHE},
    )


@router.get("/persone/importa/modello.csv")
def modello_importazione(request: Request, db: Session = Depends(get_db)):
    require_login(request, db)
    return Response(
        content=importa_persone.modello_csv(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="modello-import-persone.csv"'},
    )


@router.post("/persone/importa", response_class=HTMLResponse)
async def esegui_importa(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    user = require_login(request, db)
    dati = await file.read()
    rep = importa_persone.importa(db, dati)
    if not rep["fatale"] or rep["creati"] or rep["aggiornati"]:
        registra_log(
            db,
            t("Import CSV persone: %(p)s create, %(p2)s aggiornate, %(p3)s badge, %(p4)s righe saltate", p=rep['creati'], p2=rep['aggiornati'], p3=rep['badge'], p4=len(rep['errori'])),
            categoria="persone",
        )
    return templates.TemplateResponse(
        "importa_persone.html",
        {"request": request, "user": user, "report": rep, "colonne": importa_persone.COLONNE,
         "max_righe": importa_persone.MAX_RIGHE},
    )
