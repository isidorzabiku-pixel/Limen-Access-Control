# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Canale UDP leggero per gli slave a microcontrollore (o piccola SBC) che non
fanno girare l'agent Python: niente HTTP, niente websocket, solo datagrammi
UDP firmati con l'api_key del varco. Pensato per restare valido anche su un
ESP32 (HMAC-SHA256 è supportato in hardware su quasi tutti i microcontrollori
moderni, nessuna libreria pesante necessaria).

Tutto e' unicast: il master ha un IP fisso e ogni varco ha l'IP che l'admin scrive nella
pagina Varchi; non esiste nessun broadcast.

  tocco (master -> UN slave, unicast): HI|varco|ts|firma
    Lo slave non scrive mai per primo: e' il master a "toccarlo" all'IP del varco, ogni 3 s
    finche' il varco e' silenzioso (poi ogni 15 s). Dal tocco lo slave impara l'IP del master
    (con la chiave: solo da un tocco firmato) e risponde: senza chiave con una PR (richiesta di
    abbinamento), con la chiave con un battito. Un tocco con firma non valida (varco ricreato,
    chiave reimpostata) fa ripresentare lo slave, se arriva dal master che conosce.

  cache (master -> UN slave, unicast)
    Un pacchetto JSON per varco con l'elenco badge+fasce orarie autorizzati
    su quel varco, firmato con l'api_key di quel varco, all'IP del varco. Mandato
    ogni INTERVALLO_CACHE_SEC e a richiesta (bottone "Scarica dati" sulla webapp).
    Un varco senza IP non la riceve.

  apri (master -> UN slave, unicast)
    Comando di apertura remota, mandato all'IP configurato per quel varco
    (campo Varco.ip_address, impostato a mano dall'admin: niente discovery
    automatico, per determinismo/sicurezza).

  heartbeat / evento / esito-apertura / cache-ack / richiesta-cache (slave -> master, unicast)
    Lo slave manda all'IP del master, imparato dal tocco, e il
    master accetta il pacchetto SOLO se il mittente è l'IP configurato per
    quel varco_id — altrimenti lo scarta, chiunque esso sia. "CA" (cache-ack)
    è la conferma di consegna della cache: lo slave la manda subito dopo aver
    verificato ed elaborato con successo un pacchetto cache, riportando la
    stessa "versione" ricevuta. Il master la confronta con l'ultima versione
    mandata a quel varco per sapere se è stata davvero recapitata (il bottone
    "Scarica dati" aspetta fino a 2s questa conferma prima di rispondere).

  Gli eventi (EV: passaggio badge) arrivano con un numero progressivo e l'ora
  in cui sono avvenuti ("EV|varco|ts|codice|esito|seq|ts_evento"). Il master li
  registra con quell'ora e risponde in unicast con "AE|varco|ts|seq" (conferma
  di ricezione): lo slave li tiene in coda su disco finche' non arriva la
  conferma, quindi un master spento o un pacchetto perso non fanno perdere
  nulla. Se la conferma si perde lo slave ritrasmette: lo stesso numero visto
  due volte e' un duplicato, che viene confermato ma non registrato di nuovo.
  I pacchetti EV senza numero (slave vecchi) restano accettati come prima.

  Funzionamento del varco (lettori, uscite, ingressi): la configurazione (services/
  funzionamento.py) viaggia nella cache come campo "fn"; senza, lo slave si comporta
  come prima. Pacchetti aggiunti:
    EV   in coda ai campi di sempre porta il numero del lettore (0 = primo):
         "EV|varco|ts|codice|esito|seq|ts_evento|lettore" (un master vecchio lo ignora)
    IN   (slave -> master, con coda e conferma come EV) un ingresso e' scattato:
         "IN|varco|ts|indice|1|seq|ts_evento" (1 = attivo; il rilascio non si registra)
    SU   (slave -> master, con coda e conferma) stato di un'uscita acceso/spento:
         "SU|varco|ts|indice|0/1|seq|ts_evento"
    UC   (master -> UN slave, unicast, come AP) comando a un'uscita dal pannello:
         "UC|varco|ts|indice|i" impulso, "1" accendi, "0" spegni

Formato dei pacchetti piccoli (tutto tranne la cache): testo semplice
"TIPO|VARCO_ID|TS|campo1|campo2|...|HMAC", HMAC-SHA256 troncato a 16 byte (32
caratteri hex) calcolato sulla parte a sinistra dell'ultimo "|", con chiave
l'api_key del varco. TS è un timestamp unix: i pacchetti più vecchi di
FINESTRA_REPLAY_SEC vengono scartati (mitigazione replay — non perfetta, ma
adeguata alla minaccia reale: non vogliamo che un pacchetto "apri" catturato
in chiaro sulla rete possa essere rispedito più tardi per riaprire il varco).

