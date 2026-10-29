# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Pagine e API della webapp amministrativa. Tutte protette da login.
Server-rendered con Jinja2 + htmx: niente build step, leggero anche su hardware modesto.
"""
import ipaddress
import json
import os
import secrets
import threading
import time as time_module
from datetime import datetime, timedelta, time as dtime
from typing import Optional
from urllib.parse import urlencode
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import or_, and_, func, case
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database import get_db
from auth import require_login, require_admin, hash_password, verify_password
from models import (
    ApiAccount,
    Persona, Gruppo, AccessMedia, Varco, FasciaOraria, FasciaOrariaRiga, Policy, Evento,
    EsitoEvento, LogSistema, LivelloLog, User, TipoMedia,
)
from services.logger import registra_log
from services.system_info import raccogli_info_sistema
from services import lingua as lingua_service
from services import udp_agent, connessioni, log_codici, backup as backup_service, api_accesso
from services.avvio import AVVIATO_IL
from services import modifiche, tempo, presenza, impostazioni, manutenzione, salute, funzionamento as funz_service
from services import presenze as presenze_service, permessi as permessi_service, credenziali as cred_service
from templating import templates
from routers.badge import credenziali_sconosciute_recenti
from routers.modalita import valida_modalita, _applica as _applica_modalita
from services.lingua import pigro, t

router = APIRouter()

# Un dispositivo che non manda heartbeat da più di questo tempo è "offline"
SOGLIA_OFFLINE_SEC = presenza.SOGLIA_OFFLINE_SEC
FINESTRA_REPLAY_SEC = udp_agent.FINESTRA_REPLAY_SEC

GIORNI = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
GIORNI_ESTESI = [pigro(g) for g in ("Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica")]
MESI = [pigro(m) for m in (
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
)]

DIREZIONI = ("ingresso", "uscita")
MODALITA_PASSBACK = ("soft", "hard")

# Etichetta leggibile + classe badge per ogni esito di un Evento (passaggio).
# Usata da monitoraggio, dashboard e alert per non ripetere la mappatura.
ESITO_INFO = {
    EsitoEvento.concesso: (pigro("Accesso consentito"), "ok"),
    EsitoEvento.negato_credenziale_sconosciuta: (pigro("Credenziale non riconosciuta"), "err"),
    EsitoEvento.negato_fuori_fascia: (pigro("Fuori fascia oraria"), "err"),
    EsitoEvento.negato_nessuna_policy: (pigro("Nessuna regola assegnata"), "err"),
    EsitoEvento.negato_varco_non_autorizzato: (pigro("Non autorizzato su questo varco"), "err"),
    EsitoEvento.negato_policy_disattivata: (pigro("Regola disattivata"), "err"),
    EsitoEvento.negato_dati_non_aggiornati: (pigro("Dati del varco non aggiornati"), "err"),
    EsitoEvento.negato_credenziale_disattivata: (pigro("Credenziale disattivata"), "err"),
    EsitoEvento.negato_anti_passback: (pigro("Bloccato da anti-passback"), "err"),
    EsitoEvento.negato_varco_disattivato: (pigro("Varco disattivato"), "err"),
    EsitoEvento.negato_credenziale_scaduta: (pigro("Credenziale scaduta o non ancora valida"), "err"),
    EsitoEvento.negato_varco_manutenzione: (pigro("Varco in manutenzione"), "err"),
    EsitoEvento.porta_forzata: (pigro("Porta forzata"), "err"),
    EsitoEvento.porta_lasciata_aperta: (pigro("Porta lasciata aperta"), "err"),
    EsitoEvento.apertura_remota: (pigro("Apertura remota da webapp"), "info"),
    EsitoEvento.apertura_comando_esterno: (pigro("Apertura da comando esterno"), "info"),
    EsitoEvento.ingresso_attivato: (pigro("Ingresso attivato"), "info"),
    EsitoEvento.uscita_azionata: (pigro("Uscita azionata da webapp"), "info"),
}

# Griglia fasce orarie: slot da 30 minuti (0..47 = 00:00..23:30)
SLOT_MINUTI = 30
SLOT_PER_ORA = 60 // SLOT_MINUTI
TOTALE_SLOT = 24 * SLOT_PER_ORA


def _slot_a_time(slot: int) -> dtime:
    """Converte un indice di slot (0..47) nell'orario dtime corrispondente."""
    if slot >= TOTALE_SLOT:
        return dtime(hour=23, minute=59)
    return dtime(hour=slot // SLOT_PER_ORA, minute=(slot % SLOT_PER_ORA) * SLOT_MINUTI)


def _etichetta_slot(slot: int) -> str:
    return f"{slot // SLOT_PER_ORA:02d}:{(slot % SLOT_PER_ORA) * SLOT_MINUTI:02d}"


def _label_policy(p) -> str:
    """Etichetta leggibile di una policy, usata nei tag-picker e altrove."""
    nome = p.nome or t("Regola #%(id)s", id=p.id)
    fascia = p.fascia_nome
    return t("%(nome)s — %(nomi_varchi)s (%(fascia)s)", nome=nome, nomi_varchi=p.nomi_varchi, fascia=fascia)


def _calcola_stato_online(varchi):
    """Imposta v.online e v.ultimo_contatto (attributi NON persistiti): l'ultimo
    contatto reale sta in memoria (services/presenza.py), il valore nel database
    e' aggiornato solo ogni tanto. Si prende il piu' recente dei due."""
    now = datetime.utcnow()
    for v in varchi:
        candidati = [d for d in (v.ultimo_heartbeat, presenza.ultimo_contatto(v.id)) if d is not None]
        v.ultimo_contatto = max(candidati) if candidati else None
        v.online = bool(
            v.ultimo_contatto and (now - v.ultimo_contatto) < timedelta(seconds=SOGLIA_OFFLINE_SEC)
        )
        # Orologio dello slave (misurato sugli heartbeat): scarto in secondi,
        # solo se oltre soglia. `corretto` = lo slave lo sta gia' compensando.
        misura = presenza.deriva(v.id)
        v.sensore_porta = funz_service.sensore_porta_attivo(v)
        v.deriva_sec = None
        v.deriva_corretta = False
        v.scarto_breve, v.scarto_testo = None, None
        sc = None if v.online else presenza.scarto(v.id)
        if sc:
            v.scarto_breve, v.scarto_testo = descrivi_scarto(v, *sc)
        if misura and abs(misura[0]) >= presenza.SOGLIA_DERIVA_SEC:
            v.deriva_sec, v.deriva_corretta = misura
    return varchi


def descrivi_scarto(v, motivo: str, ip: str) -> tuple[str, str]:
    """(etichetta breve, spiegazione) del perche' i pacchetti di un varco silenzioso vengono scartati."""
    if motivo == "ip":
        return (t("arriva da %(ip)s", ip=ip),
                t("Arrivano pacchetti da %(ip)s ma il varco e' registrato con l'IP %(p)s: correggi l'IP del varco oppure quello del dispositivo (gli indirizzi sono fissi).", ip=ip, p=v.ip_address or t('nessuno')))
    return (t("chiave diversa"),
            t("Il dispositivo %(ip)s scrive ma con una chiave diversa da quella del master (varco ricreato o chiave reimpostata). Quando compare «Dispositivo in attesa» premi Abbina; se non compare, sul dispositivo usa «Dimentica abbinamento».", ip=ip))


def descrivi_deriva(sec: float) -> str:
    """'avanti di 1 h 00 min' / 'indietro di 25 s' per un orologio sballato."""
    s = int(abs(sec))
    if s >= 3600:
        durata = f"{s // 3600} h {(s % 3600) // 60:02d} min"
    elif s >= 60:
        durata = f"{s // 60} min {s % 60:02d} s"
    else:
        durata = f"{s} s"
    return t("avanti di %(durata)s", durata=durata) if sec > 0 else t("indietro di %(durata)s", durata=durata)


# Un allarme non gestito resta nell'elenco attivo solo per questo tempo: oltre,
# smette di gonfiare il contatore anche se nessuno l'ha preso in carico (resta
# comunque visibile nel Monitoraggio). Evita il "contatore fisso per sempre".
SCADENZA_ALLARMI_ORE = 72


def _titolo_allarme(e: Evento) -> str:
    nome = e.varco.nome if e.varco else (e.varco_nome or "—")   # varco eliminato: i passaggi restano con il suo nome
    if e.esito in (EsitoEvento.concesso, EsitoEvento.negato_anti_passback):
        return t("Allarme anti-passback: %(nome)s", nome=nome)
    etichetta, _ = ESITO_INFO.get(e.esito, (e.esito.value, "err"))
    return f"{etichetta}: {nome}"


def _calcola_alert(db: Session):
    """Alert di sistema calcolati al volo (nessuna tabella dedicata):
    dispositivi offline + allarmi degli ultimi SCADENZA_ALLARMI_ORE non ancora
    presi in carico (es. violazione anti-passback). Gli offline si risolvono da
    soli quando il dispositivo torna; gli allarmi di un evento si chiudono con
    "Prendi in carico" (vedi /alert)."""
    alert = []

    varchi = _calcola_stato_online(db.query(Varco).all())
    for v in varchi:
        if not v.online:
            # «In attesa» = mai arrivato nulla, nemmeno un pacchetto scartato per l'orologio.
            mai_visto = not (v.ultimo_contatto or v.deriva_sec is not None)
            descrizione = (
                t("Ultimo contatto: %(p)s", p=tempo.formatta(v.ultimo_contatto))
                if v.ultimo_contatto else (t("Il dispositivo non si e' ancora collegato (varco appena creato o non ancora abbinato)") if mai_visto else t("Nessun pacchetto valido ricevuto"))
            )
            if v.scarto_testo:
                descrizione = v.scarto_testo
            if v.deriva_sec is not None and abs(v.deriva_sec) > FINESTRA_REPLAY_SEC:
                descrizione += (
                    t(". Il dispositivo risponde ma il suo orologio e' %(p)s: i suoi pacchetti vengono scartati finche' non si riallinea (riceve l'ora con la cache).", p=descrivi_deriva(v.deriva_sec))
                )
            alert.append({
                "livello": "warning" if mai_visto else "error",
                "titolo": t("In attesa del dispositivo: %(nome)s", nome=v.nome) if mai_visto else t("Dispositivo offline: %(nome)s", nome=v.nome),
                "descrizione": descrizione,
                "timestamp": v.ultimo_contatto,
                "evento_id": None,
            })
        if v.modalita == "emergenza":
            alert.append({
                "livello": "error", "titolo": t("Emergenza attiva: %(nome)s", nome=v.nome),
                "descrizione": t("La porta è libera per tutti (nessun badge richiesto) finché non si torna alla modalità normale."),
                "timestamp": None, "evento_id": None,
            })
        elif v.modalita == "manutenzione":
            alert.append({
                "livello": "warning", "titolo": t("Varco in manutenzione: %(nome)s", nome=v.nome),
                "descrizione": t("Nessun badge apre questo varco (resta l'apertura dalla webapp)."),
                "timestamp": None, "evento_id": None,
            })
        if v.online and v.ultima_cache_versione and v.ultima_cache_confermata != v.ultima_cache_versione:
            # Se e' offline basta l'alert "Dispositivo offline": questo compare solo
            # quando il dispositivo c'e' ma non ha risposto all'ultimo invio.
            alert.append({
                "livello": "warning",
                "titolo": t("Invio dati non confermato: %(nome)s", nome=v.nome),
                "descrizione": t("Il dispositivo e' online ma non ha confermato l'ultimo invio dei dati. Prova a premere \"Scarica dati\"."),
                "timestamp": None,
                "evento_id": None,
            })
        if v.online and v.deriva_sec is not None and not v.deriva_corretta:
            alert.append({
                "livello": "warning",
                "titolo": t("Orologio sballato: %(nome)s", nome=v.nome),
                "descrizione": (
                    t("L'orologio del dispositivo e' %(p)s rispetto al master. Premi \"Scarica dati\": con la cache riceve l'ora corretta e il fuso orario.", p=descrivi_deriva(v.deriva_sec))
                ),
                "timestamp": None,
                "evento_id": None,
            })

    limite = datetime.utcnow() - timedelta(hours=SCADENZA_ALLARMI_ORE)
    allarmi = (
        db.query(Evento)
        .filter(Evento.allarme == True, Evento.gestito_at == None, Evento.timestamp >= limite)  # noqa: E712,E711
        .order_by(Evento.timestamp.desc())
        .limit(50)
        .all()
    )
    for e in allarmi:
        etichetta, _ = ESITO_INFO.get(e.esito, (e.esito.value, "err"))
        chi = f" · {e.persona.nome} {e.persona.cognome}" if e.persona else ""
        alert.append({
            "livello": "warning",
            "titolo": _titolo_allarme(e),
            "descrizione": etichetta + chi,
            "timestamp": e.timestamp,
            "evento_id": e.id,
        })

    alert.sort(key=lambda a: a["timestamp"] or datetime.min, reverse=True)
    return alert


def _allarmi_gestiti_di_recente(db: Session, limite: int = 15):
    return (
        db.query(Evento)
        .filter(Evento.allarme == True, Evento.gestito_at != None)  # noqa: E712,E711
        .order_by(Evento.gestito_at.desc())
        .limit(limite)
        .all()
    )


# ---------- Dashboard ----------

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    now = tempo.adesso_locale()
    varchi = _calcola_stato_online(db.query(Varco).all())
    ultimi_eventi = db.query(Evento).order_by(Evento.timestamp.desc()).limit(8).all()

    varchi_offline = [v for v in varchi if not v.online]
    alert = _calcola_alert(db)

    # Conteggi di oggi: giorno di calendario in ora locale (non UTC), convertito
    # in UTC per confrontarlo con Evento.timestamp. Tutto calcolato dal database
    # (COUNT/GROUP BY): niente caricamento di tutti gli eventi in memoria.
    # Gli eventi del sensore porta sono allarmi, non passaggi: restano fuori dai conteggi.
    ok = (EsitoEvento.concesso, EsitoEvento.apertura_remota, EsitoEvento.apertura_comando_esterno)
    # Ingressi attivati e uscite azionate dalla webapp non sono passaggi: restano fuori dai conteggi come gli allarmi porta.
    porta = (EsitoEvento.porta_forzata, EsitoEvento.porta_lasciata_aperta, EsitoEvento.ingresso_attivato, EsitoEvento.uscita_azionata)

    def conta(inizio, fine=None):
        q = db.query(func.count(Evento.id), func.sum(case((Evento.esito.in_(ok), 1), else_=0))).filter(
            Evento.timestamp >= inizio, Evento.esito.notin_(porta))
        if fine is not None:
            q = q.filter(Evento.timestamp < fine)
        totale, concessi = q.one()
        return int(totale or 0), int(concessi or 0)

    inizio_oggi = tempo.inizio_oggi_utc()
    passaggi_oggi, concessi_oggi = conta(inizio_oggi)
    negati_oggi = passaggi_oggi - concessi_oggi

    oggi_label = t("%(p)s %(day)s %(p2)s", p=GIORNI_ESTESI[now.weekday()], day=now.day, p2=MESI[now.month - 1])

    # Motivi dei negati di oggi: un GROUP BY, piu' frequenti per primi.
    motivi_rows = (
        db.query(Evento.esito, func.count(Evento.id))
        .filter(Evento.timestamp >= inizio_oggi, Evento.esito.notin_(ok + porta))
        .group_by(Evento.esito).order_by(func.count(Evento.id).desc()).all()
    )
    motivi = [
        {"nome": esito.name, "etichetta": ESITO_INFO.get(esito, (esito.value, "err"))[0], "n": n}
        for esito, n in motivi_rows
    ]
    massimo_motivi = max((m["n"] for m in motivi), default=0) or 1

    # Ultimi 7 giorni (oggi compreso), un conteggio per giorno locale; i giorni senza eventi restano a zero.
    grafico = []
    for k in range(6, -1, -1):
        inizio = tempo.inizio_giorno_locale_utc(k)
        fine = tempo.inizio_giorno_locale_utc(k - 1) if k > 0 else None
        totale, concessi = conta(inizio, fine)
        giorno = now.date() - timedelta(days=k)
        grafico.append({"label": GIORNI[giorno.weekday()], "data": giorno.strftime("%d/%m"),
                        "concessi": concessi, "negati": totale - concessi, "totale": totale})
    massimo_grafico = max((g["totale"] for g in grafico), default=0) or 1

    cfg_presenze = presenze_service.configurazione(db)
    dentro_n = presenze_service.conta_dentro(db) if cfg_presenze["puo_dentro"] else 0
    elenco_aperto = cfg_presenze["puo_dentro"] and request.query_params.get("elenco") == "1"
    dentro = presenze_service.chi_e_dentro(db) if elenco_aperto else []

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request, "user": user, "varchi": varchi, "eventi": ultimi_eventi,
            "varchi_offline": varchi_offline, "alert": alert, "esito_info": ESITO_INFO,
            "passaggi_oggi": passaggi_oggi, "negati_oggi": negati_oggi, "oggi_label": oggi_label,
            "grafico": grafico, "massimo_grafico": massimo_grafico,
            "motivi": motivi, "massimo_motivi": massimo_motivi, "oggi_iso": now.date().isoformat(),
            "presenze_attive": cfg_presenze["puo_dentro"],
            "dentro_n": dentro_n, "dentro": dentro, "elenco_aperto": elenco_aperto,
            "soglia_ore": presenze_service.SOGLIA_SOSPETTO_ORE,
        },
    )


