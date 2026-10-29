# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Entry point della webapp di controllo accessi.
Avvio: uvicorn main:app --host 0.0.0.0 --port 8000
"""
import os
import secrets
import logging
from dotenv import load_dotenv

# Deve girare PRIMA di importare database/models: database.py legge
# DATABASE_PATH dall'ambiente al momento dell'import.
load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import inspect, text

from database import Base, engine
import models  # noqa: F401  (serve per registrare i modelli su Base prima del create_all)
from routers import auth_router, admin, registro, badge, permessi, stato, festivita, modalita, presenze, api
from services import udp_agent, manutenzione, modifiche  # noqa: F401  (registra i listener di sessione)
from services import impostazioni, lingua
from services.lingua import LinguaMiddleware

logger = logging.getLogger("uvicorn.error")


# Colonne aggiunte dopo il primo deploy: {tabella: {colonna: tipo SQL}}.
# create_all() crea le tabelle mancanti ma non altera quelle già esistenti:
# su un database creato con una versione precedente del modello, una nuova
# colonna non comparirebbe mai da sola. Senza Alembic (vedi README, "Prossimi
# passi"), basta un controllo manuale con ALTER TABLE ... ADD COLUMN — SQLite
# lo supporta bene per colonne nullable o con un DEFAULT, come queste.
COLONNE_AGGIUNTE = {
    "users": {"lingua": "VARCHAR(5)", "password_predefinita": "BOOLEAN NOT NULL DEFAULT 0"},
    "varchi": {
        "ultima_cache_versione": "INTEGER",
        "ultima_cache_confermata": "INTEGER",
        # DEFAULT 1 = attivo: i varchi già esistenti restano attivi dopo la
        # migrazione, nessuno si disattiva da solo con l'aggiornamento.
        "attivo": "BOOLEAN NOT NULL DEFAULT 1",
        "ultimo_evento_seq": "BIGINT",
        "anti_passback_timeout_min": "INTEGER",
        "modalita": "VARCHAR(20) NOT NULL DEFAULT 'normale'",
        "sblocco_fascia_id": "INTEGER",
        "porta_stato": "VARCHAR(20)",
        "porta_stato_at": "DATETIME",
        "conta_presenze": "BOOLEAN NOT NULL DEFAULT 0",
        "funzionamento": "TEXT",
        "uscite_stato": "TEXT",
        "abbinamento_ip": "VARCHAR(45)",
        "abbinamento_nonce": "VARCHAR(32)",
        "abbinamento_at": "DATETIME",
    },
    "persone": {
        "ospite": "BOOLEAN NOT NULL DEFAULT 0",
        "valido_da": "DATETIME",
        "valido_a": "DATETIME",
        "anti_passback": "BOOLEAN NOT NULL DEFAULT 0",
        "passback_varco_id": "INTEGER",
        "presenza_stato": "VARCHAR(10)",
        "presenza_aggiornato_at": "DATETIME",
    },
    "policies": {
        "sempre": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "fasce_orarie": {
        "escludi_festivi": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "log_sistema": {
        "varco_id": "INTEGER",
        "codice": "VARCHAR(40)",
        "utente": "VARCHAR(80)",
        "origine_ip": "VARCHAR(45)",
        "dettagli": "TEXT",
    },
    "eventi": {
        "gestito_da": "VARCHAR(64)",
        "gestito_at": "DATETIME",
        "dettaglio": "VARCHAR(120)",    # es. "Lettore 2", nome dell'ingresso/uscita
        "varco_nome": "VARCHAR(100)",   # nome del varco eliminato: i suoi passaggi restano nel registro
    },
}


def _migra_schema():
    ispettore = inspect(engine)
    tabelle = set(ispettore.get_table_names())
    with engine.begin() as conn:
        for tabella, colonne in COLONNE_AGGIUNTE.items():
            if tabella not in tabelle:
                continue  # tabella creata ora da create_all, già con tutte le colonne
            esistenti = {c["name"] for c in ispettore.get_columns(tabella)}
            for nome, tipo_sql in colonne.items():
                if nome not in esistenti:
                    conn.execute(text(f"ALTER TABLE {tabella} ADD COLUMN {nome} {tipo_sql}"))
                    logger.info(f"Migrazione schema: aggiunta colonna {tabella}.{nome}")


def _migra_policy_multi_varco():
    """Una policy ora puo' avere piu' varchi (tabella policy_varchi) invece di uno solo (policies.varco_id).
    Sui database esistenti copia i collegamenti e ricostruisce `policies` senza la vecchia colonna (NOT NULL, quindi
    non si potrebbe piu' creare una policy). Idempotente: se la colonna non c'e' piu' non fa nulla.
    Si crea la tabella nuova e poi si rinomina (non il contrario): rinominare `policies` farebbe puntare le chiavi
    esterne di persona_policy/gruppo_policy/policy_varchi alla tabella vecchia, che poi viene eliminata."""
    _ripara_riferimenti_policies_vecchia()
    ispettore = inspect(engine)
    if "policies" not in ispettore.get_table_names():
        return
    colonne = {c["name"] for c in ispettore.get_columns("policies")}
    if "varco_id" not in colonne:
        return
    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO policy_varchi (policy_id, varco_id) SELECT id, varco_id FROM policies WHERE varco_id IS NOT NULL"))
        conn.execute(text(
            "CREATE TABLE policies_nuova (id INTEGER NOT NULL PRIMARY KEY, nome VARCHAR(100), "
            "fascia_oraria_id INTEGER REFERENCES fasce_orarie (id), attiva BOOLEAN, sempre BOOLEAN NOT NULL DEFAULT 0)"))
        conn.execute(text("INSERT INTO policies_nuova (id, nome, fascia_oraria_id, attiva) SELECT id, nome, fascia_oraria_id, attiva FROM policies"))
        conn.execute(text("DROP TABLE policies"))
        conn.execute(text("ALTER TABLE policies_nuova RENAME TO policies"))
    logger.info("Migrazione schema: policy con piu' varchi (policies.varco_id -> policy_varchi)")


def _ripara_riferimenti_policies_vecchia():
    """Una prima versione della migrazione sopra lasciava le chiavi esterne verso la tabella eliminata
    "policies_vecchia": ricostruisce quelle tabelle con il riferimento giusto a `policies`."""
    with engine.begin() as conn:
        rotte = conn.execute(text(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND sql LIKE '%policies_vecchia%' AND name != 'policies_vecchia'")).all()
        for nome, sql in rotte:
            nuova = f"{nome}_riparata"
            sql_nuovo = sql.replace('"policies_vecchia"', "policies").replace("policies_vecchia", "policies")
            conn.execute(text(sql_nuovo.replace(nome, nuova, 1)))
            conn.execute(text(f"INSERT INTO {nuova} SELECT * FROM {nome}"))
            conn.execute(text(f"DROP TABLE {nome}"))
            conn.execute(text(f"ALTER TABLE {nuova} RENAME TO {nome}"))
            logger.info(f"Migrazione schema: {nome} ora punta di nuovo a policies")


# Se SESSION_SECRET non è impostato in .env, ne genera uno temporaneo e
# avvisa: funziona, ma ogni riavvio invalida le sessioni attive (tutti
# disconnessi). Per un deploy stabile, imposta SESSION_SECRET in .env.
SESSION_SECRET = os.environ.get("SESSION_SECRET")
if not SESSION_SECRET and os.environ.get("LIMEN_DATA"):
    # Docker: la chiave sta nella cartella dei dati, creata una volta sola, cosi' i riavvii non scollegano nessuno.
    _file_chiave = os.path.join(os.environ["LIMEN_DATA"], "session_secret")
    try:
        with open(_file_chiave) as _f:
            SESSION_SECRET = _f.read().strip()
        if not SESSION_SECRET:
            raise OSError
    except OSError:
        SESSION_SECRET = secrets.token_hex(32)
        os.makedirs(os.environ["LIMEN_DATA"], exist_ok=True)
        _fd = os.open(_file_chiave, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(_fd, "w") as _f:
            _f.write(SESSION_SECRET)
    os.environ["SESSION_SECRET"] = SESSION_SECRET
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_hex(32)
    logger.warning(
        "SESSION_SECRET non impostato in .env: generato un valore temporaneo. "
        "Le sessioni verranno invalidate ad ogni riavvio del server. "
        "Copia backend/.env.example in backend/.env e imposta un valore fisso."
    )

app = FastAPI(title="Limen", docs_url=None, redoc_url=None, openapi_url=None)   # nessuna documentazione pubblica: si scarica da Proprietà di sistema
# Con HTTPS (backend/avvia.sh imposta LIMEN_HTTPS=1) il cookie di sessione viaggia solo su HTTPS.
app.add_middleware(LinguaMiddleware)   # dentro SessionMiddleware: legge la lingua dalla sessione
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, https_only=os.environ.get("LIMEN_HTTPS") == "1")

# Crea le tabelle se non esistono già (per iniziare; poi si passa ad Alembic se serve)
Base.metadata.create_all(bind=engine)
_migra_schema()
_migra_policy_multi_varco()


def _migra_festivi_nella_griglia():
    """Prima ogni fascia aveva la spunta «non vale nei festivi». Ora i festivi sono una riga della griglia (giorno 7):
    nei festivi vale quella, se e' vuota la fascia non vale. Una sola volta: alle fasce che valevano anche nei
    festivi con lo stesso orario tutti i giorni attivi copia quell'orario nella riga Festivi; le altre restano con
    la riga vuota (piu' restrittivo, da completare a mano)."""
    from database import SessionLocal
    from models import FasciaOraria, FasciaOrariaRiga, Impostazione
    db = SessionLocal()
    try:
        if db.query(Impostazione).filter(Impostazione.chiave == "migrazione_festivi_griglia").first():
            return
        for f in db.query(FasciaOraria).filter(FasciaOraria.escludi_festivi == False).all():  # noqa: E712
            per = {}
            for r in f.righe:
                if r.giorno_settimana < 7:
                    per.setdefault(r.giorno_settimana, []).append((r.ora_inizio, r.ora_fine))
            if not per or any(r.giorno_settimana == 7 for r in f.righe):
                continue
            insiemi = {tuple(sorted(v)) for v in per.values()}
            if len(insiemi) == 1:
                for ini, fine in next(iter(insiemi)):
                    db.add(FasciaOrariaRiga(fascia_id=f.id, giorno_settimana=7, ora_inizio=ini, ora_fine=fine))
        db.add(Impostazione(chiave="migrazione_festivi_griglia", valore="1"))
        db.commit()
    finally:
        db.close()


_migra_festivi_nella_griglia()


def _crea_admin_iniziale():
    """Primo avvio con nessun utente: crea admin/admin. La password e' segnata come predefinita e la webapp
    mostra un avviso finche' non la si cambia (Proprietà di sistema)."""
    from database import SessionLocal
    from auth import hash_password
    from models import User
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            db.add(User(username="admin", password_hash=hash_password("admin"), is_admin=True, password_predefinita=True))
            db.commit()
            logger.warning("Primo avvio: creato l'utente admin con password «admin». Cambiala dal primo accesso.")
    finally:
        db.close()


_crea_admin_iniziale()


def _migra_regole_sempre():
    """Una sola volta: le regole che avevano «nessuna fascia» (= sempre valide) diventano «Sempre» esplicito. Da qui in
    poi nessuna fascia = regola incompleta, che non concede niente."""
    from database import SessionLocal
    from models import Impostazione, Policy
    db = SessionLocal()
    try:
        if db.query(Impostazione).filter(Impostazione.chiave == "migrazione_regole_sempre").first():
            return
        for p in db.query(Policy).filter(Policy.fascia_oraria_id.is_(None)).all():
            p.sempre = True
        db.add(Impostazione(chiave="migrazione_regole_sempre", valore="1"))
        db.commit()
    finally:
        db.close()


_migra_regole_sempre()


def _migra_anti_passback_persone():
    """Una sola volta: l'anti-passback ora va attivato anche sulle singole persone. Per non cambiare il comportamento
    di chi lo usava gia', se almeno un varco lo ha attivo lo si accende a tutte le persone esistenti."""
    from database import SessionLocal
    from models import Impostazione, Persona, Varco
    db = SessionLocal()
    try:
        if db.query(Impostazione).filter(Impostazione.chiave == "migrazione_ap_persone").first():
            return
        if db.query(Varco).filter(Varco.anti_passback_enabled == True).count():  # noqa: E712
            db.query(Persona).update({Persona.anti_passback: True}, synchronize_session=False)
        db.add(Impostazione(chiave="migrazione_ap_persone", valore="1"))
        db.commit()
    finally:
        db.close()


_migra_anti_passback_persone()

# htmx e font locali (vedi templates/base.html). Percorso assoluto: non dipende
# dalla cartella da cui parte uvicorn.
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")), name="static")

app.include_router(auth_router.router)
app.include_router(admin.router)
app.include_router(registro.router)
app.include_router(presenze.router)
app.include_router(badge.router)
app.include_router(permessi.router)
app.include_router(stato.router)
app.include_router(festivita.router)
app.include_router(modalita.router)
app.include_router(api.router)


@app.on_event("startup")
def _avvia_canale_udp():
    # Canale UDP per gli slave leggeri (microcontrollore/piccola SBC senza
    # Python): ricezione report/heartbeat + invio periodico della cache e tocco dei varchi silenziosi
    # di autorizzazioni. Vedi services/udp_agent.py per i dettagli: e' l'unico
    # canale con i varchi (le vecchie API HTTP/websocket sono state rimosse).
    from database import SessionLocal
    _db = SessionLocal()
    try:
        lingua.imposta_di_sistema(impostazioni.leggi(_db, "lingua_di_sistema"))
    finally:
        _db.close()
    udp_agent.avvia()
    manutenzione.avvia()   # pulizia periodica secondo la conservazione dati impostata


@app.on_event("shutdown")
def _arresta_canale_udp():
    udp_agent.arresta()
    manutenzione.arresta()