La cache usa invece un JSON per la struttura (più comodo per un elenco di
badge), con la stessa idea: payload_json + "\\n" + hmac_hex(payload_json).
"""
import re
import hmac
import time
import json
import socket
import logging
import threading
import hashlib
import calendar
from datetime import datetime, timedelta
from typing import Optional

from database import SessionLocal
from models import Varco, Persona, AccessMedia, Policy, EsitoEvento, Evento, LogSistema
from services.policy_engine import aggiorna_posizione_anti_passback, blocco_ingresso_fino, violazione_anti_passback
from services.logger import registra_log
from services import funzionamento, presenza, tempo, festivita, connessioni, motivi
from services.lingua import t

logger = logging.getLogger("udp_agent")

PORTA = 57990                     # porta alta (range dinamico/privato 49152-65535), per evitare conflitti
PORTA_SLAVE = PORTA               # dove ascoltano gli slave: uguale a PORTA, cambia solo nelle prove con master e slave sullo stesso PC
FINESTRA_REPLAY_SEC = 30          # per apri / evento / heartbeat (azioni "vive")
INTERVALLO_CACHE_SEC = 6 * 3600   # invio automatico della cache, ogni 6 ore
LUNGHEZZA_FIRMA = 32              # hex chars = 16 byte di HMAC-SHA256 troncato

_stop = threading.Event()
_thread_ricezione: Optional[threading.Thread] = None
_thread_cache: Optional[threading.Thread] = None

# Socket unico per l'invio (tocco, apri, cache, abbinamento): creato una volta, non ad ogni invio.
_sock_invio: Optional[socket.socket] = None


# ---------- Firma ----------

def _firma(payload: bytes, api_key: str) -> str:
    return hmac.new(api_key.encode(), payload, hashlib.sha256).hexdigest()[:LUNGHEZZA_FIRMA]


def _verifica(payload: bytes, api_key: str, firma_ricevuta: str) -> bool:
    attesa = _firma(payload, api_key)
    return hmac.compare_digest(attesa, firma_ricevuta)


def _impacchetta_piccolo(campi: list) -> bytes:
    """campi = [tipo, varco_id, ts, ...] SENZA la firma: quella la aggiunge il chiamante dopo averla calcolata."""
    return "|".join(str(c) for c in campi).encode()


# ---------- Cache: formato compatto e suddivisione in pacchetti ----------

# Un datagramma UDP porta al massimo ~1472 byte senza frammentazione (MTU 1500
# meno intestazioni IP/UDP): la cache di un varco con centinaia di badge non
# ci sta in un pacchetto solo, quindi viene spezzata. Il JSON di ogni pezzo
# resta sotto questo limite (la firma, 33 byte, si aggiunge dopo).
MAX_BYTE_CACHE_PEZZO = 1350
SCADUTO_IN_CACHE_GIORNI = 7
PAUSA_TRA_PEZZI_SEC = 0.004      # evita di saturare il buffer di ricezione dello slave


def _json_compatto(obj) -> str:
    return json.dumps(obj, separators=(",", ":"))


def spezza_cache(meta: dict, voci: list[dict], max_byte: int = MAX_BYTE_CACHE_PEZZO) -> list[bytes]:
    """
    Trasforma le autorizzazioni di un varco in una lista di JSON (bytes), uno
    per datagramma. Formato:

      meta (in OGNI pezzo, cosi' ogni pezzo e' autosufficiente): tipo, varco_id,
        versione, durata_apertura_sec, attivo, modo, sblocco, ora, locale
      "parte"/"parti": indice del pezzo e numero totale (lo slave applica la
        cache solo quando li ha tutti, con la stessa versione)
      "profili": tabella delle fasce orarie {indice: [[giorno, min_inizio, min_fine(, 1)], ...]}
        (il 4o elemento 1 = la riga non vale nei giorni festivi, elencati in meta "festivi"),
        UNA volta per profilo distinto invece di ripeterla per ogni badge; ogni
        pezzo contiene solo i profili usati dai suoi badge
      "badge": [{"c": codice, "s": 1}]          badge valido sempre (H24)
               [{"c": codice, "p": indice}]     badge con fasce = profilo `indice`
               + facoltativi "d" / "e": inizio / fine di validita' del badge (unix,
                 secondi): lo slave li controlla con il proprio orologio, cosi' un
                 badge ospite scade all'ora giusta anche senza una nuova cache

    voci = [{"c": codice, "sempre": True} | {"c": codice, "f": [[g, ini, fine], ...]}]
    (il formato di _costruisci_autorizzazioni_varco).
    """
    profili: dict[tuple, int] = {}
    compatte = []
    for v in voci:
        if v.get("sempre"):
            voce = {"c": v["c"], "s": 1}
        else:
            chiave = tuple(sorted(tuple(x) for x in v.get("f", [])))
            idx = profili.setdefault(chiave, len(profili))
            voce = {"c": v["c"], "p": idx}
        for k in ("d", "e", "x"):
            if v.get(k) is not None:
                voce[k] = v[k]
        compatte.append(voce)
    profilo_json = {idx: _json_compatto([list(x) for x in chiave]) for chiave, idx in profili.items()}

    base = len(_json_compatto({**meta, "parte": 9999, "parti": 9999, "profili": {}, "badge": []}))
    pezzi: list[tuple[list[dict], set[int]]] = []
    corrente: list[dict] = []
    usati: set[int] = set()
    dimensione = base
    for voce in compatte:
        costo = len(_json_compatto(voce)) + 1
        nuovo_profilo = "p" in voce and voce["p"] not in usati
        if nuovo_profilo:
            costo += len(profilo_json[voce["p"]]) + len(str(voce["p"])) + 4
        if corrente and dimensione + costo > max_byte:
            pezzi.append((corrente, usati))
            corrente, usati, dimensione = [], set(), base
            # il profilo va (ri)contato nel nuovo pezzo
            costo = len(_json_compatto(voce)) + 1
            if "p" in voce:
                costo += len(profilo_json[voce["p"]]) + len(str(voce["p"])) + 4
            nuovo_profilo = "p" in voce
        corrente.append(voce)
        if nuovo_profilo:
            usati.add(voce["p"])
        dimensione += costo
    pezzi.append((corrente, usati))  # anche vuoto: un varco senza badge manda comunque meta (es. disattivato)

    chiavi_per_idx = {idx: chiave for chiave, idx in profili.items()}
    risultato = []
    for i, (badge, idx_usati) in enumerate(pezzi):
        corpo = {
            **meta,
            "parte": i,
            "parti": len(pezzi),
            "profili": {str(idx): [list(x) for x in chiavi_per_idx[idx]] for idx in sorted(idx_usati)},
            "badge": badge,
        }
        risultato.append(_json_compatto(corpo).encode())
    return risultato


# ---------- Invio ----------

def _socket_invio() -> socket.socket:
    global _sock_invio
    if _sock_invio is None:
        _sock_invio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    return _sock_invio


def invia_apri(varco: Varco) -> bool:
    """
    Comando di apertura remota verso l'IP configurato per questo varco.
    Ritorna False (senza inviare nulla) se il varco non ha un IP configurato:
    non e' un errore di rete ma di configurazione, il chiamante lo segnala.
    """
    if not varco.ip_address:
        return False
    corpo = _impacchetta_piccolo(["AP", varco.id, int(time.time()), varco.apertura_durata_sec])
    pacchetto = corpo + b"|" + _firma(corpo, varco.api_key).encode()
    try:
        _socket_invio().sendto(pacchetto, (varco.ip_address, PORTA_SLAVE))
        connessioni.traccia(f"TX AP a {varco.ip_address} per '{varco.nome}' (apertura {varco.apertura_durata_sec} s)", varco.id, "PKT_TX", {"tipo": "AP", "ip": varco.ip_address})
        return True
    except OSError as e:
        logger.warning(f"Invio UDP 'apri' a {varco.ip_address} fallito: {e}")
        connessioni.anomalia(("invio", "apri", varco.id), t("Invio del comando di apertura a %(ip_address)s fallito: %(e)s", ip_address=varco.ip_address, e=e), varco.id, "error", "rete")
        return False


def invia_comando_uscita(varco: Varco, indice: int, azione: str) -> bool:
    """UC: comando a un'uscita del varco dal pannello (unicast, "a perdere" come AP).
    azione: "i" impulso (per il suo tempo), "1" accendi, "0" spegni."""
    if not varco.ip_address or azione not in ("i", "1", "0"):
        return False
    corpo = _impacchetta_piccolo(["UC", varco.id, int(time.time()), indice, azione])
    pacchetto = corpo + b"|" + _firma(corpo, varco.api_key).encode()
    try:
        _socket_invio().sendto(pacchetto, (varco.ip_address, PORTA_SLAVE))
        connessioni.traccia(f"TX UC a {varco.ip_address} per '{varco.nome}' (uscita {indice}, azione {azione})", varco.id, "PKT_TX", {"tipo": "UC", "ip": varco.ip_address})
        return True
    except OSError as e:
        logger.warning(f"Invio UDP 'UC' a {varco.ip_address} fallito: {e}")
        connessioni.anomalia(("invio", "UC", varco.id), t("Invio del comando uscita a %(ip_address)s fallito: %(e)s", ip_address=varco.ip_address, e=e), varco.id, "error", "rete")
        return False


# ---------- Abbinamento ----------

# Uno slave senza chiave (o con una chiave non piu' valida) risponde al tocco del master con
# PR|varco_id|ts|nonce, non firmato. Il master non risponde da solo: mostra la richiesta all'admin e, solo
# dopo il suo "Abbina", manda PA|varco_id|ts|nonce|chiave all'IP del varco. Il nonce (scelto dallo slave) lega la
# risposta a quella richiesta; lo slave accetta la PA solo da chi lo ha appena toccato.
# Il master tocca ogni 3 s e lo slave risponde a ogni tocco: se ne mancano alcune, e' sparito o ha cambiato id.
# L'avviso resta quindi pochi secondi; l'«Abbina» tollera un po' di margine in piu'.
ABBINAMENTO_VALIDITA_SEC = 10
ABBINAMENTO_MARGINE_SEC = 30
ONLINE_ENTRO_SEC = presenza.SOGLIA_OFFLINE_SEC
_NONCE_VALIDO = re.compile(r"^[0-9a-f]{8,32}$")


def _gestisci_richiesta_abbinamento(varco_id: int, nonce: str, mittente_ip: str) -> None:
    if not _NONCE_VALIDO.match(nonce):
        return
    db = SessionLocal()
    try:
        # Lo stesso dispositivo (stesso IP) che si presenta con un altro id ha cambiato numero: la sua
        # richiesta precedente non vale piu' e non deve restare nell'elenco.
        for altro in db.query(Varco).filter(Varco.abbinamento_ip == mittente_ip, Varco.id != varco_id).all():
            altro.abbinamento_ip = altro.abbinamento_nonce = altro.abbinamento_at = None
        db.commit()
        varco = db.query(Varco).filter(Varco.id == varco_id).first()
        if varco is None:
            connessioni.anomalia(("pr-ignoto", varco_id, mittente_ip),
                                 t("Richiesta di abbinamento da %(mittente_ip)s per il varco n. %(varco_id)s, che non esiste: crealo da Varchi con questo numero oppure correggi l'id sullo slave.", mittente_ip=mittente_ip, varco_id=varco_id), None, "warning", "dispositivi",
                                 dettagli={"ip": mittente_ip, "varco_id": varco_id})
            return
        if varco.ip_address != mittente_ip:
            presenza.segna_scarto(varco_id, "ip", mittente_ip)
            connessioni.anomalia(("pr-ip", varco_id, mittente_ip),
                                 t("Richiesta di abbinamento per '%(nome)s' scartata: arriva da %(mittente_ip)s ma il varco e' registrato con IP %(p)s. Correggi l'IP del varco o quello del dispositivo (gli indirizzi sono fissi).", nome=varco.nome, mittente_ip=mittente_ip, p=varco.ip_address or t('nessuno')),
                                 varco_id, "warning", "dispositivi", dettagli={"ip": mittente_ip, "ip_registrato": varco.ip_address})
            return
        adesso = datetime.utcnow()
        # Un varco che sta parlando con una chiave valida non ha bisogno di abbinarsi: cosi' chi
        # si finge uno slave gia' collegato non puo' mettersi in coda al suo posto.
        if varco.ultimo_heartbeat and (adesso - varco.ultimo_heartbeat).total_seconds() < ONLINE_ENTRO_SEC:
            connessioni.anomalia(("pr-attivo", varco_id, mittente_ip),
                                 t("Richiesta di abbinamento da %(mittente_ip)s ignorata: il varco '%(nome)s' sta gia' comunicando con la sua chiave (se e' un secondo dispositivo con lo stesso id, cambiagli id).", mittente_ip=mittente_ip, nome=varco.nome), varco_id, "warning", "dispositivi",
                                 dettagli={"ip": mittente_ip})
            return
        if varco.abbinamento_nonce != nonce:
            connessioni.scrivi(t("Richiesta di abbinamento per il varco '%(nome)s' da %(mittente_ip)s: in attesa che un amministratore prema «Abbina».", nome=varco.nome, mittente_ip=mittente_ip),
                               "info", "dispositivi", varco_id, db, "ABB_RICHIESTA", {"ip": mittente_ip})
        varco.abbinamento_ip = mittente_ip
        varco.abbinamento_nonce = nonce
        varco.abbinamento_at = adesso
        db.commit()
    finally:
        db.close()


def dimentica_varco(varco_id: int) -> None:
    """Varco eliminato: niente limiti residui se un nuovo varco riusa il numero."""
    _ultima_rc.pop(varco_id, None)
    _ultimo_tocco.pop(varco_id, None)


def varco_con_ip(db, ip: str | None, escludi_id: int | None = None) -> Varco | None:
    """Un altro varco che ha gia' questo indirizzo IP (due dispositivi non possono avere lo stesso)."""
    if not ip:
        return None
    q = db.query(Varco).filter(Varco.ip_address == ip)
    if escludi_id is not None:
        q = q.filter(Varco.id != escludi_id)
    return q.first()