# ---------- Persone ----------

@router.get("/persone", response_class=HTMLResponse)
def lista_persone(request: Request, db: Session = Depends(get_db)):
    return _pagina_persone(request, db, visitatori=False)


@router.get("/visitatori", response_class=HTMLResponse)
def lista_visitatori(request: Request, db: Session = Depends(get_db)):
    return _pagina_persone(request, db, visitatori=True)


def _base_persona(db: Session, persona_id) -> str:
    """Pagina a cui tornare dopo una modifica: i visitatori hanno la loro, le persone la loro."""
    p = db.query(Persona).filter(Persona.id == int(persona_id)).first() if str(persona_id).isdigit() else None
    return "/visitatori" if p is not None and p.ospite else "/persone"


def _pagina_persone(request: Request, db: Session, visitatori: bool):
    """Persone e Visitatori sono la stessa cosa nel database (il campo «ospite»): due pagine separate per chiarezza."""
    user = require_login(request, db)
    base = "/visitatori" if visitatori else "/persone"
    gruppi = db.query(Gruppo).all()
    policy_disponibili = db.query(Policy).filter(Policy.attiva == True).all()  # noqa: E712

    # Ricerca per nome, cognome o nome del gruppo di appartenenza
    ricerca = (request.query_params.get("q") or "").strip()
    query = db.query(Persona).filter(Persona.ospite == visitatori)
    if ricerca:
        pattern = f"%{ricerca.lower()}%"
        query = (
            query.outerjoin(Persona.gruppi)
            .filter(
                or_(
                    func.lower(Persona.nome).like(pattern),
                    func.lower(Persona.cognome).like(pattern),
                    func.lower(Gruppo.nome).like(pattern),
                    # anche per il numero di una credenziale; i PIN no (cercandolo si scoprirebbe di chi e')
                    Persona.media.any(and_(func.lower(AccessMedia.codice).like(pattern), AccessMedia.tipo != TipoMedia.pin)),
                )
            )
            .distinct()
        )
    persone = query.order_by(Persona.cognome, Persona.nome).all()

    # Filtro per tipo di credenziale (?tipo=rfid|pin|uhf|ble|qr|senza)
    tutte = db.query(Persona).filter(Persona.ospite == visitatori).all()
    conteggi_cred = cred_service.conteggi(tutte)
    tipo_filtro = request.query_params.get("tipo") or ""
    if tipo_filtro == "senza":
        persone = [p for p in persone if not any(m.attivo for m in p.media)]
    elif tipo_filtro in cred_service.ORDINE:
        persone = [p for p in persone if any(m.tipo and m.tipo.value == tipo_filtro for m in p.media)]
    else:
        tipo_filtro = ""
    adesso = datetime.utcnow()
    riassunti = {p.id: cred_service.riassunto(p, adesso) for p in persone}

    # Se c'è ?apri=<id> in query string, apriamo la colonnina di dettaglio
    # di quella persona (stesso pattern server-rendered di tutto il resto,
    # niente stato JS da gestire).
    persona_aperta = None
    apri = request.query_params.get("apri")
    if apri and apri.isdigit():
        persona_aperta = db.query(Persona).filter(Persona.id == int(apri)).first()
        if persona_aperta is not None and bool(persona_aperta.ospite) != visitatori:   # e' dell'altra pagina
            qs = "&".join(f"{k}={v}" for k, v in request.query_params.items())
            return RedirectResponse(f"{'/persone' if visitatori else '/visitatori'}?{qs}", status_code=303)
    scheda_persona = request.query_params.get("scheda")
    if scheda_persona not in SCHEDE_PERSONA:
        scheda_persona = "accessi"

    # Dati per i tag-picker (chip + "+ Aggiungi") della colonnina di dettaglio:
    # separiamo già qui cosa è assegnato da cosa è ancora disponibile, così il
    # template non deve far altro che disegnarli (niente liste di decine di
    # checkbox da scorrere quando ci sono molti gruppi o regole).
    gruppi_selezionati, gruppi_disponibili_picker = [], []
    policy_selezionate, policy_disponibili_picker = [], []
    if persona_aperta:
        gruppi_sel_ids = {g.id for g in persona_aperta.gruppi}
        gruppi_selezionati = [{"id": g.id, "label": g.nome} for g in persona_aperta.gruppi]
        gruppi_disponibili_picker = [{"id": g.id, "label": g.nome} for g in gruppi if g.id not in gruppi_sel_ids]

        policy_sel_ids = {p.id for p in persona_aperta.policy_dirette}
        policy_selezionate = [{"id": p.id, "label": _label_policy(p)} for p in persona_aperta.policy_dirette]
        policy_disponibili_picker = [
            {"id": p.id, "label": _label_policy(p)} for p in policy_disponibili if p.id not in policy_sel_ids
        ]

    return templates.TemplateResponse(
        "persone.html",
        {
            "request": request, "user": user, "persone": persone, "gruppi": gruppi,
            "base": base, "visitatori": visitatori,
            "ap_varchi": [v.nome for v in db.query(Varco).filter(Varco.anti_passback_enabled == True).order_by(Varco.nome).all()],  # noqa: E712
            "passback_varco": (lambda i: (db.query(Varco).filter(Varco.id == i).first() if i else None))(persona_aperta.passback_varco_id if persona_aperta else None),
            "policy_disponibili": policy_disponibili, "persona_aperta": persona_aperta,
            "scheda": scheda_persona, "ricerca": ricerca, "adesso": adesso,
            "tipo_filtro": tipo_filtro, "conteggi_cred": conteggi_cred, "riassunti": riassunti,
            "tipi_cred": [(t, cred_service.ETICHETTE[t]) for t in cred_service.ORDINE],
            "suggerimenti_cred": cred_service.SUGGERIMENTI,
            "sconosciute": credenziali_sconosciute_recenti(db),
            "tutte_le_persone": [(p.id, f"{p.cognome} {p.nome}") for p in persone] if not ricerca else [
                (p.id, f"{p.cognome} {p.nome}") for p in db.query(Persona).filter(Persona.ospite == visitatori).order_by(Persona.cognome, Persona.nome).all()
            ],
            "gruppi_selezionati": gruppi_selezionati, "gruppi_disponibili_picker": gruppi_disponibili_picker,
            "policy_selezionate": policy_selezionate, "policy_disponibili_picker": policy_disponibili_picker,
        },
    )


@router.post("/persone")
def crea_persona(
    request: Request,
    nome: str = Form(...),
    cognome: str = Form(...),
    email: str = Form(""),
    codice: str = Form(""),
    db: Session = Depends(get_db),
):
    """Crea una persona. Con `codice` associa subito il primo badge. I visitatori hanno la loro pagina (/visitatori)."""
    require_login(request, db)
    codice = codice.strip()
    if codice and db.query(AccessMedia).filter(AccessMedia.codice == codice).first() is not None:
        return RedirectResponse("/persone?errore=badge_duplicato", status_code=303)
    p = Persona(nome=nome, cognome=cognome, email=email or None)
    db.add(p)
    db.flush()
    if codice:
        db.add(AccessMedia(codice=codice, persona_id=p.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/persone?errore=badge_duplicato", status_code=303)
    registra_log(db, t("Persona creata: %(nome)s %(cognome)s", nome=nome, cognome=cognome) + (t(" (badge %(codice)s)", codice=codice) if codice else ""), categoria="persone")
    return RedirectResponse("/persone", status_code=303)


@router.post("/visitatori")
def crea_visitatore(
    request: Request,
    nome: str = Form(...),
    cognome: str = Form(...),
    codice: str = Form(""),
    valido_da: str = Form(""),
    valido_a: str = Form(""),
    db: Session = Depends(get_db),
):
    """Crea un visitatore. La scadenza e' obbligatoria: vale per la persona e per il suo badge (quello piu' stretto
    decide). Con `codice` associa subito il badge."""
    require_login(request, db)
    codice = codice.strip()
    da, a = tempo.da_input_locale(valido_da), tempo.da_input_locale(valido_a)
    if a is None:
        return RedirectResponse("/visitatori?errore=scadenza", status_code=303)
    if da is not None and a < da:
        return RedirectResponse("/visitatori?errore=validita_invertita", status_code=303)
    if codice and db.query(AccessMedia).filter(AccessMedia.codice == codice).first() is not None:
        return RedirectResponse("/visitatori?errore=badge_duplicato", status_code=303)
    p = Persona(nome=nome, cognome=cognome, ospite=True, valido_da=da, valido_a=a)
    db.add(p)
    db.flush()
    if codice:
        db.add(AccessMedia(codice=codice, persona_id=p.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/visitatori?errore=badge_duplicato", status_code=303)
    registra_log(db, t("Visitatore creato: %(nome)s %(cognome)s, valido fino al %(fino)s", nome=nome, cognome=cognome, fino=tempo.formatta(a, '%d/%m/%Y %H:%M'))
                     + (t(" (badge %(codice)s)", codice=codice) if codice else ""), categoria="persone")
    return RedirectResponse(f"/visitatori?apri={p.id}&scheda=titoli" if not codice else "/visitatori", status_code=303)


@router.post("/visitatori/{persona_id}/validita")
def validita_visitatore(
    request: Request, persona_id: int, valido_da: str = Form(""), valido_a: str = Form(""), db: Session = Depends(get_db),
):
    """Cambia «dal» e «fino al» del visitatore (la scadenza resta obbligatoria)."""
    require_login(request, db)
    p = db.query(Persona).filter(Persona.id == persona_id, Persona.ospite == True).first()  # noqa: E712
    if p is None:
        return RedirectResponse("/visitatori", status_code=303)
    da, a = tempo.da_input_locale(valido_da), tempo.da_input_locale(valido_a)
    if a is None:
        return RedirectResponse(f"/visitatori?apri={p.id}&errore=scadenza", status_code=303)
    if da is not None and a < da:
        return RedirectResponse(f"/visitatori?apri={p.id}&errore=validita_invertita", status_code=303)
    p.valido_da, p.valido_a = da, a
    db.commit()
    modifiche.segna_modifica_globale(db)   # la scadenza arriva agli slave con la cache
    registra_log(db, t("Visitatore %(nome)s %(cognome)s: valido fino al %(p)s", nome=p.nome, cognome=p.cognome, p=tempo.formatta(a, '%d/%m/%Y %H:%M')), categoria="persone")
    return RedirectResponse(f"/visitatori?apri={p.id}&salvata=1", status_code=303)


@router.post("/visitatori/elimina-scaduti")
def elimina_visitatori_scaduti(request: Request, db: Session = Depends(get_db)):
    """Toglie i visitatori la cui scadenza e' passata (lo storico dei passaggi resta)."""
    require_login(request, db)
    adesso = datetime.utcnow()
    scaduti = db.query(Persona).filter(Persona.ospite == True, Persona.valido_a.isnot(None), Persona.valido_a < adesso).all()  # noqa: E712
    n = len(scaduti)
    for p in scaduti:
        db.delete(p)
    db.commit()
    if n:
        registra_log(db, t("Visitatori scaduti eliminati: %(n)s", n=n), categoria="persone", livello="warning")
    return RedirectResponse(f"/visitatori?eliminati={n}", status_code=303)


@router.post("/persone/{persona_id}/attivo")
def cambia_attivo_persona(request: Request, persona_id: int, db: Session = Depends(get_db)):
    """Sospende / riattiva una persona senza cancellarla: i suoi badge restano
    associati e lo storico dei passaggi mantiene il nome, ma finché è sospesa
    nessuno dei suoi badge apre un varco (la cache mandata agli slave la esclude:
    serve "Scarica dati ai dispositivi" perche' la modifica arrivi ai varchi)."""
    user = require_login(request, db)
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if p:
        p.attivo = not p.attivo
        db.commit()
        if p.attivo:
            registra_log(db, t("Persona riattivata: %(nome)s %(cognome)s (%(username)s)", nome=p.nome, cognome=p.cognome, username=user.username), categoria="persone")
        else:
            registra_log(
                db, t("Persona sospesa: %(nome)s %(cognome)s (%(username)s): i suoi badge non aprono più i varchi", nome=p.nome, cognome=p.cognome, username=user.username),
                livello="warning", categoria="persone",
            )
    return RedirectResponse(f"{_base_persona(db, persona_id)}?apri={persona_id}&sospesa={'0' if p and p.attivo else '1'}", status_code=303)


@router.post("/persone/{persona_id}/delete")
def elimina_persona(request: Request, persona_id: int, db: Session = Depends(get_db)):
    require_login(request, db)
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    base = "/visitatori" if p is not None and p.ospite else "/persone"
    if p:
        nome_completo = f"{p.nome} {p.cognome}"
        db.delete(p)
        db.commit()
        registra_log(db, (t("Visitatore eliminato: %(nome_completo)s", nome_completo=nome_completo) if base == '/visitatori' else t("Persona eliminata: %(nome_completo)s", nome_completo=nome_completo)), livello="warning", categoria="persone")
    return RedirectResponse(base, status_code=303)


# ---------- Azioni su più persone selezionate insieme ----------

@router.post("/persone/selezione/gruppo")
async def selezione_aggiungi_gruppo(request: Request, db: Session = Depends(get_db)):
    """Aggiunge tutte le persone selezionate in lista a un gruppo in un colpo solo."""
    require_login(request, db)
    form = await request.form()
    persona_ids = [int(v) for v in form.getlist("persona_ids")]
    gruppo_id = form.get("gruppo_id")
    if persona_ids and gruppo_id:
        gruppo = db.query(Gruppo).filter(Gruppo.id == int(gruppo_id)).first()
        if gruppo:
            persone = db.query(Persona).filter(Persona.id.in_(persona_ids)).all()
            for p in persone:
                if gruppo not in p.gruppi:
                    p.gruppi.append(gruppo)
            db.commit()
            registra_log(db, t("%(p)s persone aggiunte al gruppo '%(nome)s'", p=len(persone), nome=gruppo.nome), categoria="persone")
    return RedirectResponse("/visitatori" if form.get("ritorno") == "/visitatori" else "/persone", status_code=303)


@router.post("/persone/selezione/elimina")
async def selezione_elimina(request: Request, db: Session = Depends(get_db)):
    """Elimina tutte le persone selezionate in lista in un colpo solo."""
    require_login(request, db)
    form = await request.form()
    persona_ids = [int(v) for v in form.getlist("persona_ids")]
    if persona_ids:
        persone = db.query(Persona).filter(Persona.id.in_(persona_ids)).all()
        nomi = ", ".join(f"{p.nome} {p.cognome}" for p in persone)
        numero = len(persone)
        for p in persone:
            db.delete(p)
        db.commit()
        registra_log(db, t("%(numero)s persone eliminate: %(nomi)s", numero=numero, nomi=nomi), livello="warning", categoria="persone")
    return RedirectResponse("/visitatori" if form.get("ritorno") == "/visitatori" else "/persone", status_code=303)


@router.post("/persone/{persona_id}/gruppi")
async def aggiorna_gruppi_persona(request: Request, persona_id: int, db: Session = Depends(get_db)):
    """Imposta i gruppi di una persona dalla colonnina di dettaglio (checkbox multiple)."""
    require_login(request, db)
    form = await request.form()
    ids = [int(v) for v in form.getlist("gruppo_id")]
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if p:
        p.gruppi = db.query(Gruppo).filter(Gruppo.id.in_(ids)).all() if ids else []
        db.commit()
        nomi = ", ".join(g.nome for g in p.gruppi) or "nessuno"
        registra_log(db, t("Gruppi di %(nome)s %(cognome)s aggiornati: %(nomi)s", nome=p.nome, cognome=p.cognome, nomi=nomi), categoria="persone")
    return RedirectResponse(f"{_base_persona(db, persona_id)}?apri={persona_id}", status_code=303)


@router.post("/persone/{persona_id}/policy")
async def aggiorna_policy_persona(request: Request, persona_id: int, db: Session = Depends(get_db)):
    """Imposta le regole dirette di una persona (oltre a quelle dei suoi gruppi)."""
    require_login(request, db)
    form = await request.form()
    ids = [int(v) for v in form.getlist("policy_id")]
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if p:
        p.policy_dirette = db.query(Policy).filter(Policy.id.in_(ids)).all() if ids else []
        db.commit()
        registra_log(db, t("Regole dirette di %(nome)s %(cognome)s aggiornate", nome=p.nome, cognome=p.cognome), categoria="persone")
    return RedirectResponse(f"{_base_persona(db, persona_id)}?apri={persona_id}", status_code=303)


@router.post("/persone/{persona_id}/anti-passback")
def imposta_anti_passback_persona(request: Request, persona_id: int, attivo: str = Form(""), db: Session = Depends(get_db)):
    """Attiva/disattiva l'anti-passback per una persona (conta solo sui varchi che lo hanno attivo)."""
    require_login(request, db)
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if p:
        p.anti_passback = attivo == "1"
        db.commit()
        registra_log(db, t("Anti-passback %(p)s per %(nome)s %(cognome)s", p='attivato' if p.anti_passback else 'disattivato', nome=p.nome, cognome=p.cognome), categoria="persone")
        udp_agent.aggiorna_cache_ap(db)
    return RedirectResponse(f"{_base_persona(db, persona_id)}?apri={persona_id}&scheda=presenza", status_code=303)


@router.post("/persone/selezione/anti-passback")
async def selezione_anti_passback(request: Request, db: Session = Depends(get_db)):
    """Attiva o disattiva l'anti-passback per tutte le persone selezionate."""
    require_login(request, db)
    form = await request.form()
    ids = [int(v) for v in form.getlist("persona_ids") if str(v).isdigit()]
    valore = form.get("anti_passback")
    if ids and valore in ("1", "0"):
        n = db.query(Persona).filter(Persona.id.in_(ids)).update({Persona.anti_passback: valore == "1"}, synchronize_session=False)
        db.commit()
        registra_log(db, t("Anti-passback %(p)s per %(n)s persone", p='attivato' if valore == '1' else 'disattivato', n=n), categoria="persone")
        udp_agent.aggiorna_cache_ap(db)
    return RedirectResponse("/visitatori" if form.get("ritorno") == "/visitatori" else "/persone", status_code=303)


@router.post("/persone/{persona_id}/reset-passback")
def reset_passback(request: Request, persona_id: int, db: Session = Depends(get_db)):
    """Sblocca una persona bloccata dall'anti-passback (es. è uscita in coda a un altro)."""
    require_login(request, db)
    p = db.query(Persona).filter(Persona.id == persona_id).first()
    if p:
        p.passback_stato = None
        p.passback_varco_id = None
        p.passback_aggiornato_at = datetime.utcnow()
        db.commit()
        registra_log(db, t("Anti-passback resettato per %(nome)s %(cognome)s", nome=p.nome, cognome=p.cognome), categoria="persone")
        udp_agent.aggiorna_cache_ap(db)
    return RedirectResponse(f"{_base_persona(db, persona_id)}?apri={persona_id}&scheda=presenza", status_code=303)


# ---------- Access media (badge / titoli di accesso) ----------

@router.post("/persone/{persona_id}/media")
def aggiungi_media(
    request: Request, persona_id: int, codice: str = Form(...), tipo: str = Form("rfid"),
    valido_da: str = Form(""), valido_a: str = Form(""), db: Session = Depends(get_db)
):
    require_login(request, db)
    codice = codice.strip()
    tipo_media = cred_service.tipo_valido(tipo)
    if not codice:
        return RedirectResponse(f"{_base_persona(db, persona_id)}?apri={persona_id}&scheda=titoli&errore=badge_vuoto", status_code=303)
    if tipo_media == TipoMedia.pin and not cred_service.PIN_VALIDO.match(codice):
        return RedirectResponse(f"{_base_persona(db, persona_id)}?apri={persona_id}&scheda=titoli&errore=pin_non_valido", status_code=303)
    esistente = db.query(AccessMedia).filter(AccessMedia.codice == codice).first()
    if esistente is not None:
        return RedirectResponse(f"{_base_persona(db, persona_id)}?apri={persona_id}&scheda=titoli&errore=badge_duplicato", status_code=303)
    da, a = tempo.da_input_locale(valido_da), tempo.da_input_locale(valido_a)
    if da and a and a < da:
        return RedirectResponse(f"{_base_persona(db, persona_id)}?apri={persona_id}&scheda=titoli&errore=validita_invertita", status_code=303)
    m = AccessMedia(codice=codice, tipo=tipo_media, persona_id=persona_id, valido_da=da, valido_a=a)
    db.add(m)
    try:
        db.commit()
    except IntegrityError:
        # Due richieste quasi simultanee con lo stesso codice: la seconda
        # supera il controllo sopra ma viene fermata dal vincolo UNIQUE.
        db.rollback()
        return RedirectResponse(f"{_base_persona(db, persona_id)}?apri={persona_id}&scheda=titoli&errore=badge_duplicato", status_code=303)
    persona = db.query(Persona).filter(Persona.id == persona_id).first()
    chi = f"{persona.nome} {persona.cognome}" if persona else persona_id
    nome_cred = f"{cred_service.ETICHETTE[tipo_media.value]} '{codice}'" if tipo_media != TipoMedia.pin else "PIN"
    registra_log(db, t("Credenziale %(nome_cred)s aggiunta a %(chi)s", nome_cred=nome_cred, chi=chi), categoria="persone")
    return RedirectResponse(f"{_base_persona(db, persona_id)}?apri={persona_id}&scheda=titoli", status_code=303)


@router.post("/media/{media_id}/toggle")
def toggle_media(request: Request, media_id: int, db: Session = Depends(get_db)):
    require_login(request, db)
    m = db.query(AccessMedia).filter(AccessMedia.id == media_id).first()
    persona_id = m.persona_id if m else None
    if m:
        m.attivo = not m.attivo
        db.commit()
        stato = "attivata" if m.attivo else "disattivata"
        registra_log(db, t("Credenziale '%(codice)s' %(stato)s", codice=m.codice, stato=stato), categoria="persone")
    return RedirectResponse(f"{_base_persona(db, persona_id)}?apri={persona_id}&scheda=titoli" if persona_id else "/persone", status_code=303)


# ---------- Varchi (configurazione logica + stato del dispositivo fisico: stessa riga, stessa pagina) ----------

SCHEDE_VARCO = ("controllo", "impostazioni", "dispositivo")
SCHEDE_PERSONA = ("accessi", "titoli", "presenza")


@router.get("/varchi", response_class=HTMLResponse)
def lista_varchi(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    varchi = _calcola_stato_online(db.query(Varco).all())

    # Se c'è ?apri=<id>, apriamo il drawer di dettaglio di quel varco (stesso
    # pattern server-rendered usato in Persone).
    varco_aperto = None
    ultimi_passaggi = []
    apri = request.query_params.get("apri")
    if apri and apri.isdigit():
        varco_aperto = next((v for v in varchi if v.id == int(apri)), None)
    if varco_aperto:
        ultimi_passaggi = (
            db.query(Evento).filter(Evento.varco_id == varco_aperto.id)
            .order_by(Evento.timestamp.desc(), Evento.id.desc()).limit(5).all()
        )
    scheda = request.query_params.get("scheda")
    if scheda not in SCHEDE_VARCO:
        scheda = "controllo"

    # Funzionamento (lettori, uscite, ingressi): riepilogo e comandi nel drawer, popup con ?funz=1.
    fn = funz_service.da_varco(varco_aperto) if varco_aperto else None
    stato_uscite = funz_service.leggi_stato_uscite(varco_aperto) if varco_aperto else {}
    comandi = []
    if fn is not None:
        for i in funz_service.uscite_comandabili(fn):
            u = fn["uscite"][i]
            comandi.append({
                "indice": i, "nome": t("Allarme") if u["r"] == "inseritore" else funz_service.nome_uscita(fn, i),
                "inseritore": u["r"] == "inseritore", "interruttore": funz_service.e_interruttore(u),
                "acceso": stato_uscite.get(i, False), "sec": u["sec"],
            })
    funz_aperto = bool(varco_aperto and request.query_params.get("funz") == "1")

    return templates.TemplateResponse(
        "varchi.html",
        {"request": request, "user": user, "varchi": varchi, "varco_aperto": varco_aperto,
         "prossimo_id": _prossimo_id_varco(db),
         "fn": fn, "fn_predefinito": funz_service.predefinito(),
         "tecnologie_lista": [[k, str(lab), sic] for k, (lab, sic) in funz_service.TECNOLOGIE.items()],
         "fn_riepilogo": funz_service.riepilogo(fn) if fn is not None else "", "fn_esplicito": bool(varco_aperto and funz_service.e_esplicito(varco_aperto)),
         "comandi_uscite": comandi, "funz_aperto": funz_aperto,
         "scheda": scheda, "ultimi_passaggi": ultimi_passaggi, "esito_info": ESITO_INFO,
         "fasce": db.query(FasciaOraria).order_by(FasciaOraria.nome).all(),
         "fasce_lista": [[f.id, f.nome] for f in db.query(FasciaOraria).order_by(FasciaOraria.nome).all()],
         "bloccanti": list(funz_service.MODALITA_BLOCCANTI),
         "n_policy_varco": db.query(Policy).filter(Policy.varchi.any(Varco.id == varco_aperto.id)).count() if varco_aperto else 0,
         "n_policy_orfane": sum(1 for p in db.query(Policy).filter(Policy.varchi.any(Varco.id == varco_aperto.id)).all() if len(p.varchi) == 1) if varco_aperto else 0,
         "policy_varco": [p.nome or t("Regola #%(id)s", id=p.id) for p in db.query(Policy).filter(Policy.varchi.any(Varco.id == varco_aperto.id)).order_by(Policy.nome).all()] if varco_aperto else [],
         "n_eventi_varco": db.query(Evento).filter(Evento.varco_id == varco_aperto.id).count() if varco_aperto else 0},
    )


@router.get("/dashboard/varchi-live", response_class=HTMLResponse)
def dashboard_varchi_live(request: Request, db: Session = Depends(get_db)):
    """
    Risposta per il polling htmx (ogni 8s) della card "Varchi — azioni
    rapide" e, in uno swap "out of band" nella stessa risposta, della
    stat-card "Varchi online" in alto — un solo giro invece di due.
    """
    require_login(request, db)
    varchi = _calcola_stato_online(db.query(Varco).all())
    varchi_offline = [v for v in varchi if not v.online]
    return templates.TemplateResponse(
        "_dashboard_varchi_live.html", {"request": request, "varchi": varchi, "varchi_offline": varchi_offline},
    )


@router.get("/varchi/stato-live", response_class=HTMLResponse)
def varchi_stato_live(request: Request, db: Session = Depends(get_db)):
    """
    Risposta per il polling htmx (ogni 8s) della tabella su /varchi: niente
    stato online/offline "congelato" al momento del caricamento della
    pagina, senza dover ricaricarla a mano per saperlo aggiornato.
    """
    user = require_login(request, db)
    varchi = _calcola_stato_online(db.query(Varco).all())
    return templates.TemplateResponse("_varchi_tabella.html", {"request": request, "user": user, "varchi": varchi})


@router.get("/varchi/abbinamenti", response_class=HTMLResponse)
def varchi_abbinamenti(request: Request, db: Session = Depends(get_db)):
    """Avviso «slave in attesa» (polling htmx dalla pagina Varchi, solo admin)."""
    user = require_admin(request, db)
    return templates.TemplateResponse(
        "_abbinamenti.html", {"request": request, "user": user, "in_attesa": udp_agent.richieste_abbinamento(db),
                          "conflitti": {v.id: udp_agent.varco_con_ip(db, v.abbinamento_ip, v.id) for v in udp_agent.richieste_abbinamento(db)}},
    )


@router.get("/da-fare", response_class=HTMLResponse)
def da_fare(request: Request, da: str = "/", db: Session = Depends(get_db)):
    """Pannello «Da fare» nella barra laterale (polling htmx da ogni pagina): dispositivi in attesa di
    abbinamento (solo admin) e varchi con dati non aggiornati. Vuoto se non c'e' niente."""
    user = require_login(request, db)
    in_attesa = udp_agent.richieste_abbinamento(db) if user.is_admin else []
    ritorno = da if da.startswith("/") and not da.startswith("//") else "/"
    return templates.TemplateResponse("_da_fare.html", {
        "request": request, "user": user, "in_attesa": in_attesa, "ritorno": ritorno,
        "conflitti": {v.id: udp_agent.varco_con_ip(db, v.abbinamento_ip, v.id) for v in in_attesa},
        "da_inviare": modifiche.stato_dati(db)["da_inviare"],
    })


@router.post("/varchi/{varco_id}/abbina")
def abbina_varco(request: Request, varco_id: int, db: Session = Depends(get_db)):
    """Conferma dell'admin: manda la chiave allo slave che si e' presentato (e ne registra l'IP)."""
    user = require_admin(request, db)
    v = db.query(Varco).filter(Varco.id == varco_id).first()
    if not v:
        return RedirectResponse("/varchi", status_code=303)
    ip = v.abbinamento_ip
    if udp_agent.varco_con_ip(db, ip, v.id):
        return RedirectResponse("/varchi?errore=abbinamento_ip", status_code=303)
    if not udp_agent.abbina(db, v):
        return RedirectResponse("/varchi?errore=abbinamento", status_code=303)
    registra_log(db, t("Varco '%(nome)s' abbinato allo slave %(ip)s da %(username)s", nome=v.nome, ip=ip, username=user.username), categoria="varchi")
    return RedirectResponse(f"/varchi?abbinato={v.id}", status_code=303)


@router.get("/dispositivi")
def redirect_dispositivi():
    """Varchi e Dispositivi erano due pagine per la stessa cosa (la riga Varco):
    unificate in /varchi. Redirect per non rompere eventuali link salvati."""
    return RedirectResponse("/varchi", status_code=301)


def _minuti_o_none(testo: str) -> int | None:
    """Campo numerico facoltativo di un form: vuoto o <= 0 = nessun valore."""
    try:
        n = int((testo or "").strip())
    except ValueError:
        return None
    return n if n > 0 else None


def _ip_valido(testo: str) -> bool:
    """Indirizzo IPv4/IPv6 ben formato (vuoto = non ancora impostato, lo gestisce il chiamante)."""
    try:
        ipaddress.ip_address(testo)
        return True
    except ValueError:
        return False


def _prossimo_id_varco(db: Session) -> int:
    """Primo numero libero in coda: e' il suggerimento nel form «Nuovo varco»."""
    massimo = db.query(func.max(Varco.id)).scalar() or 0
    return massimo + 1


@router.post("/varchi")
def crea_varco(
    request: Request,
    nome: str = Form(...),
    varco_id: str = Form(""),
    ip_address: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    Crea un varco con i soli dati che servono per collegarlo: nome, numero (ID, lo stesso
    che lo slave ha in config.yaml) e indirizzo IP del dispositivo. Tutto il resto
    (descrizione, presenze, anti-passback, funzionamento) si regola poi dal dettaglio.
    """
    require_admin(request, db)
    nome = nome.strip()
    ip = ip_address.strip()
    testo_id = varco_id.strip()

    def errore(codice: str):
        qs = urlencode({"errore": codice, "nuovo_nome": nome, "nuovo_id": testo_id, "nuovo_ip": ip})
        return RedirectResponse(f"/varchi?{qs}", status_code=303)

    if not nome:
        return errore("varco_nome")
    if testo_id:
        if not (testo_id.isdigit() and 0 < int(testo_id) <= 2_000_000_000):
            return errore("varco_id")
        numero = int(testo_id)
        if db.query(Varco).filter(Varco.id == numero).first():
            return errore("varco_id_usato")
    else:
        numero = _prossimo_id_varco(db)
    # L'IP si scrive solo qui: e' da qui che il master tocca il dispositivo, che impara l'indirizzo del master.
    if not ip or not _ip_valido(ip):
        return errore("varco_ip")
    if db.query(Varco).filter(Varco.ip_address == ip).first():
        return errore("varco_ip_usato")

    v = Varco(
        id=numero,
        nome=nome,
        api_key=secrets.token_hex(16),
        ip_address=ip or None,
    )
    db.add(v)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return errore("varco_id_usato")
    registra_log(db, t("Varco creato: %(nome)s (n. %(id)s%(ip)s)", nome=nome, id=v.id, ip=t(", IP %(ip)s", ip=v.ip_address) if v.ip_address else ""),
                 categoria="varchi", varco_id=v.id)
    return RedirectResponse(f"/varchi?creato={v.id}", status_code=303)


@router.post("/varchi/{varco_id}/modifica")
def aggiorna_varco(
    request: Request,
    varco_id: int,
    descrizione: str = Form(""),
    apertura_durata_sec: Optional[int] = Form(None),
    direzione: str = Form(""),
    anti_passback_enabled: str = Form(""),
    anti_passback_modalita: str = Form("soft"),
    anti_passback_timeout_min: str = Form(""),
    conta_presenze: str = Form(""),
    db: Session = Depends(get_db),
):
    """Modifica dal drawer di dettaglio sulla pagina Varchi (descrizione, presenze,
    anti-passback — tutto tranne l'IP, che si modifica in riga nella tabella principale).
    Durata di apertura e direzione ora stanno nel popup Funzionamento: restano accettate
    solo se presenti (form vecchi) e non vengono toccate se mancano."""
    require_login(request, db)
    v = db.query(Varco).filter(Varco.id == varco_id).first()
    if v:
        v.descrizione = descrizione
        if apertura_durata_sec is not None and apertura_durata_sec > 0:
            v.apertura_durata_sec = apertura_durata_sec
        if direzione in DIREZIONI:
            v.direzione = direzione
        v.conta_presenze = bool(conta_presenze)
        v.anti_passback_enabled = bool(anti_passback_enabled)
        # «hard» = blocco: il master segna nella cache di questo varco chi risulta gia' dentro; i lettori di ingresso
        # dello slave non li aprono (l'uscita passa sempre). Lo slave decide in locale con la cache che ha.
        v.anti_passback_modalita = anti_passback_modalita if anti_passback_modalita in MODALITA_PASSBACK else "soft"
        v.anti_passback_timeout_min = _minuti_o_none(anti_passback_timeout_min)
        db.commit()
        presenze_service.riallinea(db)
        registra_log(db, t("Varco '%(nome)s' aggiornato", nome=v.nome), categoria="varchi")
        udp_agent.aggiorna_cache_ap(db)
        return RedirectResponse(f"/varchi?apri={varco_id}&scheda=impostazioni", status_code=303)
    return RedirectResponse(f"/varchi?apri={varco_id}&scheda=impostazioni", status_code=303)


@router.post("/varchi/{varco_id}/funzionamento")
def salva_funzionamento(
    request: Request,
    varco_id: int,
    dati: str = Form(...),
    modalita: str = Form(""),
    sblocco_fascia_id: str = Form(""),
    db: Session = Depends(get_db),
):
    """Salva il funzionamento del varco dal popup (lettori, uscite, ingressi, avvisi).
    `dati` e' il JSON costruito dal popup: si controlla tutto lato server (mai fidarsi
    del browser). Il popup porta anche la modalita' di funzionamento: se cambia, la cache va subito
    al dispositivo; in Manutenzione ed Emergenza uscite e ingressi sono bloccati e non si toccano.
    Il resto arriva al dispositivo con la prossima cache ("Scarica dati")."""
    user = require_login(request, db)
    v = db.query(Varco).filter(Varco.id == varco_id).first()
    if v is None:
        return RedirectResponse("/varchi", status_code=303)
    base = f"/varchi?apri={varco_id}&scheda=controllo"
    # Modalita' (facoltativa: i form vecchi non la mandano). Si controlla prima di salvare qualunque cosa.
    fascia_id = None
    cambia_modalita = False
    if modalita:
        errore, fascia_id = valida_modalita(db, modalita, sblocco_fascia_id)
        if errore:
            return RedirectResponse(f"{base}&funz=1&errore={errore}", status_code=303)
        cambia_modalita = modalita != v.modalita or (modalita == "sblocco" and fascia_id != v.sblocco_fascia_id)
    finale = modalita or v.modalita
    if finale not in funz_service.MODALITA_BLOCCANTI:
        try:
            fn = funz_service.normalizza(json.loads(dati))
        except (ValueError, TypeError):
            return RedirectResponse(f"{base}&funz=1&errore=funzionamento", status_code=303)
        funz_service.azzera_stati_cambiati(v, funz_service.da_varco(v), fn)
        v.funzionamento = funz_service.a_json(fn)
        if not funz_service.ha_sensore_porta(fn):
            v.porta_stato, v.porta_stato_at = None, None      # senza sensore configurato non esiste uno «stato porta»
        # Campi storici tenuti allineati (cache per slave vecchi, comando AP, config.yaml mostrato):
        # durata = quella della prima uscita che apre, direzione = quella del primo lettore.
        apre = funz_service.indici_apre(fn)
        if apre:
            v.apertura_durata_sec = fn["uscite"][apre[0]]["sec"]
        v.direzione = fn["lettori"][0]["dir"]
        db.commit()
        presenze_service.riallinea(db)
        registra_log(db, t("Funzionamento del varco '%(nome)s' aggiornato da %(username)s: %(p)s", nome=v.nome, username=user.username, p=funz_service.riepilogo(fn)), categoria="varchi")
    if cambia_modalita:
        _applica_modalita(db, v, modalita, fascia_id, user.username)
        # Subito allo slave (porta con se' anche il funzionamento): l'emergenza non puo' aspettare il giro delle 6 ore.
        udp_agent.invia_cache(db, solo_varco_id=v.id)
    return RedirectResponse(f"{base}&funz_salvato={'2' if cambia_modalita else '1'}", status_code=303)


@router.post("/varchi/{varco_id}/uscita/{indice}")
def comanda_uscita(
    request: Request,
    varco_id: int,
    indice: int,
    azione: str = Form("attiva"),
    db: Session = Depends(get_db),
):
    """Comando dal pannello a un'uscita "Altro" o all'inseritore allarme: "attiva" (impulso),
    "accendi", "spegni" oppure "inverti". Va allo slave in UDP (a perdere, come l'apertura
    remota); lo stato vero arriva poi dallo slave (SU) e si vede ricaricando."""
    user = require_login(request, db)
    v = db.query(Varco).filter(Varco.id == varco_id).first()
    base = f"/varchi?apri={varco_id}&scheda=controllo"
    if v is None:
        return RedirectResponse("/varchi", status_code=303)
    fn = funz_service.da_varco(v)
    if not 0 <= indice < funz_service.N_USCITE or indice not in funz_service.uscite_comandabili(fn):
        return RedirectResponse(f"{base}&errore=uscita", status_code=303)
    if v.ip_address and not _calcola_stato_online([v])[0].online:
        registra_log(db, t("Comando a un'uscita di '%(nome)s' non inviato: il dispositivo e' offline (%(username)s)", nome=v.nome, username=user.username),
                     livello="warning", categoria="varchi", varco_id=v.id, codice="CMD_OFFLINE")
        return RedirectResponse(f"{base}&errore=dispositivo_offline", status_code=303)
    u = fn["uscite"][indice]
    nome = t("Allarme") if u["r"] == "inseritore" else funz_service.nome_uscita(fn, indice)
    if funz_service.e_interruttore(u):
        acceso_ora = funz_service.leggi_stato_uscite(v).get(indice, False)
        voluto = {"accendi": True, "spegni": False}.get(azione, not acceso_ora)
        codice, descr = ("1" if voluto else "0"), (
            (t("inserito") if voluto else t("disinserito")) if u["r"] == "inseritore" else (t("acceso") if voluto else t("spento")))
    else:
        codice, descr = "i", t("impulso di %(sec)s s", sec=u['sec'])
    if not udp_agent.invia_comando_uscita(v, indice, codice):
        registra_log(db, t("Comando a '%(nome)s' di '%(nome2)s' non inviato: IP del varco non configurato o rete non raggiungibile (%(username)s)", nome=nome, nome2=v.nome, username=user.username),
                     livello="warning", categoria="varchi")
        return RedirectResponse(f"{base}&errore=comando_non_inviato", status_code=303)
    db.add(Evento(timestamp=datetime.utcnow(), varco_id=v.id, esito=EsitoEvento.uscita_azionata, dettaglio=f"{nome} · {descr}"[:120]))
    db.commit()
    registra_log(db, t("Uscita '%(nome)s' di '%(nome2)s': %(descr)s (richiesto da %(username)s)", nome=nome, nome2=v.nome, descr=descr, username=user.username), categoria="varchi")
    return RedirectResponse(base, status_code=303)


@router.post("/varchi/{varco_id}/ip")
def aggiorna_ip_varco(
    request: Request,
    varco_id: int,
    ip_address: str = Form(""),
    db: Session = Depends(get_db),
):
    """
    Imposta l'IP del varco per il canale UDP (slave leggeri: microcontrollore
    o piccola SBC senza l'agent Python). Va messo a mano qui perché il master
    deve poter toccare lo slave e mandargli comandi e cache in unicast a un indirizzo noto,
    e per coerenza anche i report/heartbeat dello slave vengono accettati
    solo se arrivano da questo IP (vedi services/udp_agent.py).
    """
    require_login(request, db)
    v = db.query(Varco).filter(Varco.id == varco_id).first()
    ip = ip_address.strip()
    if ip and not _ip_valido(ip):
        return RedirectResponse("/varchi?errore=varco_ip", status_code=303)
    if v and ip and udp_agent.varco_con_ip(db, ip, v.id):
        return RedirectResponse("/varchi?errore=varco_ip_usato", status_code=303)
    if v:
        v.ip_address = ip or None
        db.commit()
        registra_log(
            db, t("IP del varco '%(nome)s' impostato a %(p)s", nome=v.nome, p=v.ip_address or t('(nessuno)')), categoria="varchi",
        )
    return RedirectResponse("/varchi", status_code=303)


@router.post("/varchi/{varco_id}/rigenera-api-key")
def rigenera_api_key_varco(request: Request, varco_id: int, db: Session = Depends(get_db)):
    """
    Genera una nuova api_key per il varco, invalidando quella vecchia:
    da usare se la vecchia può essere stata esposta (es. commit git, backup
    non protetto). Il varco smette di autenticarsi col master finché non
    aggiorni config.yaml (o il firmware) con la nuova chiave.
    """
    user = require_admin(request, db)
    v = db.query(Varco).filter(Varco.id == varco_id).first()
    if v:
        v.api_key = secrets.token_hex(16)
        db.commit()
        registra_log(
            db, t("api_key rigenerata per varco '%(nome)s' da %(username)s — lo slave si ripresenta da solo entro un minuto: serve un nuovo «Abbina» (o la nuova chiave a mano nel config)", nome=v.nome, username=user.username),
            livello="warning", categoria="varchi",
        )
    return RedirectResponse(f"/varchi?apri={varco_id}&scheda=dispositivo", status_code=303)


@router.post("/varchi/{varco_id}/elimina")
def elimina_varco(request: Request, varco_id: int, db: Session = Depends(get_db)):
    """Elimina il varco; le sue regole restano (incomplete se era l'unico varco) e i passaggi restano nel registro (la conferma, che elenca
    cosa si perde, e' nel form). Solo admin. Il dispositivo fisico non viene toccato: senza il
    varco nella webapp le sue richieste di abbinamento restano senza risposta."""
    user = require_admin(request, db)
    v = db.query(Varco).filter(Varco.id == varco_id).first()
    if v is None:
        return RedirectResponse("/varchi", status_code=303)
    nome = v.nome
    policy = db.query(Policy).filter(Policy.varchi.any(Varco.id == v.id)).all()
    n_policy = 0
    for p in policy:
        p.varchi = [x for x in p.varchi if x.id != v.id]
        if not p.varchi:           # la regola resta, incompleta (nessun varco): non concede niente finche' non la si completa o la si elimina
            n_policy += 1
    # I passaggi NON si toccano: restano in Monitoraggio e Log, con il nome che il varco aveva.
    n_eventi = db.query(Evento).filter(Evento.varco_id == v.id).update({Evento.varco_nome: nome}, synchronize_session=False)
    db.delete(v)                   # porta via anche le vecchie richieste di apertura (cascade)
    db.commit()
    presenza.dimentica(varco_id)
    udp_agent.dimentica_varco(varco_id)
    connessioni.dimentica(varco_id)
    presenze_service.riallinea(db)
    registra_log(
        db, t("Varco '%(nome)s' eliminato da %(username)s (%(n_policy)s regole rimaste senza varchi (incomplete); %(n_eventi)s passaggi conservati nel registro)", nome=nome, username=user.username, n_policy=n_policy, n_eventi=n_eventi),
        livello="warning", categoria="varchi",
    )
    return RedirectResponse(f"/varchi?eliminato={n_policy}", status_code=303)


@router.post("/varchi/{varco_id}/attivo")
def cambia_attivo_varco(request: Request, varco_id: int, db: Session = Depends(get_db)):
    """
    Attiva/disattiva un varco (solo amministratori). Un varco disattivato
    nega sempre l'accesso — tramite policy_engine per il flusso sincrono, e
    tramite una cache vuota (nessun badge autorizzato) per gli slave UDP che
    decidono in locale — qualunque sia la policy collegata; serve a chiudere
    un varco per manutenzione/emergenza senza toccarne le policy. Blocca
    anche l'apertura remota manuale (vedi apri_varco_remoto sotto). La
    riattivazione non richiede conferma; la disattivazione la chiede lato
    client (confirm()), perché smette di far entrare chiunque finché non la
    riattivi di nuovo.
    """
    admin = require_admin(request, db)
    v = db.query(Varco).filter(Varco.id == varco_id).first()
    if not v:
        return RedirectResponse("/varchi", status_code=303)
    v.attivo = not v.attivo
    db.commit()
    if v.attivo:
        registra_log(db, t("Varco '%(nome)s' riattivato da %(username)s", nome=v.nome, username=admin.username), categoria="varchi")
    else:
        registra_log(
            db, t("Varco '%(nome)s' disattivato da %(username)s: nega l'accesso a chiunque finché non viene riattivato", nome=v.nome, username=admin.username),
            livello="warning", categoria="varchi",
        )

    # Uno slave che decide in locale continuerebbe ad aprire con la cache
    # vecchia fino al prossimo invio (giro automatico ogni 6h o "Scarica
    # dati"): per una chiusura di emergenza non basta. Come per il cambio di
    # modalità (routers/modalita.py), manda subito la cache di QUESTO varco
    # soltanto e aspetta la conferma CA come fa "Scarica dati".
    try:
        mandati = udp_agent.invia_cache(db, solo_varco_id=v.id)
        confermato = bool(mandati) and all(udp_agent.attendi_conferme_cache(mandati, timeout_sec=2.0).values())
    except Exception:
        confermato = False
    if not confermato:
        registra_log(
            db, t("Varco '%(nome)s' %(p)s: il dispositivo non ha confermato la nuova cache entro 2s (offline?) — la riceverà al prossimo invio", nome=v.nome, p=t('riattivato') if v.attivo else t('disattivato')),
            livello="warning", categoria="varchi",
        )
    esito_cache = "ok" if confermato else "nessuna"
    return RedirectResponse(f"/varchi?apri={varco_id}&scheda=controllo&attivo_cache={esito_cache}", status_code=303)


@router.post("/varchi/scarica-dati")
def scarica_dati_varchi(request: Request, redirect_to: str = Form("/varchi"), db: Session = Depends(get_db)):
    """
    Bottone 'Scarica dati ai dispositivi': manda subito (non aspetta le 6h)
    la cache di autorizzazioni a tutti i varchi via UDP (unicast a ciascuno), poi
    aspetta fino a 2s la conferma di consegna reale (pacchetto CA mandato
    dallo slave dopo aver elaborato la cache) prima di rispondere — un
    "inviato" da solo non dice se qualcuno l'ha davvero ricevuto, qui invece
    sì. Nessun invio automatico ad ogni salvataggio: solo qui, su richiesta
    esplicita dell'admin (puo' cambiare idea piu' volte prima di essere
    pronto a distribuire le modifiche).
    """
    user = require_login(request, db)
    n_tot = n_ok = 0
    try:
        mandati = udp_agent.invia_cache(db)
        confermati = udp_agent.attendi_conferme_cache(mandati, timeout_sec=2.0)
        n_tot = len(mandati)
        n_ok = sum(1 for ok in confermati.values() if ok)

        if n_tot == 0:
            esito = "vuoto"
        elif n_ok == n_tot:
            esito = "ok"
        elif n_ok > 0:
            esito = "parziale"
        else:
            esito = "nessuno"

        if n_ok < n_tot:
            non_confermati = [varco_id for varco_id, ok in confermati.items() if not ok]
            registra_log(
                db,
                t("Dati inviati ai dispositivi via UDP da %(username)s: confermati %(n_ok)s/%(n_tot)s (nessuna risposta entro 2s da varco_id=%(non_confermati)s)", username=user.username, n_ok=n_ok, n_tot=n_tot, non_confermati=non_confermati),
                livello="warning", categoria="varchi",
            )
        else:
            registra_log(
                db,
                t("Dati inviati e confermati da tutti i %(n_tot)s varchi via UDP da %(username)s", n_tot=n_tot, username=user.username),
                categoria="varchi",
            )
    except Exception:
        esito = "err"
        registra_log(db, t("Invio dati ai dispositivi fallito (richiesto da %(username)s)", username=user.username), livello="error", categoria="varchi")

    destinazione = redirect_to if redirect_to.startswith("/") else "/varchi"
    separatore = "&" if "?" in destinazione else "?"
    return RedirectResponse(
        f"{destinazione}{separatore}scaricato={esito}&confermati={n_ok}&totali={n_tot}", status_code=303
    )


@router.post("/varchi/{varco_id}/apri")
def apri_varco_remoto(
    request: Request, varco_id: int,
    redirect_to: str = Form("/varchi"),
    db: Session = Depends(get_db),
):
    """
    Bottone 'Apri' dalla webapp: manda il comando di apertura via UDP
    (unicast) all'IP configurato per quel varco e logga l'evento. `redirect_to`
    permette ad altre pagine (es. dashboard) di riportare l'utente dove si
    trovava invece che sempre su /varchi.

    Il comando e' "a perdere" come ogni pacchetto UDP: se lo slave e' spento
    nessuno lo recupera dopo (e' voluto, la porta non deve aprirsi a sorpresa
    minuti dopo). Se il varco non ha un IP configurato non c'e' nessun modo di
    raggiungerlo e si torna indietro con un errore esplicito.
    """
    user = require_login(request, db)
    varco = db.query(Varco).filter(Varco.id == varco_id).first()
    destinazione = redirect_to if redirect_to.startswith("/") else "/varchi"
    separatore = "&" if "?" in destinazione else "?"
    if varco and not varco.attivo:
        # Varco disattivato da un amministratore: l'apertura remota manuale
        # non deve poter bypassare la disattivazione, altrimenti sarebbe
        # inutile per chiudere davvero un varco.
        registra_log(
            db, t("Apertura remota di '%(nome)s' bloccata: il varco è disattivato (%(username)s)", nome=varco.nome, username=user.username),
            livello="warning", categoria="varchi",
        )
        db.commit()
        return RedirectResponse(f"{destinazione}{separatore}errore=varco_disattivato", status_code=303)
    if varco and varco.ip_address and not _calcola_stato_online([varco])[0].online:
        # Il comando UDP e' «a perdere»: a un dispositivo offline non arriva e non va registrata un'apertura mai avvenuta.
        registra_log(
            db, t("Apertura remota di '%(nome)s' non inviata: il dispositivo e' offline (%(username)s)", nome=varco.nome, username=user.username),
            livello="warning", categoria="varchi", varco_id=varco.id, codice="CMD_OFFLINE",
        )
        return RedirectResponse(f"{destinazione}{separatore}errore=dispositivo_offline", status_code=303)
    if varco:
        if not udp_agent.invia_apri(varco):
            registra_log(
                db, t("Apertura remota di '%(nome)s' non inviata: IP del varco non configurato o rete non raggiungibile (%(username)s)", nome=varco.nome, username=user.username),
                livello="warning", categoria="varchi",
            )
            return RedirectResponse(f"{destinazione}{separatore}errore=apertura_non_inviata", status_code=303)
        db.add(Evento(
            timestamp=datetime.utcnow(),
            varco_id=varco.id,
            esito=EsitoEvento.apertura_remota,
        ))
        db.commit()
        registra_log(db, t("Apertura remota richiesta per '%(nome)s' da %(username)s (udp)", nome=varco.nome, username=user.username), categoria="varchi")
    return RedirectResponse(destinazione, status_code=303)


# ---------- Fasce orarie ----------

@router.get("/fasce-orarie", response_class=HTMLResponse)
def lista_fasce(request: Request, apri: int = 0, db: Session = Depends(get_db)):
    user = require_login(request, db)
    fasce = db.query(FasciaOraria).order_by(FasciaOraria.nome).all()
    fascia_aperta = next((f for f in fasce if f.id == apri), None)

    # Per ogni fascia, prepariamo una griglia 7 x 48 di booleani (giorno x slot
    # da 30 minuti) già pronta per il template: griglia[fascia.id][giorno][slot] = True/False
    griglie = {}
    for f in fasce:
        griglia = [[False] * TOTALE_SLOT for _ in range(8)]   # riga 7 = giorni festivi
        for r in f.righe:
            slot_i = (r.ora_inizio.hour * 60 + r.ora_inizio.minute) // SLOT_MINUTI
            minuti_fine = r.ora_fine.hour * 60 + r.ora_fine.minute
            slot_f = -(-minuti_fine // SLOT_MINUTI)  # arrotonda all'alto (slot finale escluso)
            for s in range(slot_i, min(slot_f, TOTALE_SLOT)):
                griglia[r.giorno_settimana][s] = True
        griglie[f.id] = griglia

    etichette_ore = [_etichetta_slot(s) for s in range(TOTALE_SLOT)]

    def _tipo(griglia):
        """Descrizione breve del tipo di fascia, ricavata dalla griglia (nessun dato in piu' da tenere)."""
        griglia = griglia[:7]   # il tipo descrive la settimana, non i festivi
        slot = sum(sum(r) for r in griglia)
        if not slot:
            return t("Nessun orario impostato")
        ore = slot / 2
        ore_txt = t("%(ore)s ore a settimana", ore=f"{ore:g}".replace(".", ","))
        attivi = [g for g in range(7) if any(griglia[g])]
        if slot == 7 * TOTALE_SLOT:
            return t("Sempre, 24 ore su 24")
        if set(attivi) <= {5, 6}:
            return t("Solo weekend, %(ore_txt)s", ore_txt=ore_txt)
        if set(attivi) <= {0, 1, 2, 3, 4}:
            max_giorno = max(sum(griglia[g]) for g in attivi) / 2
            return t("Orario d'ufficio, %(ore_txt)s", ore_txt=ore_txt) if max_giorno <= 12 else t("Giorni feriali, %(ore_txt)s", ore_txt=ore_txt)
        return t("Tutta la settimana, %(ore_txt)s", ore_txt=ore_txt) if len(attivi) == 7 else t("Personalizzata, %(ore_txt)s", ore_txt=ore_txt)

    return templates.TemplateResponse(
        "fasce_orarie.html",
        {
            "request": request, "user": user, "fasce": fasce, "griglie": griglie,
            "fascia_aperta": fascia_aperta, "tipi": {f.id: _tipo(griglie[f.id]) for f in fasce},
            "giorni": GIORNI, "giorni_estesi": GIORNI_ESTESI, "ore": range(TOTALE_SLOT), "etichette_ore": etichette_ore,
            "etichette_slot": [_etichetta_slot(s) for s in range(TOTALE_SLOT + 1)],
            "riepiloghi": {f.id: permessi_service.descrivi_fasce(f.righe) for f in fasce},
            "uso_fasce": dict(
                db.query(Policy.fascia_oraria_id, func.count(Policy.id))
                .filter(Policy.fascia_oraria_id.isnot(None)).group_by(Policy.fascia_oraria_id).all()
            ),
        },
    )


@router.post("/fasce-orarie")
def crea_fascia(request: Request, nome: str = Form(...), db: Session = Depends(get_db)):
    require_login(request, db)
    db.add(FasciaOraria(nome=nome))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(
            "/fasce-orarie?errore=nome_duplicato", status_code=303
        )
    registra_log(db, t("Fascia oraria creata: %(nome)s", nome=nome), categoria="fasce_orarie")
    nuova = db.query(FasciaOraria).filter(FasciaOraria.nome == nome).first()
    return RedirectResponse(f"/fasce-orarie?apri={nuova.id}" if nuova else "/fasce-orarie", status_code=303)


@router.post("/fasce-orarie/{fascia_id}/delete")
def elimina_fascia(request: Request, fascia_id: int, db: Session = Depends(get_db)):
    require_login(request, db)
    f = db.query(FasciaOraria).filter(FasciaOraria.id == fascia_id).first()
    if f:
        nome = f.nome
        # Le regole che la usano restano, senza fascia: incomplete, non concedono niente finche' non ne scelgono un'altra.
        usate = db.query(Policy).filter(Policy.fascia_oraria_id == f.id).all()
        n_policy = len(usate)
        for p in usate:
            p.fascia_oraria_id = None
            p.sempre = False
        # Un varco in "sblocco programmato" con questa fascia tornerebbe senza orari:
        # lo si riporta alla modalita' normale invece di lasciarlo con un riferimento rotto.
        for v in db.query(Varco).filter(Varco.sblocco_fascia_id == f.id).all():
            v.modalita, v.sblocco_fascia_id = "normale", None
            registra_log(db, t("Varco '%(nome)s' riportato a modalità normale: la fascia di sblocco '%(nome2)s' è stata eliminata", nome=v.nome, nome2=nome),
                         livello="warning", categoria="varchi")
        db.delete(f)
        db.commit()
        registra_log(db, t("Fascia oraria eliminata: %(nome)s (%(n_policy)s regole rimaste senza fascia, incomplete)", nome=nome, n_policy=n_policy), livello="warning", categoria="fasce_orarie")
        return RedirectResponse(f"/fasce-orarie?eliminata=1&incomplete={n_policy}", status_code=303)
    return RedirectResponse("/fasce-orarie", status_code=303)


@router.post("/fasce-orarie/{fascia_id}/griglia")
async def salva_griglia_fascia(request: Request, fascia_id: int, db: Session = Depends(get_db)):
    """
    Riceve i checkbox selezionati nella griglia settimanale (nome tipo
    'cella_2_16' = giorno 2, slot 16 = 08:00) e li converte in righe
    FasciaOrariaRiga, unendo gli slot contigui (da 30 minuti) dello stesso
    giorno in un unico intervallo (es. slot 16,17,18,19 -> una riga sola
    08:00-10:00).
    """
    require_login(request, db)
    fascia = db.query(FasciaOraria).filter(FasciaOraria.id == fascia_id).first()
    form = await request.form()
    if fascia:
        nuovo_nome = str(form.get("nome", "")).strip()
        if nuovo_nome and nuovo_nome != fascia.nome:
            vecchio = fascia.nome
            fascia.nome = nuovo_nome
            try:
                db.flush()
                registra_log(db, t("Fascia oraria rinominata: %(vecchio)s → %(nuovo_nome)s", vecchio=vecchio, nuovo_nome=nuovo_nome), categoria="fasce_orarie")
            except IntegrityError:
                db.rollback()
                return RedirectResponse(f"/fasce-orarie?apri={fascia_id}&errore=nome_duplicato", status_code=303)
    selezionate = set()
    for chiave in form.keys():
        if chiave.startswith("cella_"):
            _, giorno_str, slot_str = chiave.split("_")
            selezionate.add((int(giorno_str), int(slot_str)))

    # Ripulisce le righe esistenti e le ricrea dalla griglia inviata
    db.query(FasciaOrariaRiga).filter(FasciaOrariaRiga.fascia_id == fascia_id).delete()
    modifiche.segna_modifica_globale(db)   # la cancellazione in blocco non passa dagli eventi: senza questo «Da fare» non sa che e' cambiata

    for giorno in range(8):   # 7 = giorni festivi
        slot_giorno = sorted(s for (g, s) in selezionate if g == giorno)
        if not slot_giorno:
            continue
        # Unisce slot contigui in intervalli: [16,17,18,28,29] -> (16-19),(28-30)
        inizio = slot_giorno[0]
        precedente = slot_giorno[0]
        for s in slot_giorno[1:] + [None]:
            if s is not None and s == precedente + 1:
                precedente = s
                continue
            db.add(FasciaOrariaRiga(
                fascia_id=fascia_id,
                giorno_settimana=giorno,
                ora_inizio=_slot_a_time(inizio),
                ora_fine=_slot_a_time(precedente + 1),
            ))
            if s is not None:
                inizio = s
                precedente = s

    db.commit()
    if fascia:
        registra_log(db, t("Griglia orari aggiornata per fascia '%(nome)s'", nome=fascia.nome), categoria="fasce_orarie")
    return RedirectResponse("/fasce-orarie?salvata=1", status_code=303)


# ---------- Gruppi ----------

@router.get("/gruppi", response_class=HTMLResponse)
def lista_gruppi(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    gruppi = db.query(Gruppo).all()
    policy_disponibili = db.query(Policy).filter(Policy.attiva == True).all()  # noqa: E712

    # Stessi tag-picker usati su Persone: per ciascun gruppo, le regole già
    # assegnate (chip) e quelle ancora disponibili (menu "+ Aggiungi").
    policy_per_gruppo = {}
    for g in gruppi:
        sel_ids = {p.id for p in g.policies}
        policy_per_gruppo[g.id] = {
            "selezionate": [{"id": p.id, "label": _label_policy(p)} for p in g.policies],
            "disponibili": [{"id": p.id, "label": _label_policy(p)} for p in policy_disponibili if p.id not in sel_ids],
        }

    # Modifica in corsa (?modifica=ID): quel gruppo mostra nome e membri come campi.
    try:
        modifica_id = int(request.query_params.get("modifica", ""))
    except ValueError:
        modifica_id = None
    if modifica_id not in {g.id for g in gruppi}:
        modifica_id = None
    membri = None
    if modifica_id is not None:
        gm = next(g for g in gruppi if g.id == modifica_id)
        ids = {p.id for p in gm.persone}
        tutte = db.query(Persona).order_by(Persona.cognome, Persona.nome).all()
        etichetta = lambda p: f"{p.cognome} {p.nome}"
        membri = {
            "selezionate": [{"id": p.id, "label": etichetta(p)} for p in tutte if p.id in ids],
            "disponibili": [{"id": p.id, "label": etichetta(p)} for p in tutte if p.id not in ids],
        }

    return templates.TemplateResponse(
        "gruppi.html",
        {
            "request": request, "user": user, "gruppi": gruppi, "policy_disponibili": policy_disponibili,
            "policy_per_gruppo": policy_per_gruppo, "modifica_id": modifica_id, "membri": membri,
        },
    )


@router.post("/gruppi")
def crea_gruppo(request: Request, nome: str = Form(...), db: Session = Depends(get_db)):
    require_login(request, db)
    db.add(Gruppo(nome=nome))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/gruppi?errore=nome_duplicato", status_code=303)
    registra_log(db, t("Gruppo creato: %(nome)s", nome=nome), categoria="gruppi")
    return RedirectResponse("/gruppi", status_code=303)


@router.post("/gruppi/{gruppo_id}/modifica")
async def modifica_gruppo(request: Request, gruppo_id: int, db: Session = Depends(get_db)):
    """Rinomina un gruppo e ne imposta i membri. Le regole del gruppo hanno il loro modulo."""
    require_login(request, db)
    g = db.query(Gruppo).filter(Gruppo.id == gruppo_id).first()
    if g is None:
        return RedirectResponse("/gruppi", status_code=303)
    form = await request.form()
    nome = (form.get("nome") or "").strip()
    if not nome:
        return RedirectResponse(f"/gruppi?modifica={g.id}&errore=nome_vuoto", status_code=303)
    try:
        ids = [int(v) for v in form.getlist("persona_id")]
    except ValueError:
        ids = []
    vecchio_nome = g.nome
    prima = {p.id for p in g.persone}
    prima_pol = {p.id for p in g.policies}
    g.nome = nome
    g.persone = db.query(Persona).filter(Persona.id.in_(ids)).all() if ids else []
    if form.get("con_policy"):   # il modulo di modifica porta con se' anche le regole
        try:
            ids_pol = [int(v) for v in form.getlist("policy_id")]
        except ValueError:
            ids_pol = []
        g.policies = db.query(Policy).filter(Policy.id.in_(ids_pol)).all() if ids_pol else []
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse(f"/gruppi?modifica={gruppo_id}&errore=nome_duplicato", status_code=303)
    dopo = {p.id for p in g.persone}
    cambi = []
    if nome != vecchio_nome:
        cambi.append(t("rinominato da '%(vecchio_nome)s'", vecchio_nome=vecchio_nome))
    if dopo - prima:
        cambi.append(t("%(n)s persone aggiunte", n=len(dopo - prima)))
    if prima - dopo:
        cambi.append(t("%(n)s persone tolte", n=len(prima - dopo)))
    dopo_pol = {p.id for p in g.policies}
    if dopo_pol != prima_pol:
        cambi.append(t("regole: %(prima)s -> %(dopo)s", prima=len(prima_pol), dopo=len(dopo_pol)))
    registra_log(db, t("Gruppo '%(nome)s' modificato: %(cambi)s", nome=nome, cambi=", ".join(cambi) or t("nessuna differenza")), categoria="gruppi")
    return RedirectResponse(f"/gruppi?salvato={g.id}", status_code=303)


@router.post("/gruppi/{gruppo_id}/delete")
def elimina_gruppo(request: Request, gruppo_id: int, db: Session = Depends(get_db)):
    require_login(request, db)
    g = db.query(Gruppo).filter(Gruppo.id == gruppo_id).first()
    if g:
        nome = g.nome
        db.delete(g)
        db.commit()
        registra_log(db, t("Gruppo eliminato: %(nome)s", nome=nome), livello="warning", categoria="gruppi")
    return RedirectResponse("/gruppi", status_code=303)


@router.post("/gruppi/{gruppo_id}/policy")
async def aggiorna_policy_gruppo(request: Request, gruppo_id: int, db: Session = Depends(get_db)):
    require_login(request, db)
    form = await request.form()
    ids = [int(v) for v in form.getlist("policy_id")]
    g = db.query(Gruppo).filter(Gruppo.id == gruppo_id).first()
    if g:
        g.policies = db.query(Policy).filter(Policy.id.in_(ids)).all() if ids else []
        db.commit()
        registra_log(db, t("Regole del gruppo '%(nome)s' aggiornate", nome=g.nome), categoria="gruppi")
    return RedirectResponse("/gruppi", status_code=303)


# ---------- Policy (uno o piu varchi + fascia oraria: il "chi" si assegna su Persone/Gruppi) ----------

@router.get("/policy", response_class=HTMLResponse)
def lista_policy(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    policies = db.query(Policy).all()
    varchi = db.query(Varco).all()
    fasce = db.query(FasciaOraria).all()
    try:
        modifica_id = int(request.query_params.get("modifica", ""))
    except ValueError:
        modifica_id = None
    if modifica_id not in {p.id for p in policies}:
        modifica_id = None
    tutti = [{"id": v.id, "label": v.nome} for v in sorted(varchi, key=lambda v: v.nome.lower())]
    scelti = {p.id: {v.id for v in p.varchi} for p in policies}
    varchi_picker = {
        p.id: {"selezionati": [x for x in tutti if x["id"] in scelti[p.id]], "disponibili": [x for x in tutti if x["id"] not in scelti[p.id]]}
        for p in policies
    }
    return templates.TemplateResponse(
        "policy.html",
        {"request": request, "user": user, "policies": policies, "varchi": varchi, "tutti_varchi": tutti, "varchi_picker": varchi_picker,
         "fasce": fasce, "modifica_id": modifica_id},
    )


def _scelta_fascia(db: Session, valore: str):
    """Dal campo del form: («sempre», fascia) -> (True, None) se «Sempre», (False, fascia) se una fascia esistente,
    None se non e' stato scelto niente di valido."""
    if valore == "sempre":
        return True, None
    if valore.isdigit():
        f = db.query(FasciaOraria).filter(FasciaOraria.id == int(valore)).first()
        if f is not None:
            return False, f
    return None


def _varchi_scelti(db: Session, ids: list[str]) -> list[Varco]:
    """I varchi indicati dal form (id ripetuti o inesistenti ignorati), nell'ordine del nome."""
    numeri = {int(i) for i in ids if str(i).isdigit()}
    return db.query(Varco).filter(Varco.id.in_(numeri)).order_by(Varco.nome).all() if numeri else []


@router.post("/policy/{policy_id}/modifica")
async def modifica_policy(request: Request, policy_id: int, db: Session = Depends(get_db)):
    """Cambia nome, varchi, fascia oraria e stato (attiva/disattiva) di una regola. Vale subito per tutti
    quelli che la usano (persone e gruppi); ai varchi arriva con «Scarica dati» (il banner lo ricorda)."""
    require_login(request, db)
    form = await request.form()
    p = db.query(Policy).filter(Policy.id == policy_id).first()
    if p is None:
        return RedirectResponse("/policy", status_code=303)
    varchi = _varchi_scelti(db, form.getlist("varco_id"))
    scelta = _scelta_fascia(db, str(form.get("fascia_oraria_id", "")))
    if not varchi or scelta is None:
        return RedirectResponse(f"/policy?modifica={p.id}&errore=dati", status_code=303)
    sempre, fascia = scelta
    nome = str(form.get("nome", "")).strip()
    attiva = str(form.get("attiva", "")) == "1"
    prima = (p.nome or "", p.nomi_varchi, p.fascia_nome, bool(p.attiva))
    p.nome = nome
    p.varchi = varchi
    p.sempre = sempre
    p.fascia_oraria_id = fascia.id if fascia else None
    p.attiva = attiva
    db.commit()
    dopo = (p.nome or "", p.nomi_varchi, p.fascia_nome, bool(p.attiva))
    campi = ["nome", "varchi", "fascia", "stato"]
    etic_campo = {"nome": t("nome"), "varchi": t("varchi"), "fascia": t("fascia"), "stato": t("stato")}
    cambi = [f"{etic_campo[c]}: {a_ if c != 'stato' else (t('attiva') if a_ else t('disattiva'))} -> {b if c != 'stato' else (t('attiva') if b else t('disattiva'))}"
             for c, a_, b in zip(campi, prima, dopo) if a_ != b]
    registra_log(db, t("Regola '%(nome)s' modificata (%(persone)s persone dirette, %(gruppi)s gruppi): %(cambi)s", nome=p.nome or ('#' + str(p.id)), persone=len(p.persone), gruppi=len(p.gruppi), cambi="; ".join(cambi) or t("nessuna differenza")),
                 livello="info", categoria="policy")
    return RedirectResponse(f"/policy?salvata={p.id}", status_code=303)


@router.post("/policy")
async def crea_policy(request: Request, db: Session = Depends(get_db)):
    require_login(request, db)
    form = await request.form()
    varchi = _varchi_scelti(db, form.getlist("varco_id"))
    scelta = _scelta_fascia(db, str(form.get("fascia_oraria_id", "")))
    if not varchi or scelta is None:
        return RedirectResponse("/policy?errore=dati", status_code=303)
    sempre, fascia = scelta
    nome = str(form.get("nome", "")).strip()
    p = Policy(nome=nome, varchi=varchi, sempre=sempre, fascia_oraria_id=fascia.id if fascia else None)
    db.add(p)
    db.commit()
    registra_log(db, t("Regola creata: %(p)s su %(nomi_varchi)s", p=nome or '(senza nome)', nomi_varchi=p.nomi_varchi), categoria="policy")
    return RedirectResponse("/policy", status_code=303)


@router.post("/policy/{policy_id}/delete")
def elimina_policy(request: Request, policy_id: int, db: Session = Depends(get_db)):
    require_login(request, db)
    p = db.query(Policy).filter(Policy.id == policy_id).first()
    if p:
        nome = p.nome or t("Regola #%(id)s", id=p.id)
        db.delete(p)
        db.commit()
        registra_log(db, t("Regola eliminata: %(nome)s", nome=nome), livello="warning", categoria="policy")
    return RedirectResponse("/policy", status_code=303)


# ---------- Monitoraggio (log passaggi/eventi ai varchi) ----------

# ---------- Alert di sistema ----------

@router.get("/alert", response_class=HTMLResponse)
def pagina_alert(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    alert = _calcola_alert(db)
    return templates.TemplateResponse(
        "alert.html",
        {
            "request": request, "user": user, "alert": alert,
            "gestiti": _allarmi_gestiti_di_recente(db), "esito_info": ESITO_INFO,
            "scadenza_ore": SCADENZA_ALLARMI_ORE,
        },
    )


@router.post("/alert/{evento_id}/gestisci")
def gestisci_alert(request: Request, evento_id: int, db: Session = Depends(get_db)):
    """Presa in carico di un singolo allarme: esce dall'elenco attivo."""
    user = require_login(request, db)
    e = db.query(Evento).filter(Evento.id == evento_id, Evento.allarme == True).first()  # noqa: E712
    if e and e.gestito_at is None:
        e.gestito_da = user.username
        e.gestito_at = datetime.utcnow()
        db.commit()
        registra_log(db, t("Allarme #%(id)s preso in carico da %(username)s", id=e.id, username=user.username), categoria="alert")
    return RedirectResponse("/alert", status_code=303)


@router.post("/alert/gestisci-tutti")
def gestisci_tutti_alert(request: Request, db: Session = Depends(get_db)):
    """Prende in carico in un colpo solo tutti gli allarmi ancora attivi.
    Gli alert 'dispositivo offline' non c'entrano: si risolvono da soli."""
    user = require_login(request, db)
    limite = datetime.utcnow() - timedelta(hours=SCADENZA_ALLARMI_ORE)
    aperti = (
        db.query(Evento)
        .filter(Evento.allarme == True, Evento.gestito_at == None, Evento.timestamp >= limite)  # noqa: E712,E711
        .all()
    )
    adesso = datetime.utcnow()
    for e in aperti:
        e.gestito_da = user.username
        e.gestito_at = adesso
    db.commit()
    if aperti:
        registra_log(db, t("%(p)s allarmi presi in carico da %(username)s", p=len(aperti), username=user.username), categoria="alert")
    return RedirectResponse("/alert", status_code=303)


@router.get("/alert/count", response_class=HTMLResponse)
def conteggio_alert(request: Request, db: Session = Depends(get_db)):
    require_login(request, db)
    n = len(_calcola_alert(db))
    if not n:
        return HTMLResponse("")
    return HTMLResponse(f'<span class="nav-badge">{n}</span>')


# ---------- Log di sistema ----------
# Il log vive nella scheda «Log» di Info sistema (/sistema/info?scheda=log). I frammenti htmx
# (righe, dettaglio) e l'esportazione restano sotto /log/... ; la vecchia pagina /log rimanda alla scheda.

LOG_PER_PAGINA = 100
LOG_MAX_CSV = 50_000
LOG_ETICHETTE_CATEGORIA = {
    "dispositivi": pigro("Dispositivi"), "rete": pigro("Rete"), "varchi": pigro("Varchi"), "sistema": pigro("Sistema"), "auth": pigro("Accessi"),
    "persone": pigro("Persone"), "gruppi": pigro("Gruppi"), "policy": pigro("Regole di accesso"), "fasce": pigro("Fasce orarie"), "fasce_orarie": pigro("Fasce orarie"),
    "utenti": pigro("Utenti"), "alert": pigro("Alert"), "presenze": pigro("Presenze"),
}
LOG_PERIODI = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7g": timedelta(days=7)}
LOG_LIVELLI_CHIP = (("", pigro("Tutto")), ("warning", pigro("Avvisi ed errori")), ("error", pigro("Errori")), ("debug", pigro("Diagnostica")))
LOG_FINESTRA_CONTESTO = timedelta(minutes=5)


def _filtri_log(params) -> dict:
    def _par(n):
        return (params.get(n) or "").strip()
    try:
        varco = int(_par("varco")) if _par("varco") else None
    except ValueError:
        varco = None
    try:
        pagina = max(1, int(_par("pagina") or 1))
    except ValueError:
        pagina = 1
    livello = _par("livello") if _par("livello") in (l.value for l in LivelloLog) else ""
    periodo = _par("periodo") if _par("periodo") in LOG_PERIODI else ""
    return {
        "livello": livello, "categoria": _par("categoria"), "varco": varco, "q": _par("q"), "pagina": pagina,
        "codice": _par("codice")[:40], "utente": _par("utente")[:80], "periodo": periodo, "da": _par("da")[:16], "a": _par("a")[:16],
    }


def _filtri_attivi_log(f: dict) -> bool:
    return any(f[k] for k in ("livello", "categoria", "varco", "q", "codice", "utente", "periodo", "da", "a"))


def _qs_log(f: dict, **extra) -> str:
    """Query string dei filtri attivi (per paginazione ed esportazione); `extra` aggiunge o sovrascrive."""
    dati = {k: f[k] for k in ("livello", "categoria", "varco", "q", "codice", "utente", "periodo", "da", "a") if f[k]}
    dati.update({k: v for k, v in extra.items() if v not in (None, "")})
    return urlencode(dati)


def _query_log(db: Session, f: dict):
    query = db.query(LogSistema)
    if f["livello"] == "warning":
        query = query.filter(LogSistema.livello.in_([LivelloLog.warning, LivelloLog.error]))   # «da guardare»: avvisi ed errori
    elif f["livello"]:
        query = query.filter(LogSistema.livello == LivelloLog(f["livello"]))
    else:
        query = query.filter(LogSistema.livello != LivelloLog.debug)      # le righe di diagnostica solo se le chiedi
    if f["categoria"]:
        query = query.filter(LogSistema.categoria == f["categoria"])
    if f["varco"]:
        query = query.filter(LogSistema.varco_id == f["varco"])
    if f["codice"]:
        if f["codice"].endswith("*"):
            query = query.filter(LogSistema.codice.like(f["codice"][:-1] + "%"))
        else:
            query = query.filter(LogSistema.codice == f["codice"])
    if f["utente"]:
        query = query.filter(LogSistema.utente == f["utente"])
    if f["periodo"]:
        query = query.filter(LogSistema.timestamp >= datetime.utcnow() - LOG_PERIODI[f["periodo"]])
    else:
        dal, al = tempo.da_input_locale(f["da"]), tempo.da_input_locale(f["a"])
        if dal:
            query = query.filter(LogSistema.timestamp >= dal)
        if al:
            query = query.filter(LogSistema.timestamp <= al + timedelta(minutes=1))     # l'ultimo minuto indicato e' compreso
    for parola in f["q"].split():
        like = f"%{parola}%"
        query = query.filter(or_(
            LogSistema.messaggio.ilike(like), LogSistema.codice.ilike(like), LogSistema.utente.ilike(like),
            LogSistema.origine_ip.ilike(like), LogSistema.dettagli.ilike(like),
        ))
    return query.order_by(LogSistema.timestamp.desc(), LogSistema.id.desc())


def _contesto_log(db: Session, f: dict) -> dict:
    query = _query_log(db, f)
    totale = query.count()
    pagine = max(1, -(-totale // LOG_PER_PAGINA))
    pagina = min(f["pagina"], pagine)
    righe = query.offset((pagina - 1) * LOG_PER_PAGINA).limit(LOG_PER_PAGINA).all()
    nomi = {vid: nome for vid, nome in db.query(Varco.id, Varco.nome).all()}
    return {"righe": righe, "totale": totale, "pagina": pagina, "pagine": pagine, "nomi_varchi": nomi}


def _riepilogo_log_24h(db: Session) -> dict:
    """Quante righe per livello nelle ultime 24 h (per il colpo d'occhio sopra ai filtri)."""
    da = datetime.utcnow() - timedelta(hours=24)
    righe = db.query(LogSistema.livello, func.count()).filter(LogSistema.timestamp >= da).group_by(LogSistema.livello).all()
    conta = {l.value: n for l, n in righe}
    return {k: conta.get(k, 0) for k in ("error", "warning", "info", "debug")}


def contesto_scheda_log(request: Request, db: Session) -> dict:
    """Tutto quello che serve alla scheda Log di Info sistema."""
    f = _filtri_log(request.query_params)
    ctx = _contesto_log(db, f)
    categorie = sorted({c for (c,) in db.query(LogSistema.categoria).distinct().all() if c})
    codici = sorted({c for (c,) in db.query(LogSistema.codice).distinct().all() if c})
    utenti_log = sorted({u for (u,) in db.query(LogSistema.utente).distinct().all() if u})
    ha_debug = connessioni.diagnostica_attiva() or db.query(LogSistema.id).filter(LogSistema.livello == LivelloLog.debug).first() is not None
    ctx.update({
        "f": f, "varchi": db.query(Varco).order_by(Varco.nome).all(),
        "categorie": [(c, LOG_ETICHETTE_CATEGORIA.get(c, c.capitalize())) for c in categorie],
        "codici": [(c, log_codici.etichetta(c)) for c in codici], "utenti_log": utenti_log,
        "filtri_attivi": _filtri_attivi_log(f), "per_pagina": LOG_PER_PAGINA, "qs": _qs_log(f),
        "riepilogo": _riepilogo_log_24h(db), "livelli_chip": [c for c in LOG_LIVELLI_CHIP if c[0] != "debug" or ha_debug],
        "diag_attiva": connessioni.diagnostica_attiva(), "diag_residuo_min": -(-connessioni.diagnostica_residuo_sec() // 60),
        "diag_max_min": connessioni.DIAGNOSTICA_MAX_MIN,
    })
    return ctx


@router.get("/log")
def pagina_log(request: Request, db: Session = Depends(get_db)):
    """Vecchio indirizzo: il log e' diventato una scheda di Info sistema (i filtri nel link restano)."""
    require_login(request, db)
    qs = urlencode({"scheda": "log", **{k: v for k, v in request.query_params.items() if k != "scheda"}})
    return RedirectResponse(f"/sistema/info?{qs}", status_code=301)


@router.get("/log/righe", response_class=HTMLResponse)
def righe_log(request: Request, db: Session = Depends(get_db)):
    """Solo le righe della tabella: risposta del polling htmx della scheda Log."""
    require_login(request, db)
    f = _filtri_log(request.query_params)
    ctx = _contesto_log(db, f)
    ctx.update({"request": request, "f": f, "filtri_attivi": _filtri_attivi_log(f)})
    return templates.TemplateResponse("_log_righe.html", ctx)


def _dettagli_leggibili(testo: Optional[str]):
    """Coppie (nome, valore) dai dettagli JSON; se il testo e' stato troncato resta com'e'."""
    if not testo:
        return [], None
    try:
        dati = json.loads(testo)
    except ValueError:
        return [], testo
    if not isinstance(dati, dict):
        return [], testo
    return [(str(k).replace("_", " "), v if isinstance(v, (str, int, float, bool)) else json.dumps(v, ensure_ascii=False)) for k, v in dati.items()], None


@router.get("/log/{riga_id}/dettaglio", response_class=HTMLResponse)
def dettaglio_log(request: Request, riga_id: int, db: Session = Depends(get_db)):
    """Contenuto del drawer di una riga del log: tutti i campi, il significato del codice e il link al contesto."""
    require_login(request, db)
    r = db.query(LogSistema).filter(LogSistema.id == riga_id).first()
    if r is None:
        return HTMLResponse(t("<p class='muted'>Riga non trovata (forse e' stata ripulita).</p>"))
    varco = db.query(Varco).filter(Varco.id == r.varco_id).first() if r.varco_id else None
    coppie, grezzo = _dettagli_leggibili(r.dettagli)
    contesto = {"da": tempo.a_input_locale(r.timestamp - LOG_FINESTRA_CONTESTO), "a": tempo.a_input_locale(r.timestamp + LOG_FINESTRA_CONTESTO),
                "scheda": "log", "livello": "debug" if r.livello == LivelloLog.debug else ""}
    if r.varco_id:
        contesto["varco"] = r.varco_id
    testo_copia = "\n".join(filter(None, [
        f"Limen log #{r.id}", f"Ora: {tempo.formatta(r.timestamp)} (UTC {r.timestamp.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]})",
        f"Livello: {r.livello.value}", f"Categoria: {r.categoria or '-'}", f"Codice: {r.codice or '-'}",
        f"Varco: {varco.nome if varco else (('n. ' + str(r.varco_id)) if r.varco_id else '-')}",
        f"Utente: {r.utente or '-'}" + (f" da {r.origine_ip}" if r.origine_ip else ""),
        f"Messaggio: {r.messaggio}",
        *(f"  {k}: {v}" for k, v in coppie), (f"  dettagli: {grezzo}" if grezzo else ""),
    ]))
    return templates.TemplateResponse("_log_dettaglio.html", {
        "request": request, "r": r, "varco": varco, "coppie": coppie, "grezzo": grezzo,
        "codice_etichetta": log_codici.etichetta(r.codice), "codice_descrizione": log_codici.descrizione(r.codice),
        "link_contesto": "/sistema/info?" + urlencode(contesto), "testo_copia": testo_copia,
        "utc_ms": r.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
    })


def _righe_csv_log(db: Session, righe, nomi):
    def sicura(v):
        t = "" if v is None else str(v)
        return "'" + t if t[:1] in ("=", "+", "-", "@") else t
    for r in righe:
        yield [tempo.formatta(r.timestamp), r.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z", r.livello.value, sicura(r.categoria),
               sicura(r.codice), sicura(nomi.get(r.varco_id, "")), sicura(r.utente), sicura(r.origine_ip), sicura(r.messaggio), sicura(r.dettagli)]


CSV_INTESTAZIONE_LOG = [t("Data/ora"), t("UTC"), t("Livello"), t("Categoria"), t("Codice"), t("Varco"), t("Utente"), t("IP origine"), t("Messaggio"), t("Dettagli (JSON)")]


@router.get("/log/export.csv")
def esporta_log(request: Request, db: Session = Depends(get_db)):
    require_login(request, db)
    import csv, io
    f = _filtri_log(request.query_params)
    nomi = {vid: nome for vid, nome in db.query(Varco.id, Varco.nome).all()}
    out = io.StringIO()
    w = csv.writer(out, delimiter=";", lineterminator="\r\n")
    w.writerow(CSV_INTESTAZIONE_LOG)
    for riga in _righe_csv_log(db, _query_log(db, f).limit(LOG_MAX_CSV).all(), nomi):
        w.writerow(riga)
    nome = f"log-{tempo.adesso_locale().strftime('%Y%m%d-%H%M')}.csv"
    return Response(content="\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@router.post("/sistema/log/diagnostica")
def diagnostica_log(request: Request, azione: str = Form("attiva"), minuti: int = Form(30), db: Session = Depends(get_db)):
    """Modalita' diagnostica: per un tempo limitato ogni pacchetto ricevuto/inviato finisce nel log (livello debug)."""
    user = require_admin(request, db)
    if azione == "spegni":
        connessioni.disattiva_diagnostica()
        registra_log(db, t("Modalita' diagnostica spenta da %(username)s", username=user.username), categoria="sistema", codice="SYS_DIAGNOSTICA")
    else:
        m = connessioni.attiva_diagnostica(minuti)
        registra_log(db, t("Modalita' diagnostica attivata da %(username)s per %(m)s minuti: ogni pacchetto verra' registrato.", username=user.username, m=m),
                     "warning", "sistema", codice="SYS_DIAGNOSTICA", dettagli={"minuti": m})
    return RedirectResponse("/sistema/info?scheda=log", status_code=303)


@router.get("/sistema/diagnostica.zip")
def pacchetto_diagnostico(request: Request, db: Session = Depends(get_db)):
    """Tutto quello che serve a chi deve capire un problema da remoto, in un file solo (niente chiavi ne' password)."""
    user = require_admin(request, db)
    import csv, io, zipfile
    adesso = datetime.utcnow()
    nomi = {vid: nome for vid, nome in db.query(Varco.id, Varco.nome).all()}
    righe = (db.query(LogSistema).filter(LogSistema.timestamp >= adesso - timedelta(days=7))
             .order_by(LogSistema.timestamp.desc(), LogSistema.id.desc()).limit(LOG_MAX_CSV).all())
    csv_out = io.StringIO()
    w = csv.writer(csv_out, delimiter=";", lineterminator="\r\n")
    w.writerow(CSV_INTESTAZIONE_LOG)
    for riga in _righe_csv_log(db, righe, nomi):
        w.writerow(riga)
    varchi = _calcola_stato_online(db.query(Varco).order_by(Varco.id).all())
    stato = raccogli_stato(db)
    dati = {
        "generato_il_utc": adesso.isoformat() + "Z", "generato_da": user.username, "fuso_minuti": tempo.fuso_minuti(),
        "avvio_applicazione_utc": AVVIATO_IL.isoformat() + "Z", "udp": udp_agent.stato(),
        "diagnostica_attiva": connessioni.diagnostica_attiva(),
        "sistema": raccogli_info_sistema(),
        "verdetto": stato["verdetto"],
        "controlli_sistema": [{k: c.get(k) for k in ("titolo", "livello", "dettaglio")} for c in stato["sistema"]],
        "varchi": [{
            "id": v.id, "nome": v.nome, "attivo": bool(v.attivo), "ip": v.ip_address, "online": bool(v.online),
            "ultimo_contatto_utc": v.ultimo_contatto.isoformat() + "Z" if v.ultimo_contatto else None,
            "modalita": v.modalita, "cache_inviata": v.ultima_cache_versione, "cache_confermata": v.ultima_cache_confermata,
            "deriva_orologio_s": v.deriva_sec, "stato_porta": v.porta_stato, "funzionamento": v.funzionamento,
            "controlli": [{k: c.get(k) for k in ("titolo", "livello", "dettaglio")} for c in salute.controlli_varco(v, adesso)],
        } for v in varchi],
    }
    leggimi = ("Pacchetto diagnostico Limen\n"
               f"Generato il {tempo.formatta(adesso)} da {user.username}.\n\n"
               "log.csv       righe del log degli ultimi 7 giorni (tutti i livelli, comprese quelle di diagnostica)\n"
               "diagnosi.json stato del sistema, dei controlli e dei varchi (senza chiavi ne' password)\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("LEGGIMI.txt", leggimi)
        z.writestr("log.csv", "\ufeff" + csv_out.getvalue())
        z.writestr("diagnosi.json", json.dumps(dati, ensure_ascii=False, indent=2, default=str))
    registra_log(db, t("Pacchetto diagnostico scaricato da %(username)s", username=user.username), categoria="sistema", codice="SYS_DIAGNOSI")
    nome = f"limen-diagnosi-{tempo.adesso_locale().strftime('%Y%m%d-%H%M')}.zip"
    return Response(content=buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{nome}"'})


# ---------- Info sistema (dispositivo su cui gira l'app) ----------

def raccogli_stato(db: Session) -> dict:
    """Controlli di salute del sistema e di ogni varco, con il verdetto complessivo."""
    adesso = datetime.utcnow()
    sistema = salute.controlli_sistema(db, adesso)
    varchi = _calcola_stato_online(db.query(Varco).order_by(Varco.nome).all())
    per_varco = [{"varco": v, "controlli": salute.controlli_varco(v, adesso)} for v in varchi]
    tutti = sistema + [c for g in per_varco for c in g["controlli"]]
    return {"sistema": sistema, "per_varco": per_varco, "verdetto": salute.verdetto(tutti)}


@router.get("/sistema/info", response_class=HTMLResponse)
def info_sistema(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if request.query_params.get("scheda") == "log":
        return templates.TemplateResponse(
            "info_sistema.html", {"request": request, "user": user, "scheda": "log", **contesto_scheda_log(request, db)})
    info = raccogli_info_sistema()
    return templates.TemplateResponse(
        "info_sistema.html", {"request": request, "user": user, "scheda": "stato", "info": info, **raccogli_stato(db)})


# ---------- Proprietà di sistema (backup, riavvio, utenti, pulizia log, stato servizi) ----------
# Riservata agli amministratori (User.is_admin): backup/riavvio/pulizia log/
# gestione utenti sono azioni di sistema, non operative di tutti i giorni
# (quelle restano accessibili a chiunque loggato, come prima). Il cambio
# della propria password è l'unica eccezione, in fondo a questo blocco:
# qualunque utente deve poter cambiare la propria, non solo gli admin.

def _durata_leggibile(delta: timedelta) -> str:
    secondi = int(delta.total_seconds())
    giorni, resto = divmod(secondi, 86400)
    ore, resto = divmod(resto, 3600)
    minuti, _ = divmod(resto, 60)
    parti = []
    if giorni:
        parti.append(f"{giorni}g")
    if ore or giorni:
        parti.append(f"{ore}h")
    parti.append(f"{minuti}m")
    return " ".join(parti)


@router.get("/sistema", response_class=HTMLResponse)
def pagina_sistema(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if not user.is_admin:
        # Un operatore può solo cambiare la propria password (POST
        # /sistema/password usa require_login): la pagina gli mostra solo
        # quella sezione, senza caricare stato/utenti riservati agli admin.
        return templates.TemplateResponse("sistema.html", {"request": request, "user": user})
    varchi = _calcola_stato_online(db.query(Varco).all())
    utenti = db.query(User).order_by(User.username).all()
    numero_admin = sum(1 for u in utenti if u.is_admin)
    righe_log_totali = db.query(LogSistema).count()
    righe_log_pulibili = db.query(LogSistema).filter(LogSistema.livello == LivelloLog.info).count()
    return templates.TemplateResponse(
        "sistema.html",
        {
            "request": request, "user": user, "utenti": utenti, "numero_admin": numero_admin,
            "stato_udp": udp_agent.stato(),
            "varchi_totali": len(varchi), "varchi_online": sum(1 for v in varchi if v.online),
            "da_quando_attivo": _durata_leggibile(datetime.utcnow() - AVVIATO_IL),
            "righe_log_totali": righe_log_totali, "righe_log_pulibili": righe_log_pulibili,
            "eventi_totali": db.query(Evento).count(),
            "evento_piu_vecchio": db.query(func.min(Evento.timestamp)).scalar(),
            "retention_eventi": impostazioni.leggi_int(db, "retention_eventi_giorni"),
            "retention_log": impostazioni.leggi_int(db, "retention_log_giorni"),
            "lingua_di_sistema": lingua_service.di_sistema(),
            "retention_minimo": manutenzione.MIN_GIORNI,
            "api_accounts": db.query(ApiAccount).order_by(ApiAccount.nome).all(),
            "api_nuova": request.session.pop("api_nuova", None),
        },
    )


# ---------- API in ingresso: account ----------

@router.post("/sistema/api/account")
def crea_account_api(
    request: Request, nome: str = Form(...), username: str = Form(...),
    db: Session = Depends(get_db),
):
    """Crea un account API. La password si genera qui e si mostra UNA volta sola (poi c'e' solo l'hash)."""
    user = require_admin(request, db)
    nome, username = nome.strip(), username.strip()
    if not nome or not username or not all(c.isalnum() or c in "._-" for c in username):
        return RedirectResponse("/sistema?errore=api_dati#api", status_code=303)
    if db.query(ApiAccount).filter(ApiAccount.username == username).first():
        return RedirectResponse("/sistema?errore=api_duplicato#api", status_code=303)
    acc, password = api_accesso.crea_account(db, nome, username)
    db.commit()
    registra_log(db, t("Account API creato da %(username)s: %(username2)s", username=user.username, username2=username), categoria="sistema", codice="API_ACCOUNT")
    request.session["api_nuova"] = {"username": username, "password": password}
    return RedirectResponse("/sistema#api", status_code=303)


@router.post("/sistema/api/account/{account_id}/password")
def nuova_password_api(request: Request, account_id: int, db: Session = Depends(get_db)):
    """Nuova password (mostrata una volta) e revoca di tutti i token dell'account."""
    user = require_admin(request, db)
    acc = db.get(ApiAccount, account_id)
    if acc is None:
        return RedirectResponse("/sistema#api", status_code=303)
    password = api_accesso.nuova_password(db, acc)
    db.commit()
    registra_log(db, t("Nuova password dell'account API %(username)s (da %(username2)s); token revocati", username=acc.username, username2=user.username), categoria="sistema", codice="API_ACCOUNT")
    request.session["api_nuova"] = {"username": acc.username, "password": password}
    return RedirectResponse("/sistema#api", status_code=303)


@router.post("/sistema/api/account/{account_id}/attivo")
def attiva_account_api(request: Request, account_id: int, db: Session = Depends(get_db)):
    """Revoca/riattiva: un account disattivato non puo' fare login e i suoi token smettono subito di valere."""
    user = require_admin(request, db)
    acc = db.get(ApiAccount, account_id)
    if acc:
        acc.attivo = not acc.attivo
        db.commit()
        registra_log(db, t("Account API %(username)s %(p)s da %(username2)s", username=acc.username, p='riattivato' if acc.attivo else 'revocato', username2=user.username), categoria="sistema", codice="API_ACCOUNT")
    return RedirectResponse("/sistema#api", status_code=303)


@router.post("/sistema/api/account/{account_id}/elimina")
def elimina_account_api(request: Request, account_id: int, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    acc = db.get(ApiAccount, account_id)
    if acc:
        nome = acc.username
        db.delete(acc)
        db.commit()
        registra_log(db, t("Account API %(nome)s eliminato da %(username)s", nome=nome, username=user.username), categoria="sistema", codice="API_ACCOUNT")
    return RedirectResponse("/sistema#api", status_code=303)


@router.get("/sistema/manuale")
def scarica_manuale(request: Request, db: Session = Depends(get_db)):
    """Manuale d'uso (PDF) nella lingua dell'utente: per tutti gli utenti, non solo gli admin.
    Generato da backend/docs/manuale/genera_manuale.py."""
    require_login(request, db)
    from pathlib import Path
    lingua = lingua_service.lingua_corrente()
    lingua = lingua if lingua in ("it", "en") else "en"
    pdf = (Path(__file__).resolve().parent.parent / "docs" / f"limen-manuale-{lingua}.pdf").read_bytes()
    nome = "limen-manuale-it.pdf" if lingua == "it" else "limen-user-manual-en.pdf"
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@router.get("/sistema/api/documentazione")
def documentazione_api(request: Request, db: Session = Depends(get_db)):
    """Documentazione dell'API in inglese (PDF, generato da backend/docs/api_docs.html con genera_pdf.py)."""
    require_admin(request, db)
    from pathlib import Path
    pdf = (Path(__file__).resolve().parent.parent / "docs" / "limen-api.pdf").read_bytes()
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="limen-api.pdf"'})


@router.get("/sistema/backup")
def scarica_backup(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    nome_file, contenuto = backup_service.crea_backup_zip()
    registra_log(db, t("Backup del database scaricato da %(username)s", username=user.username), categoria="sistema")
    return Response(
        content=contenuto,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nome_file}"'},
    )


def _riavvia_dopo_risposta():
    # Piccolo margine per lasciare che la risposta HTTP (il redirect) arrivi
    # al browser prima che il processo termini: altrimenti la connessione
    # cadrebbe a metà. os._exit() e non sys.exit(): serve l'uscita immediata
    # del processo, non solo del thread corrente, e senza eseguire gli
    # handler di shutdown di FastAPI (che richiuderebbero comunque tutto,
    # ma più lentamente — qui vogliamo che systemd lo rialzi il prima possibile).
    time_module.sleep(0.6)
    os._exit(0)


@router.post("/sistema/riavvia")
def riavvia_app(request: Request, db: Session = Depends(get_db)):
    """
    Termina il processo: funziona come riavvio solo se qualcosa lo rialza
    da solo (il servizio systemd del progetto ha Restart=always — vedi
    deploy/limen.service). Lanciato a mano in un terminale
    (uvicorn main:app ...), il processo si chiude e basta: va fatto notare
    chiaramente in pagina, non solo qui.
    """
    user = require_admin(request, db)
    registra_log(db, t("Riavvio dell'app richiesto da %(username)s", username=user.username), livello="warning", categoria="sistema")
    threading.Thread(target=_riavvia_dopo_risposta, daemon=True).start()
    return RedirectResponse("/sistema?riavviato=1", status_code=303)


@router.post("/sistema/log/pulizia")
def pulisci_log(request: Request, giorni: int = Form(90), db: Session = Depends(get_db)):
    """
    Elimina solo le righe di LogSistema di livello 'info' più vecchie della
    soglia scelta: warning/error non vengono mai toccati da qui (restano per
    sempre, a meno di un intervento manuale sul database), e la tabella
    Evento (i passaggi/transazioni ai varchi) non è nemmeno nella query:
    la pulizia riguarda solo il rumore di routine dell'audit log, non la
    storia di chi ha avuto accesso a cosa.
    """
    user = require_admin(request, db)
    giorni = max(giorni, 7)  # soglia minima di sicurezza: non azzerare tutto per errore
    cutoff = datetime.utcnow() - timedelta(days=giorni)
    eliminate = (
        db.query(LogSistema)
        .filter(LogSistema.livello == LivelloLog.info, LogSistema.timestamp < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    registra_log(
        db, t("Pulizia log: %(eliminate)s righe di livello 'info' più vecchie di %(giorni)s giorni eliminate da %(username)s", eliminate=eliminate, giorni=giorni, username=user.username),
        livello="warning", categoria="sistema",
    )
    return RedirectResponse("/sistema?pulizia=ok", status_code=303)


@router.post("/sistema/lingua")
def salva_lingua_di_sistema(request: Request, lingua: str = Form("it"), db: Session = Depends(get_db)):
    """Lingua per chi non ne ha scelta una (ognuno puo' sceglierla per se' dal menu in basso a sinistra)."""
    user = require_admin(request, db)
    codice = lingua_service.valida(lingua) or lingua_service.DEFAULT
    impostazioni.scrivi(db, "lingua_di_sistema", codice)
    db.commit()
    lingua_service.imposta_di_sistema(codice)
    registra_log(db, t("Lingua predefinita impostata da %(username)s: %(codice)s", username=user.username, codice=codice), categoria="sistema", codice="OP_SISTEMA")
    return RedirectResponse("/sistema?pannello=lingua&lingua=salvata", status_code=303)


@router.post("/sistema/conservazione")
def salva_conservazione(
    request: Request,
    retention_eventi_giorni: str = Form("0"),
    retention_log_giorni: str = Form("0"),
    applica_ora: str = Form(""),
    db: Session = Depends(get_db),
):
    """Periodo di conservazione di passaggi e log (0 = per sempre). Con la retention
    attiva i dati piu' vecchi vengono eliminati ogni 6 ore (o subito con "applica ora")."""
    user = require_admin(request, db)

    def giorni(testo: str) -> int:
        try:
            n = int((testo or "0").strip())
        except ValueError:
            return 0
        return 0 if n <= 0 else max(n, manutenzione.MIN_GIORNI)

    ev, lg = giorni(retention_eventi_giorni), giorni(retention_log_giorni)
    impostazioni.scrivi(db, "retention_eventi_giorni", ev)
    impostazioni.scrivi(db, "retention_log_giorni", lg)
    db.commit()
    registra_log(
        db,
        t("Conservazione dati impostata da %(username)s: passaggi %(ev)s, log %(lg)s", username=user.username,
          ev=t("%(n)s giorni", n=ev) if ev else t("per sempre"), lg=t("%(n)s giorni", n=lg) if lg else t("per sempre")),
        livello="warning", categoria="sistema",
    )
    esito = "salvata"
    if applica_ora:
        r = manutenzione.esegui(db)
        esito = f"applicata&eliminati={r['eventi'] + r['log']}"
    return RedirectResponse(f"/sistema?conservazione={esito}", status_code=303)


@router.post("/sistema/password")
def cambia_password_propria(
    request: Request,
    password_attuale: str = Form(...),
    nuova_password: str = Form(...),
    conferma_password: str = Form(None),
    db: Session = Depends(get_db),
):
    """Chiunque sia loggato cambia la PROPRIA password (serve quella attuale):
    diverso dal reset in 'Gestione utenti', dove un admin ne imposta una
    nuova per un altro utente senza conoscere quella vecchia."""
    user = require_login(request, db)
    if not verify_password(password_attuale, user.password_hash):
        registra_log(db, t("Cambio password: password attuale errata per %(username)s", username=user.username), livello="warning", categoria="auth")
        return RedirectResponse("/sistema?password=errata", status_code=303)
    if len(nuova_password) < 8:
        return RedirectResponse("/sistema?password=corta", status_code=303)
    if conferma_password is not None and conferma_password != nuova_password:
        return RedirectResponse("/sistema?password=diversa", status_code=303)
    user.password_hash = hash_password(nuova_password)
    user.password_predefinita = False
    db.commit()
    registra_log(db, t("Password cambiata da %(username)s", username=user.username), categoria="auth")
    return RedirectResponse("/sistema?password=ok", status_code=303)


@router.post("/sistema/utenti")
def crea_utente(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    is_admin: str = Form(""),
    conferma_password: str = Form(None),
    db: Session = Depends(get_db),
):
    admin = require_admin(request, db)
    username = username.strip()
    if not username:
        return RedirectResponse("/sistema?errore=username_vuoto", status_code=303)
    if len(password) < 8:
        return RedirectResponse("/sistema?errore=password_corta", status_code=303)
    if conferma_password is not None and conferma_password != password:
        return RedirectResponse("/sistema?errore=password_diversa", status_code=303)
    nuovo = User(username=username, password_hash=hash_password(password), is_admin=bool(is_admin))
    db.add(nuovo)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return RedirectResponse("/sistema?errore=utente_duplicato", status_code=303)
    registra_log(
        db, t("Utente '%(username)s' creato da %(username2)s (%(p)s)", username=username, username2=admin.username, p=t('amministratore') if nuovo.is_admin else t('operatore')),
        categoria="utenti",
    )
    return RedirectResponse("/sistema?utente=creato", status_code=303)


@router.post("/sistema/utenti/{user_id}/password")
def reimposta_password_utente(request: Request, user_id: int, nuova_password: str = Form(...), conferma_password: str = Form(None), db: Session = Depends(get_db)):
    """Reset amministrativo: l'admin imposta una password nuova per un altro
    utente senza dover conoscere quella vecchia (utile se l'ha persa)."""
    admin = require_admin(request, db)
    if len(nuova_password) < 8:
        return RedirectResponse("/sistema?errore=password_corta", status_code=303)
    if conferma_password is not None and conferma_password != nuova_password:
        return RedirectResponse("/sistema?errore=password_diversa", status_code=303)
    target = db.query(User).filter(User.id == user_id).first()
    if target:
        target.password_hash = hash_password(nuova_password)
        target.password_predefinita = False
        db.commit()
        registra_log(db, t("Password di '%(username)s' reimpostata da %(username2)s", username=target.username, username2=admin.username), livello="warning", categoria="utenti")
    return RedirectResponse("/sistema?utente=password_reimpostata", status_code=303)


@router.post("/sistema/utenti/{user_id}/ruolo")
def cambia_ruolo_utente(request: Request, user_id: int, db: Session = Depends(get_db)):
    """Promuove/retrocede un utente. Bloccato se l'operazione lascerebbe il
    sistema senza nemmeno un amministratore: a quel punto nessuno potrebbe
    più tornare in questa pagina per correggere l'errore."""
    admin = require_admin(request, db)
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return RedirectResponse("/sistema", status_code=303)

    numero_admin = db.query(User).filter(User.is_admin == True).count()  # noqa: E712
    if target.is_admin and numero_admin <= 1:
        registra_log(db, t("Retrocessione di '%(username)s' bloccata: è l'unico amministratore rimasto", username=target.username), livello="warning", categoria="utenti")
        return RedirectResponse("/sistema?errore=ultimo_admin", status_code=303)

    target.is_admin = not target.is_admin
    db.commit()
    registra_log(
        db, t("Ruolo di '%(username)s' cambiato in %(p)s da %(username2)s", username=target.username, p=t('amministratore') if target.is_admin else t('operatore'), username2=admin.username),
        livello="warning", categoria="utenti",
    )
    return RedirectResponse("/sistema?utente=ruolo_cambiato", status_code=303)


@router.post("/sistema/utenti/{user_id}/delete")
def elimina_utente(request: Request, user_id: int, db: Session = Depends(get_db)):
    """Bloccato per sé stessi (eviterebbe un auto-lockout senza nemmeno un
    conferma) e per l'ultimo amministratore rimasto, stesso motivo del
    cambio ruolo sopra."""
    admin = require_admin(request, db)
    if user_id == admin.id:
        return RedirectResponse("/sistema?errore=non_puoi_eliminarti", status_code=303)

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        return RedirectResponse("/sistema", status_code=303)

    numero_admin = db.query(User).filter(User.is_admin == True).count()  # noqa: E712
    if target.is_admin and numero_admin <= 1:
        return RedirectResponse("/sistema?errore=ultimo_admin", status_code=303)

    nome = target.username
    db.delete(target)
    db.commit()
    registra_log(db, t("Utente '%(nome)s' eliminato da %(username)s", nome=nome, username=admin.username), livello="warning", categoria="utenti")
    return RedirectResponse("/sistema?utente=eliminato", status_code=303)