def richieste_abbinamento(db) -> list[Varco]:
    """Varchi con una richiesta di abbinamento ancora valida (la ripete ogni pochi secondi finche' vive)."""
    limite = datetime.utcnow() - timedelta(seconds=ABBINAMENTO_VALIDITA_SEC)
    return (db.query(Varco).filter(Varco.abbinamento_at.isnot(None), Varco.abbinamento_at >= limite)
            .order_by(Varco.abbinamento_at.desc()).all())


def abbina(db, varco: Varco) -> bool:
    """Conferma dell'admin: manda la chiave del varco allo slave che si e' presentato e ne
    registra l'IP (da qui in poi i suoi pacchetti firmati vengono accettati). False se non c'e'
    una richiesta valida o l'invio fallisce."""
    if not (varco.abbinamento_ip and varco.abbinamento_nonce and varco.abbinamento_at):
        return False
    if (datetime.utcnow() - varco.abbinamento_at).total_seconds() > ABBINAMENTO_MARGINE_SEC:
        return False
    corpo = _impacchetta_piccolo(["PA", varco.id, int(time.time()), varco.abbinamento_nonce, varco.api_key])
    # L'IP si registra PRIMA di mandare la chiave: lo slave comincia a parlare subito e il master
    # scarta i pacchetti da un IP che non conosce.
    ip_prima, ip_slave = varco.ip_address, varco.abbinamento_ip
    if varco_con_ip(db, ip_slave, varco.id):
        return False
    varco.ip_address = ip_slave
    db.commit()
    try:
        _socket_invio().sendto(corpo, (ip_slave, PORTA_SLAVE))
    except OSError as e:
        logger.warning(f"Invio UDP 'PA' a {ip_slave} fallito: {e}")
        connessioni.scrivi(t("Abbinamento di '%(nome)s': invio della chiave a %(ip_slave)s fallito (%(e)s). Riprova.", nome=varco.nome, ip_slave=ip_slave, e=e), "error", "dispositivi", varco.id,
                           codice="ABB_ERRORE", dettagli={"ip": ip_slave, "errore": str(e)})
        varco.ip_address = ip_prima
        db.commit()
        return False
    varco.abbinamento_ip = varco.abbinamento_nonce = varco.abbinamento_at = None
    db.commit()
    # Appena abbinato lo slave chiede subito la cache (RC): deve ricevere risposta, non finire nel limite
    # «una cache ogni 10 s» di una richiesta precedente (altrimenti aspetta 30 s il secondo tentativo).
    _ultima_rc.pop(varco.id, None)
    return True


SIGLE_MODALITA = {"normale": "n", "sblocco": "s", "manutenzione": "m", "emergenza": "e"}


def _righe_fascia(fascia) -> list[list[int]]:
    """Le righe di una fascia come le vuole lo slave: [giorno, min_inizio, min_fine(, 1)]."""
    righe = []
    for riga in fascia.righe:
        r = [riga.giorno_settimana,
             riga.ora_inizio.hour * 60 + riga.ora_inizio.minute,
             riga.ora_fine.hour * 60 + riga.ora_fine.minute]
        if riga.giorno_settimana < 7:
            r.append(1)   # le righe dei giorni normali non valgono nei festivi (la riga giorno 7 vale solo li')
        righe.append(r)
    return righe


def anti_passback_blocca(varco: Varco) -> bool:
    """Anti-passback «Blocca l'accesso» attivo su questo varco (blocca solo i lettori di ingresso)."""
    return bool(varco.anti_passback_enabled and varco.anti_passback_modalita == "hard")


def aggiorna_cache_ap(db) -> None:
    """Rimanda la cache ai varchi con anti-passback a blocco: la posizione di qualcuno e' cambiata."""
    for v in db.query(Varco).filter(Varco.anti_passback_enabled == True, Varco.anti_passback_modalita == "hard", Varco.attivo == True).all():  # noqa: E712
        if anti_passback_blocca(v):
            try:
                invia_cache(db, solo_varco_id=v.id)
            except Exception:
                logger.exception("Cache anti-passback non inviata")


def _costruisci_autorizzazioni_varco(db, varco: Varco) -> list[dict]:
    """
    Per questo varco, l'elenco dei badge attivi autorizzati con le relative
    fasce orarie (unione di tutte le policy applicabili: dirette + di gruppo),
    nel formato compatto che manda ai slave: {"c": codice, "sempre": true}
    oppure {"c": codice, "f": [[giorno, minuti_inizio, minuti_fine], ...]}.
    """
    now = datetime.utcnow()
    risultato = []

    persone = (
        db.query(Persona)
        .filter(Persona.attivo == True)  # noqa: E712
        .all()
    )
    for persona in persone:
        policy_ids = {p.id for p in persona.policy_dirette}
        for gruppo in persona.gruppi:
            policy_ids.update(p.id for p in gruppo.policies)
        if not policy_ids:
            continue

        policies = (
            db.query(Policy)
            .filter(Policy.id.in_(policy_ids), Policy.varchi.any(Varco.id == varco.id), Policy.attiva == True)  # noqa: E712
            .all()
        )
        policies = [p for p in policies if p.completa]   # una regola incompleta non concede niente
        if not policies:
            continue

        sempre = any(p.sempre for p in policies)
        fasce = []
        if not sempre:
            for p in policies:
                # 4o elemento = 1: la riga non vale nei giorni festivi
                fasce.extend(_righe_fascia(p.fascia_oraria))

        # Un badge non ancora valido va nella cache (lo slave lo abilita da solo
        # all'ora "d"); uno scaduto da poco ci resta, cosi' lo slave puo' dire
        # "scaduto" invece di "sconosciuto". Dopo SCADUTO_IN_CACHE_GIORNI esce.
        limite_scaduti = now - timedelta(days=SCADUTO_IN_CACHE_GIORNI)
        for m in persona.media:
            da_eff, a_eff = persona.finestra(m)   # vale l'intervallo piu' stretto fra badge e persona (visitatori)
            if not m.attivo or (a_eff is not None and a_eff < limite_scaduti):
                continue
            voce = {"c": m.codice}
            if sempre:
                voce["sempre"] = True
            else:
                voce["f"] = fasce
            if da_eff is not None:
                voce["d"] = calendar.timegm(da_eff.timetuple())
            if a_eff is not None:
                voce["e"] = calendar.timegm(a_eff.timetuple())
            # Anti-passback a blocco: «x» = i lettori di ingresso non aprono (l'uscita passa sempre).
            # «x» = istante unix fino a cui vale il blocco (con il timeout del varco e' la scadenza della posizione).
            fino = blocco_ingresso_fino(varco, persona, now)
            if fino:
                voce["x"] = fino
            risultato.append(voce)

    return risultato


_ultimo_invio_cache: Optional[datetime] = None


def ultimo_invio_cache() -> Optional[datetime]:
    """Quando e' partita l'ultima cache (UTC naive), per la pagina Stato."""
    return _ultimo_invio_cache


def invia_cache(db, solo_varco_id: Optional[int] = None) -> dict[int, int]:
    """
    Manda, per ogni varco, in unicast al suo IP, l'elenco badge+fasce autorizzati su
    quel varco, spezzato in piu' datagrammi se serve (vedi spezza_cache).
    Ritorna {varco_id: versione} per ogni varco a cui e' stata effettivamente
    mandata la cache (la "versione" e' il timestamp in millisecondi incluso in
    ogni pezzo, usata per riconoscere la conferma CA corrispondente — vedi
    attendi_conferme_cache()). L'invio in se' non garantisce nulla: UDP
    non ha conferma di consegna a livello di trasporto, per questo
    la conferma reale arriva solo se/quando lo slave, ricevuti TUTTI i pezzi,
    risponde con un pacchetto CA. `solo_varco_id` limita l'invio a un varco
    (usato quando lo slave chiede la cache all'avvio, pacchetto RC).
    """
    query = db.query(Varco)
    if solo_varco_id is not None:
        query = query.filter(Varco.id == solo_varco_id)
    varchi = query.all()
    sock = _socket_invio()
    mandati: dict[int, int] = {}
    for varco in varchi:
        if not varco.ip_address:
            connessioni.anomalia(("cache-senza-ip", varco.id), t("Cache per '%(nome)s' non inviata: il varco non ha un indirizzo IP (scrivilo nella pagina Varchi).", nome=varco.nome), varco.id, "warning", "dispositivi", db)
            continue
        # Varco disattivato da un amministratore: manda una cache vuota (nessun
        # badge autorizzato) PIU' il campo "attivo": false esplicito. Lo slave
        # (vedi agent/services/udp_client.py) controlla questo campo prima di
        # guardare i badge: senza, una cache vuota sarebbe indistinguibile da
        # "nessuno ha policy su questo varco" e lo slave riporterebbe ogni
        # tentativo come "credenziale sconosciuta" invece che "varco
        # disattivato" — fuorviante nei log, anche se l'accesso resta
        # comunque negato in entrambi i casi (i slave sono solo UDP: questo
        # campo è l'unico modo per dirglielo).
        autorizzazioni = _costruisci_autorizzazioni_varco(db, varco) if varco.attivo else []
        versione = int(time.time() * 1000)
        meta = {
            "tipo": "cache",
            "varco_id": varco.id,
            "versione": versione,
            "durata_apertura_sec": varco.apertura_durata_sec,
            "attivo": varco.attivo,
            # Ora del master e fuso orario locale: lo slave li usa per valutare
            # le fasce orarie (e firmare i pacchetti) anche se il suo orologio
            # o il suo fuso sono sbagliati.
            "ora": int(time.time()),
            "fuso_min": tempo.fuso_minuti(),
            # Prossimi giorni festivi ("AAAA-MM-GG"): le righe di fascia con il 4o
            # elemento a 1 non valgono in quelle date.
            "festivi": festivita.date_per_cache(db, tempo.adesso_locale().date()),
            # Modalita' del varco: "n" normale, "s" sblocco programmato (porta libera nelle
            # righe di "sblocco"), "m" manutenzione, "e" emergenza (porta libera).
            "modo": SIGLE_MODALITA.get(varco.modalita, "n"),
        }
        if varco.modalita == "sblocco" and varco.sblocco_fascia:
            meta["sblocco"] = _righe_fascia(varco.sblocco_fascia)
        # Funzionamento: solo se e' stato impostato (altrimenti lo slave fa come prima).
        fn = funzionamento.leggi(varco)
        if fn is not None:
            meta["fn"] = funzionamento.per_cache(fn)
        pezzi = spezza_cache(meta, autorizzazioni)
        try:
            for pezzo in pezzi:
                pacchetto = pezzo + b"\n" + _firma(pezzo, varco.api_key).encode()
                sock.sendto(pacchetto, (varco.ip_address, PORTA_SLAVE))
                if len(pezzi) > 1:
                    time.sleep(PAUSA_TRA_PEZZI_SEC)
            varco.ultima_cache_versione = versione
            mandati[varco.id] = versione
            connessioni.scrivi(
                t("Cache v%(versione)s inviata per '%(nome)s': %(n)s credenziali in %(pacchetti)s%(stato)s.", versione=versione, nome=varco.nome, n=len(autorizzazioni), pacchetti=t("1 pacchetto") if len(pezzi) == 1 else t("%(n)s pacchetti", n=len(pezzi)), stato='' if varco.attivo else t(' (varco DISATTIVATO: cache vuota)')), "info", "dispositivi", varco.id, db, "CACHE_INVIATA",
                {"versione": versione, "credenziali": len(autorizzazioni), "pacchetti": len(pezzi), "varco_attivo": bool(varco.attivo)})
        except OSError as e:
            logger.warning(f"Invio cache per varco_id={varco.id} a {varco.ip_address} fallito: {e}")
            connessioni.scrivi(t("Invio della cache per '%(nome)s' fallito: %(e)s. Controlla la rete del master.", nome=varco.nome, e=e), "error", "rete", varco.id, db,
                               "CACHE_ERRORE", {"errore": str(e)})
    db.commit()
    if mandati:
        global _ultimo_invio_cache
        _ultimo_invio_cache = datetime.utcnow()
    return mandati


def attendi_conferme_cache(mandati: dict[int, int], timeout_sec: float = 2.0) -> dict[int, bool]:
    """
    Aspetta fino a timeout_sec che per ogni varco in `mandati` arrivi (sul
    thread di ricezione, che gestisce il pacchetto CA e lo commit-ta sul suo
    DB) la conferma della STESSA versione appena mandata — non una vecchia,
    rimasta da un invio precedente. Usa sessioni DB nuove ad ogni giro di
    polling per vedere i commit fatti dall'altro thread (una singola sessione
    tenuta aperta non li vedrebbe).

    Un varco spento/offline resta semplicemente False allo scadere del
    timeout: non è un errore, è l'informazione che il chiamante deve
    mostrare (bottone "Scarica dati" sulla webapp).
    """
    scadenza = time.time() + timeout_sec
    confermati = {varco_id: False for varco_id in mandati}
    while time.time() < scadenza and not all(confermati.values()):
        db = SessionLocal()
        try:
            for varco_id, versione in mandati.items():
                if confermati[varco_id]:
                    continue
                v = db.query(Varco).filter(Varco.id == varco_id).first()
                if v is not None and v.ultima_cache_confermata == versione:
                    confermati[varco_id] = True
        finally:
            db.close()
        if not all(confermati.values()):
            time.sleep(0.1)
    return confermati


# ---------- Ricezione ----------

MAX_ETA_EVENTO_SEC = 7 * 24 * 3600   # un evento in coda piu' vecchio di cosi' e' considerato ora sbagliata
MAX_ANTICIPO_EVENTO_SEC = 120        # ... e uno nel futuro oltre questa tolleranza pure


def _istante_evento(ts_evento: Optional[int]) -> datetime:
    """Ora (UTC naive) a cui e' avvenuto l'evento, dichiarata dallo slave. Se manca
    o non e' plausibile (orologio dello slave sballato) si usa quella di ricezione."""
    adesso = time.time()
    if ts_evento is not None and adesso - MAX_ETA_EVENTO_SEC <= ts_evento <= adesso + MAX_ANTICIPO_EVENTO_SEC:
        return datetime.utcfromtimestamp(min(ts_evento, adesso))
    return datetime.utcnow()


def _estrai_seq(campi: list, n_base: int) -> tuple[Optional[int], Optional[int]]:
    """Dopo i `n_base` campi propri del tipo di pacchetto possono seguire
    [seq, ts_evento]. Ritorna (seq, ts_evento), None se assenti o malformati."""
    try:
        return int(campi[n_base]), int(campi[n_base + 1])
    except (IndexError, ValueError):
        return None, None


def _e_duplicato(varco: Varco, seq: Optional[int]) -> bool:
    """
    Lo slave manda un evento alla volta e passa al successivo solo dopo la
    conferma, quindi un duplicato puo' essere solo la ritrasmissione dell'ultimo
    evento gia' registrato (la conferma AE si e' persa): stesso numero.
    Un numero PIU' BASSO dell'ultimo visto significa che lo slave ha perso il suo
    stato (disco azzerato, sostituzione scheda): si riparte da li', non si scarta.
    """
    return seq is not None and varco.ultimo_evento_seq is not None and seq == varco.ultimo_evento_seq


_cache_ap_da_rimandare = threading.local()   # .v = True: dopo il commit rimandare la cache ai varchi con anti-passback a blocco


def _gestisci_evento(db, varco: Varco, campi: list) -> Optional[int]:
    """campi (senza tipo/varco_id/ts/firma): [codice, esito] oppure [codice, esito, seq, ts_evento],
    con in coda facoltativo il numero del lettore. Ritorna il numero da confermare con AE."""
    codice, esito_str = campi[0], campi[1]
    seq, ts_evento = _estrai_seq(campi, 2)
    try:
        lettore = max(0, int(campi[4]))
    except (IndexError, ValueError):
        lettore = 0
    fn = funzionamento.leggi(varco)
    try:
        esito = EsitoEvento(esito_str)
    except ValueError:
        logger.warning(f"Evento con esito ignoto dal varco_id={varco.id}: {esito_str!r}")
        connessioni.anomalia(("esito", varco.id, esito_str), t("Evento scartato dal varco '%(nome)s': esito sconosciuto %(esito_str)s (slave e master di versioni diverse?)", nome=varco.nome, esito_str=repr(esito_str)), varco.id, "warning", "rete", db)
        return seq   # lo si conferma comunque: ritrasmetterlo non lo renderebbe valido

    if _e_duplicato(varco, seq):
        return seq
    if seq is not None:
        varco.ultimo_evento_seq = seq

    istante = _istante_evento(ts_evento)
    media = db.query(AccessMedia).filter(AccessMedia.codice == codice).first()
    persona = media.persona if media else None

    # Lo slave decide in locale e non conosce la posizione anti-passback delle
    # persone: un passaggio concesso che la viola va comunque segnalato qui
    # (allarme), prima che la posizione venga aggiornata sotto.
    violazione = (
        esito == EsitoEvento.concesso and persona is not None
        and violazione_anti_passback(varco, persona, istante, lettore)
    )

    # Motivo vero del rifiuto. Lo slave sa solo cio' che c'e' nella sua cache: un badge noto ma
    # senza policy attiva su questo varco gli appare «sconosciuto». Il master conosce tutto.
    motivo = None
    if persona is not None and media is not None:
        try:
            if esito == EsitoEvento.negato_credenziale_sconosciuta:
                esito, motivo = motivi.raffina_sconosciuta(db, varco, media, istante)
            else:
                motivo = motivi.dettaglio_esito(persona, varco, media, esito)
        except Exception:
            logger.exception("Impossibile determinare il motivo del rifiuto: resta quello dello slave")
    note = [f"Lettore {lettore + 1}"] if fn is not None and len(fn["lettori"]) > 1 else []
    if motivo:
        note.append(motivo)

    db.add(Evento(
        timestamp=istante,
        varco_id=varco.id,
        media_codice=codice,
        persona_id=persona.id if persona else None,
        esito=esito,
        allarme=(esito == EsitoEvento.negato_anti_passback or violazione),
        # Con piu' lettori si registra da quale e' passato, poi il motivo del rifiuto.
        dettaglio=motivi.tronca(" · ".join(note)),
    ))

    if esito == EsitoEvento.concesso and persona is not None:
        aggiorna_posizione_anti_passback(persona, varco, istante, lettore)
        if persona.anti_passback:
            _cache_ap_da_rimandare.v = True
        # Presenze: solo dai varchi che le contano, e mai all'indietro (un evento
        # ritrasmesso in ritardo non deve sovrascrivere un passaggio piu' recente).
        if varco.conta_presenze and (persona.presenza_aggiornato_at is None or istante >= persona.presenza_aggiornato_at):
            persona.presenza_stato = "fuori" if funzionamento.direzione_lettore(varco, lettore) == "uscita" else "dentro"
            persona.presenza_aggiornato_at = istante
    return seq


STATI_PORTA = ("aperta", "chiusa", "forzata", "lasciata_aperta")


def _gestisci_sensore_porta(db, varco: Varco, campi: list) -> Optional[int]:
    """SP, campi: [stato] oppure [stato, seq, ts_evento]. Aperta/chiusa aggiornano solo lo
    stato mostrato; forzata e lasciata aperta sono allarmi e finiscono nel registro eventi.
    Ritorna il numero da confermare con AE."""
    stato = campi[0] if campi else ""
    seq, ts_evento = _estrai_seq(campi, 1)
    if stato not in STATI_PORTA:
        logger.warning(f"Stato porta ignoto dal varco_id={varco.id}: {stato!r}")
        connessioni.anomalia(("porta", varco.id, stato), t("Stato porta scartato dal varco '%(nome)s': valore sconosciuto %(stato)s", nome=varco.nome, stato=repr(stato)), varco.id, "warning", "rete", db)
        return seq
    if _e_duplicato(varco, seq):
        return seq
    if seq is not None:
        varco.ultimo_evento_seq = seq

    istante = _istante_evento(ts_evento)
    # Un evento in coda ritardato non deve sovrascrivere uno stato piu' recente.
    if varco.porta_stato_at is None or istante >= varco.porta_stato_at:
        varco.porta_stato = "aperta" if stato in ("aperta", "forzata", "lasciata_aperta") else "chiusa"
        varco.porta_stato_at = istante
    if stato in ("forzata", "lasciata_aperta"):
        db.add(Evento(
            timestamp=istante,
            varco_id=varco.id,
            media_codice=None,
            persona_id=None,
            esito=EsitoEvento.porta_forzata if stato == "forzata" else EsitoEvento.porta_lasciata_aperta,
            allarme=True,
        ))
    return seq


def _gestisci_stato_heartbeat(varco: Varco, campi: list, ts: int) -> None:
    """HB: [correzione, coda, porta, uscite]. Il battito porta lo stato attuale del dispositivo
    ("a"/"c" per la porta, un carattere per uscita 1/0/-, "-" se non applicabile): riallinea quello
    mostrato se un evento si e' perso o se il master e' ripartito. Conta solo cio' che il master ha
    configurato; un battito di uno slave vecchio (senza questi campi) non cambia nulla."""
    porta = campi[2] if len(campi) > 2 else "-"
    if porta in ("a", "c") and funzionamento.sensore_porta_attivo(varco):
        nuovo = "aperta" if porta == "a" else "chiusa"
        if varco.porta_stato != nuovo:       # porta_stato_at e' l'istante del cambio: non si tocca a ogni battito
            varco.porta_stato, varco.porta_stato_at = nuovo, _istante_evento(ts)
    uscite = campi[3] if len(campi) > 3 else "-"
    if uscite != "-":
        fn = funzionamento.da_varco(varco)
        attuale = funzionamento.leggi_stato_uscite(varco)
        for i, c in enumerate(uscite[:funzionamento.N_USCITE]):
            if c in ("0", "1") and i < len(fn["uscite"]) and funzionamento.e_interruttore(fn["uscite"][i]) \
                    and attuale.get(i) != (c == "1"):
                funzionamento.scrivi_stato_uscita(varco, i, c == "1")


def _gestisci_ingresso(db, varco: Varco, campi: list) -> Optional[int]:
    """IN, campi: [indice, stato, seq, ts_evento]. Si registra solo l'attivazione (stato "1")
    di un pulsante/telecomando o di un ingresso "Altro"; il sensore porta usa SP.
    Ritorna il numero da confermare con AE."""
    seq, ts_evento = _estrai_seq(campi, 2)
    try:
        indice, attivo = int(campi[0]), campi[1] == "1"
    except (IndexError, ValueError):
        return seq
    if _e_duplicato(varco, seq):
        return seq
    if seq is not None:
        varco.ultimo_evento_seq = seq
    fn = funzionamento.da_varco(varco)
    if not attivo or not 0 <= indice < funzionamento.N_INGRESSI:
        return seq
    g = fn["ingressi"][indice]
    if g["r"] == "pulsante":
        esito, dettaglio = EsitoEvento.apertura_comando_esterno, f"Ingresso {indice + 1}"
    elif g["r"] == "altro":
        dettaglio = g["nome"] or f"Ingresso {indice + 1}"
        if g["az"] != "registra":
            dettaglio += f" → {funzionamento.nome_uscita(fn, g['az'])}"
        esito = EsitoEvento.ingresso_attivato
    else:
        return seq
    db.add(Evento(timestamp=_istante_evento(ts_evento), varco_id=varco.id, esito=esito, allarme=False, dettaglio=dettaglio[:120]))
    return seq


def _gestisci_stato_uscita(db, varco: Varco, campi: list) -> Optional[int]:
    """SU, campi: [indice, 0/1, seq, ts_evento]: stato di un'uscita acceso/spento.
    Ritorna il numero da confermare con AE."""
    seq, ts_evento = _estrai_seq(campi, 2)
    try:
        indice, acceso = int(campi[0]), campi[1] == "1"
    except (IndexError, ValueError):
        return seq
    if _e_duplicato(varco, seq):
        return seq
    if seq is not None:
        varco.ultimo_evento_seq = seq
    if 0 <= indice < funzionamento.N_USCITE:
        funzionamento.scrivi_stato_uscita(varco, indice, acceso)
    return seq


def invia_conferma_evento(varco: Varco, seq: int) -> bool:
    """AE: conferma di ricezione di un evento, in unicast all'IP del varco."""
    if not varco.ip_address:
        return False
    corpo = _impacchetta_piccolo(["AE", varco.id, int(time.time()), seq])
    pacchetto = corpo + b"|" + _firma(corpo, varco.api_key).encode()
    try:
        _socket_invio().sendto(pacchetto, (varco.ip_address, PORTA_SLAVE))
        connessioni.traccia(f"TX AE a {varco.ip_address} per '{varco.nome}' (conferma evento n. {seq})", varco.id, "PKT_TX", {"tipo": "AE", "ip": varco.ip_address, "seq": seq})
        return True
    except OSError as e:
        logger.warning(f"Invio UDP 'AE' a {varco.ip_address} fallito: {e}")
        connessioni.anomalia(("invio", "AE", varco.id), t("Invio della conferma evento a %(ip_address)s fallito: %(e)s", ip_address=varco.ip_address, e=e), varco.id, "error", "rete")
        return False


INTERVALLO_MIN_CORREZIONE_ORA_SEC = 60
_ultima_correzione_ora: dict[int, float] = {}


def _registra_deriva(varco: Varco, scarto: float, campi: list) -> None:
    """HB: [] oppure [deriva_sec] oppure [deriva_sec, eventi_in_coda]. Uno slave aggiornato
    dichiara la correzione che sta applicando (master - slave): lo scarto grezzo del suo
    orologio e' l'opposto; e quanti eventi non ha ancora consegnato."""
    try:
        presenza.segna_coda(varco.id, int(campi[1]))
    except (IndexError, ValueError):
        pass
    try:
        correzione = float(campi[0])
    except (IndexError, ValueError):
        presenza.segna_deriva(varco.id, scarto, False)
        return
    if abs(correzione) < 1:
        presenza.segna_deriva(varco.id, scarto, False)
    else:
        presenza.segna_deriva(varco.id, -correzione, True)


def _insegna_ora_allo_slave(db, varco: Varco, scarto: float) -> None:
    adesso = time.monotonic()
    if adesso - _ultima_correzione_ora.get(varco.id, -1e9) < INTERVALLO_MIN_CORREZIONE_ORA_SEC:
        return
    _ultima_correzione_ora[varco.id] = adesso
    logger.warning(
        f"Orologio del varco_id={varco.id} sballato di {scarto:+.0f} s: invio la cache "
        f"(porta l'ora corretta) perche' si riallinei"
    )
    invia_cache(db, solo_varco_id=varco.id)


def _gestisci_esito_apri(db, varco: Varco, campi: list) -> None:
    esito = campi[0] if campi else "ignoto"
    registra_log(db, t("Varco '%(nome)s' ha confermato apertura via UDP (%(esito)s)", nome=varco.nome, esito=esito), categoria="varchi")


def _gestisci_cache_ack(db, varco: Varco, campi: list) -> None:
    # campi (senza tipo/varco_id/ts/firma): [versione]
    try:
        versione = int(campi[0])
    except (IndexError, ValueError):
        return
    if varco.ultima_cache_confermata != versione:
        connessioni.scrivi(t("Cache v%(versione)s confermata dal dispositivo di '%(nome)s'.", versione=versione, nome=varco.nome), "info", "dispositivi", varco.id, db,
                           "CACHE_CONFERMATA", {"versione": versione})
    varco.ultima_cache_confermata = versione


# Un solo invio di cache per varco ogni INTERVALLO_MIN_RC_SEC in risposta a RC:
# uno slave che si riavvia a ripetizione non deve poter inondare la rete.
INTERVALLO_MIN_RC_SEC = 10
_ultima_rc: dict[int, float] = {}


def _gestisci_richiesta_cache(db, varco: Varco) -> None:
    """RC: lo slave chiede la cache (appena avviato, o perche' ha solo quella su
    disco). Si risponde con la solita cache, in unicast a questo varco."""
    adesso = time.monotonic()
    if adesso - _ultima_rc.get(varco.id, -1e9) < INTERVALLO_MIN_RC_SEC:
        return
    _ultima_rc[varco.id] = adesso
    logger.info(f"Cache richiesta dal varco_id={varco.id} (RC): invio")
    connessioni.scrivi(t("Il dispositivo di '%(nome)s' chiede la cache (di solito si e' appena avviato o riavviato): gliela invio.", nome=varco.nome),
                       "info", "dispositivi", varco.id, db, "CACHE_RICHIESTA")
    invia_cache(db, solo_varco_id=varco.id)


def _elabora_pacchetto(dato: bytes, mittente_ip: str) -> None:
    try:
        testo = dato.decode()
        parti = testo.split("|")
        if len(parti) < 4:
            return
        tipo, varco_id_str, ts_str = parti[0], parti[1], parti[2]
        firma_ricevuta = parti[-1]
        campi_specifici = parti[3:-1]
        varco_id = int(varco_id_str)
        ts = int(ts_str)
    except (UnicodeDecodeError, ValueError, IndexError):
        return

    if tipo == "PR":
        # PR|varco_id|ts|nonce, senza firma: parti[3] e' il nonce (qui letto come "firma").
        if len(parti) == 4:
            _gestisci_richiesta_abbinamento(varco_id, parti[3], mittente_ip)
        return

    if tipo not in ("HB", "EA", "EV", "SP", "CA", "RC", "IN", "SU"):
        return  # "cache"/"AP" sono mandati dal master, non li aspettiamo in ricezione

    canonico = "|".join(parti[:-1]).encode()

    db = SessionLocal()
    try:
        varco = db.query(Varco).filter(Varco.id == varco_id).first()
        if varco is None:
            connessioni.anomalia(("ignoto", varco_id, mittente_ip),
                                 t("Pacchetto %(tipo)s da %(mittente_ip)s per il varco n. %(varco_id)s, che non esiste: scartato.", tipo=tipo, mittente_ip=mittente_ip, varco_id=varco_id), None, "warning", "rete",
                                 dettagli={"tipo": tipo, "ip": mittente_ip, "varco_id": varco_id})
            return
        if varco.ip_address != mittente_ip:
            presenza.segna_scarto(varco_id, "ip", mittente_ip)
            connessioni.anomalia(("ip", varco_id, mittente_ip),
                                 t("Pacchetto %(tipo)s per '%(nome)s' scartato: arriva da %(mittente_ip)s ma il varco e' registrato con IP %(p)s. Se lo slave ha cambiato indirizzo, riabbinalo o correggi l'IP.", tipo=tipo, nome=varco.nome, mittente_ip=mittente_ip, p=varco.ip_address or t('nessuno')),
                                 varco_id, "warning", "rete", db,
                                 dettagli={"tipo": tipo, "ip_mittente": mittente_ip, "ip_registrato": varco.ip_address})
            db.commit()
            logger.warning(
                f"Pacchetto UDP per varco_id={varco_id} da IP non registrato "
                f"({mittente_ip}, atteso {varco.ip_address!r}): ignorato"
            )
            return
        if not _verifica(canonico, varco.api_key, firma_ricevuta):
            presenza.segna_scarto(varco_id, "firma", mittente_ip)
            logger.warning(f"Firma non valida su pacchetto UDP da varco_id={varco_id}: ignorato")
            connessioni.anomalia(("firma", varco_id, mittente_ip),
                                 t("Pacchetto %(tipo)s da %(mittente_ip)s per '%(nome)s' scartato: firma non valida. La chiave dello slave non coincide con quella del master: riabbinalo (Varchi -> Reimposta la chiave).", tipo=tipo, mittente_ip=mittente_ip, nome=varco.nome),
                                 varco_id, "warning", "rete", db, dettagli={"tipo": tipo, "ip": mittente_ip})
            db.commit()
            return
        scarto = ts - time.time()
        if tipo == "HB":
            _registra_deriva(varco, scarto, campi_specifici)
        if abs(scarto) > FINESTRA_REPLAY_SEC:
            if tipo == "HB":
                # Slave vivo (firma valida) ma con l'ora sballata: i suoi pacchetti
                # vengono scartati finche' non la corregge, quindi il varco
                # sembrerebbe offline senza spiegazione. Gli si rimanda la cache,
                # che porta l'ora del master e il fuso: da li' in poi si corregge da solo.
                _insegna_ora_allo_slave(db, varco, scarto)
            else:
                logger.warning(f"Pacchetto UDP da varco_id={varco_id} fuori finestra temporale: ignorato")
            connessioni.anomalia(("orologio", varco_id),
                                 f"Pacchetto {tipo} da '{varco.nome}' scartato: l'orologio dello slave differisce di {scarto:+.0f} s "
                                 f"(massimo {FINESTRA_REPLAY_SEC} s)." + (" Gli invio la cache con l'ora corretta." if tipo == "HB" else ""),
                                 varco_id, "warning", "rete", db,
                                 dettagli={"tipo": tipo, "scarto_s": round(scarto), "finestra_s": FINESTRA_REPLAY_SEC, "ip": mittente_ip})
            db.commit()
            return

        # Qualunque pacchetto valido (anche solo l'heartbeat) vale da segnale di vita.
        # Resta in memoria (services/presenza.py); su disco va solo al cambio di
        # stato o al massimo una volta al minuto, non a ogni pacchetto.
        connessioni.contatto(db, varco, mittente_ip, tipo, presenza.ultimo_contatto(varco.id))
        connessioni.traccia(f"RX {tipo} da {mittente_ip} per '{varco.nome}'" + (f" ({'|'.join(campi_specifici)[:80]})" if campi_specifici and tipo != "HB" else ""),
                            varco.id, "PKT_RX", {"tipo": tipo, "ip": mittente_ip, "scarto_orologio_s": round(scarto, 1), "byte": len(dato)}, db)
        if presenza.segna_contatto(varco.id):
            varco.ultimo_heartbeat = datetime.utcnow()
        if varco.abbinamento_at is not None:
            varco.abbinamento_ip = varco.abbinamento_nonce = varco.abbinamento_at = None   # parla gia' con la chiave giusta

        da_confermare = None
        if tipo == "EV":
            da_confermare = _gestisci_evento(db, varco, campi_specifici)
        elif tipo == "SP":
            da_confermare = _gestisci_sensore_porta(db, varco, campi_specifici)
        elif tipo == "IN":
            da_confermare = _gestisci_ingresso(db, varco, campi_specifici)
        elif tipo == "SU":
            da_confermare = _gestisci_stato_uscita(db, varco, campi_specifici)
        elif tipo == "EA":
            _gestisci_esito_apri(db, varco, campi_specifici)
        elif tipo == "CA":
            _gestisci_cache_ack(db, varco, campi_specifici)
        elif tipo == "RC":
            _gestisci_richiesta_cache(db, varco)
        elif tipo == "HB":
            _gestisci_stato_heartbeat(varco, campi_specifici, ts)

        db.commit()
        # La conferma parte solo dopo il commit: se la scrittura fallisce lo
        # slave non la riceve e ritrasmette.
        if da_confermare is not None:
            invia_conferma_evento(varco, da_confermare)
        # Anti-passback a blocco: dopo un passaggio la posizione e' cambiata, i varchi a blocco ricevono la cache nuova.
        if getattr(_cache_ap_da_rimandare, "v", False):
            _cache_ap_da_rimandare.v = False
            aggiorna_cache_ap(db)
    finally:
        db.close()


def _loop_ricezione():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", PORTA))
    sock.settimeout(1.0)
    logger.info(f"Servizio UDP agent in ascolto sulla porta {PORTA}")
    while not _stop.is_set():
        try:
            dato, (ip, _porta) = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            _elabora_pacchetto(dato, ip)
        except Exception:
            logger.exception("Errore inatteso elaborando un pacchetto UDP")
    sock.close()


def _loop_cache_periodica():
    # La prima cache parte subito all'avvio (comodo dopo un riavvio del
    # backend), poi ogni INTERVALLO_CACHE_SEC.
    while not _stop.is_set():
        db = SessionLocal()
        try:
            mandati = invia_cache(db)
            logger.info(f"Cache periodica inviata a {len(mandati)} varchi")
        except Exception:
            logger.exception("Errore nell'invio periodico della cache")
        finally:
            db.close()
        _stop.wait(INTERVALLO_CACHE_SEC)


# ---------- Tocco ----------

INTERVALLO_TOCCO_SEC = 3            # un varco silenzioso si tocca ogni 3 s...
INTERVALLO_TOCCO_LENTO_SEC = 15     # ...e ogni 15 s dopo SILENZIO_LUNGO_SEC (spento o scollegato)
SILENZIO_LUNGO_SEC = 120
_ultimo_tocco: dict[int, float] = {}
_silenzio_dal: dict[int, float] = {}


def tocca_varco(varco: Varco) -> bool:
    """HI|varco|ts|firma, unicast all'IP del varco: «sono il master, rispondi». Lo slave impara da qui l'IP
    del master. Ritorna False se il varco non ha IP o l'invio fallisce."""
    if not varco.ip_address:
        return False
    corpo = _impacchetta_piccolo(["HI", varco.id, int(time.time())])
    pacchetto = corpo + b"|" + _firma(corpo, varco.api_key).encode()
    try:
        _socket_invio().sendto(pacchetto, (varco.ip_address, PORTA_SLAVE))
        return True
    except OSError as e:
        logger.warning(f"Invio UDP 'HI' a {varco.ip_address} fallito: {e}")
        connessioni.anomalia(("invio", "HI", varco.id), t("Invio del contatto a %(ip_address)s fallito: %(e)s", ip_address=varco.ip_address, e=e), varco.id, "error", "rete")
        return False


def _tocca_silenziosi(db) -> int:
    """Tocca i varchi con IP che non si fanno sentire; ritorna quanti. Un varco che parla non si tocca."""
    adesso = time.monotonic()
    ora_utc = datetime.utcnow()
    toccati = 0
    for varco in db.query(Varco).filter(Varco.ip_address.isnot(None)).all():
        ultimo = presenza.ultimo_contatto(varco.id) or varco.ultimo_heartbeat
        if ultimo and (ora_utc - ultimo).total_seconds() < ONLINE_ENTRO_SEC:
            _silenzio_dal.pop(varco.id, None)
            continue
        dal = _silenzio_dal.setdefault(varco.id, adesso)
        intervallo = INTERVALLO_TOCCO_LENTO_SEC if adesso - dal > SILENZIO_LUNGO_SEC else INTERVALLO_TOCCO_SEC
        if adesso - _ultimo_tocco.get(varco.id, -1e9) < intervallo - 0.2:
            continue
        _ultimo_tocco[varco.id] = adesso
        if tocca_varco(varco):
            toccati += 1
    return toccati


def _loop_tocco():
    while not _stop.wait(1.0):
        db = SessionLocal()
        try:
            _tocca_silenziosi(db)
        except Exception:
            logger.exception("Errore nel contatto dei varchi silenziosi")
        finally:
            db.close()


INTERVALLO_CONTROLLO_OFFLINE_SEC = 3
_thread_connessioni: Optional[threading.Thread] = None
_thread_tocco: Optional[threading.Thread] = None


def _loop_connessioni():
    """Nota quando un dispositivo smette di farsi sentire (l'ONLINE si scrive al pacchetto che arriva)."""
    while not _stop.wait(INTERVALLO_CONTROLLO_OFFLINE_SEC):
        try:
            connessioni.controlla_offline()
        except Exception:
            logger.exception("Errore nel controllo dei dispositivi offline")


# ---------- Avvio/arresto (da main.py) ----------

def stato() -> dict:
    """Stato dei thread del canale UDP, per la pagina Sistema — non è lo
    stato dei singoli varchi (quello è in Varco.ultimo_heartbeat), ma del
    servizio stesso dentro questo processo."""
    return {
        "ricezione_attiva": bool(_thread_ricezione and _thread_ricezione.is_alive()),
        "cache_attiva": bool(_thread_cache and _thread_cache.is_alive()),
    }


def avvia():
    global _thread_ricezione, _thread_cache, _thread_connessioni, _thread_tocco
    _stop.clear()
    db = SessionLocal()
    try:
        n = db.query(Varco).count()
        connessioni.scrivi(t("Master avviato: canale UDP in ascolto sulla porta %(PORTA)s, %(varchi)s. Lo stato dei dispositivi si ricostruisce dai loro heartbeat (ogni 3 s).", PORTA=PORTA, varchi=t("1 varco configurato") if n == 1 else t("%(n)s varchi configurati", n=n)), "info", "sistema", db=db,
                           codice="SYS_AVVIO", dettagli={"porta_udp": PORTA, "varchi": n})
        db.commit()
    except Exception:
        logger.exception("Impossibile scrivere l'avvio nel log")
    finally:
        db.close()
    _thread_ricezione = threading.Thread(target=_loop_ricezione, daemon=True)
    _thread_ricezione.start()
    _thread_cache = threading.Thread(target=_loop_cache_periodica, daemon=True)
    _thread_cache.start()
    _thread_connessioni = threading.Thread(target=_loop_connessioni, daemon=True)
    _thread_connessioni.start()
    _thread_tocco = threading.Thread(target=_loop_tocco, daemon=True)
    _thread_tocco.start()


def arresta():
    _stop.set()
    connessioni.scrivi(t("Master in arresto: i dispositivi risulteranno offline finche' non riparte."), "info", "sistema", codice="SYS_ARRESTO")
