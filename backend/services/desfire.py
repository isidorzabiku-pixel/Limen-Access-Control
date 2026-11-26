# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Tessere MIFARE DESFire scritte da questo impianto.

La tessera non contiene un codice: il lettore ricava la sua chiave dalla chiave d'impianto e dall'UID (NXP AN10922)
e si autentica in AES; se riesce, l'UID e' autentico e la credenziale e' "DF-<UID>" (firmware/esp32/lib/limen/desfire.h).
Il master collega quel codice al N° tessera, quello stampato sulla tessera, che serve alle persone (si riconoscono
le tessere a vista) e all'operatore (ricerca: «123» trova anche «000123»).

Scrittura («Scrivi badge»): il master apre una finestra di scrittura su un varco (DW|id|ts|sessione|secondi); il
lettore scrive la prima DESFire vergine appoggiata e risponde DR|id|ts|sessione|esito|uid|motivo; il master registra
la credenziale e chiude la sessione (DW ... 0). Le sessioni stanno in memoria: durano pochi secondi.

Pulizia («Pulisci tessera»): due passi con DC|id|ts|sessione|secondi|modo. Modo "i": il lettore riconosce la tessera
(esito "nostra" + UID); se e' assegnata il master chiede conferma dicendo di chi e'. Modo "c" (sessione "<tok>-p"): il
lettore cancella dalla tessera la sola applicazione Limen (esito "pulita") e il master elimina il suo titolo.
"""
import logging
import re
import secrets
import threading
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from models import AccessMedia, Persona, TipoMedia, Varco
from services.lingua import t

PREFISSO = "DF-"
SECONDI_FINESTRA = 30
CIFRE_MAX = 12
NUMERO = re.compile(r"^\d{1,%d}$" % CIFRE_MAX)
UID = re.compile(r"^[0-9A-F]{8,20}$")

_sessioni: dict = {}
_lock = threading.Lock()


def codice(uid: str) -> str:
    return PREFISSO + uid.upper()


def e_codice_desfire(c: str) -> bool:
    return (c or "").startswith(PREFISSO)


def normalizza_numero(testo: str) -> str:
    """Il N° tessera come scritto (zeri iniziali compresi), solo cifre; "" se non valido."""
    n = re.sub(r"[\s.\-]", "", testo or "")
    return n if NUMERO.match(n) else ""


def _valore(numero: str) -> int:
    try:
        return int(numero)
    except (TypeError, ValueError):
        return -1


def tessere(db: Session):
    return db.query(AccessMedia).filter(AccessMedia.tipo == TipoMedia.desfire).all()


def con_numero(db: Session, numero: str) -> Optional[AccessMedia]:
    """La tessera con quel numero, senza badare agli zeri iniziali (000123 = 123)."""
    v = _valore(numero)
    return next((m for m in tessere(db) if _valore(m.numero) == v), None)


def ids_persone_con_numero(db: Session, testo: str) -> set:
    """Per la ricerca in Persone: chi ha una tessera DESFire con quel numero (zeri iniziali ignorati)."""
    n = normalizza_numero(testo)
    if not n:
        return set()
    v = _valore(n)
    return {m.persona_id for m in tessere(db) if _valore(m.numero) == v}


def prossimo_numero(db: Session) -> str:
    """Il successivo al piu' alto gia' usato, con lo stesso numero di cifre (000123 → 000124)."""
    usati = [(m.numero or "") for m in tessere(db) if _valore(m.numero) >= 0]
    if not usati:
        return "000001"
    piu_alto = max(usati, key=_valore)
    return str(_valore(piu_alto) + 1).zfill(len(piu_alto))


_numeri: dict = {}
_numeri_alle = datetime.min


def numero_da_codice(c: str) -> str:
    """N° tessera di un codice DF-<UID> per Monitoraggio e feed ("" se sconosciuto). Tabella in memoria rinfrescata
    ogni 20 s: una pagina con cento passaggi non fa cento query."""
    global _numeri, _numeri_alle
    if not e_codice_desfire(c):
        return ""
    if datetime.utcnow() - _numeri_alle > timedelta(seconds=20) or c not in _numeri:
        from database import SessionLocal
        db = SessionLocal()
        try:
            _numeri = {m.codice: (m.numero or "") for m in tessere(db)}
            _numeri_alle = datetime.utcnow()
        finally:
            db.close()
    return _numeri.get(c, "")


# ---------- scrittura ----------

def avvia(db: Session, persona: Persona, varco: Varco, numero: str, sostituisce: Optional[AccessMedia] = None) -> str:
    """Apre la finestra di scrittura sul varco. Ritorna l'identificativo della sessione (anche nel pacchetto)."""
    from services import udp_agent
    tok = secrets.token_hex(4)
    with _lock:
        _pulisci()
        for v in list(_sessioni.values()):                 # una sola scrittura alla volta per varco
            if v["varco_id"] == varco.id and v["stato"] == "attesa":
                v["stato"], v["messaggio"] = "annullata", t("Annullata: è partita un'altra scrittura sullo stesso lettore.")
        _sessioni[tok] = {
            "persona_id": persona.id, "varco_id": varco.id, "varco": varco.nome, "numero": numero,
            "sostituisce": sostituisce.id if sostituisce is not None else None,
            "creato": datetime.utcnow(), "scade": datetime.utcnow() + timedelta(seconds=SECONDI_FINESTRA + 3),
            "stato": "attesa", "messaggio": "", "uid": "",
        }
    if not udp_agent.apri_finestra_desfire(varco, tok, SECONDI_FINESTRA):
        with _lock:
            _sessioni[tok]["stato"], _sessioni[tok]["messaggio"] = "errore", t("Il lettore non è raggiungibile.")
    return tok


def annulla(tok: str, db: Session) -> None:
    from services import udp_agent
    with _lock:
        s = _sessioni.get(tok)
        if s is None or s["stato"] not in ("attesa", "conferma"):
            return
        s["stato"], s["messaggio"] = "annullata", ""
    varco = db.get(Varco, s["varco_id"])
    if varco is not None:
        udp_agent.chiudi_finestra_desfire(varco, tok + SUFFISSO_PULIZIA if s.get("fase") == "pul" else tok)


def stato(tok: str) -> Optional[dict]:
    with _lock:
        s = _sessioni.get(tok)
        if s is None:
            return None
        if s["stato"] == "attesa" and datetime.utcnow() > s["scade"]:
            s["stato"], s["messaggio"] = "tempo", t("Nessuna tessera: il tempo è scaduto.")
        rimasti = max(0, int((s["scade"] - datetime.utcnow()).total_seconds()) - 3)
        return dict(s, rimasti=rimasti)


def _pulisci() -> None:
    limite = datetime.utcnow() - timedelta(minutes=10)
    for k in [k for k, v in _sessioni.items() if v["creato"] < limite]:
        del _sessioni[k]


SUFFISSO_PULIZIA = "-p"


def avvia_pulizia(db: Session, varco: Varco) -> str:
    """Primo passo di «Pulisci tessera»: il lettore riconosce la tessera appoggiata."""
    from services import udp_agent
    tok = secrets.token_hex(4)
    with _lock:
        _pulisci()
        _sessioni[tok] = {
            "modo": "pulisci", "fase": "id", "persona_id": None, "varco_id": varco.id, "varco": varco.nome, "numero": "",
            "sostituisce": None, "media_id": None, "creato": datetime.utcnow(),
            "scade": datetime.utcnow() + timedelta(seconds=SECONDI_FINESTRA + 3), "stato": "attesa", "messaggio": "", "uid": "",
        }
    if not udp_agent.apri_finestra_pulizia_desfire(varco, tok, SECONDI_FINESTRA, "i"):
        with _lock:
            _sessioni[tok]["stato"], _sessioni[tok]["messaggio"] = "errore", t("Il lettore non è raggiungibile.")
    return tok


def _avvia_cancellazione(db: Session, tok: str, s: dict) -> None:
    from services import udp_agent
    varco = db.get(Varco, s["varco_id"])
    with _lock:
        s["fase"], s["stato"], s["messaggio"] = "pul", "attesa", ""
        s["scade"] = datetime.utcnow() + timedelta(seconds=SECONDI_FINESTRA + 3)
    if varco is None or not udp_agent.apri_finestra_pulizia_desfire(varco, tok + SUFFISSO_PULIZIA, SECONDI_FINESTRA, "c"):
        with _lock:
            s["stato"], s["messaggio"] = "errore", t("Il lettore non è raggiungibile.")


def conferma_pulizia(tok: str, db: Session) -> None:
    """L'operatore ha confermato: la tessera assegnata si pulisce (deve essere ancora sul lettore)."""
    with _lock:
        s = _sessioni.get(tok)
        if s is None or s.get("modo") != "pulisci" or s["stato"] != "conferma":
            return
    _avvia_cancellazione(db, tok, s)


def _manda_cache_dopo_revoca(db: Session) -> None:
    """Una tessera pulita o sostituita smette di aprire: la cache va subito ai varchi (come per l'eliminazione di un
    titolo), senza «Invia dati». Un errore non blocca l'operazione."""
    from services import udp_agent
    try:
        udp_agent.invia_cache_finche_confermata(db)
    except Exception:
        logging.getLogger(__name__).exception("Cache non inviata dopo la revoca di una tessera DESFire")


def _esito_pulizia(db: Session, tok: str, s: dict, pulizia: bool, esito: str, uid: str, motivo: str) -> None:
    from services.logger import registra_log
    if (s["fase"] == "pul") != pulizia or s["stato"] != "attesa":
        return                                       # ripetizione di un esito gia' gestito
    uid_ok = bool(UID.match(uid))
    media = db.query(AccessMedia).filter(AccessMedia.codice == codice(uid)).first() if uid_ok else None
    persona = media.persona if media is not None else None
    chi = f"{persona.nome} {persona.cognome}" if persona else ""
    if not pulizia:
        if esito == "nostra" and uid_ok:
            with _lock:
                s["uid"] = uid
            if media is None:
                _avvia_cancellazione(db, tok, s)       # di nessuno: si pulisce subito
            else:
                with _lock:
                    s["stato"], s["media_id"] = "conferma", media.id
                    s["messaggio"] = t("È la N° %(n)s di %(chi)s", n=media.numero or "?", chi=chi or "?")
            return
    elif esito == "pulita" and uid_ok:
        if media is not None:
            numero = media.numero
            db.delete(media)
            db.commit()
            _manda_cache_dopo_revoca(db)
            registra_log(db, t("Tessera DESFire N° %(n)s di %(chi)s pulita: titolo eliminato (UID %(uid)s, lettore '%(varco)s')",
                               n=numero or "?", chi=chi or "?", uid=uid, varco=s["varco"]), livello="warning", categoria="persone")
            messaggio = t("Tessera pulita. Titolo N° %(n)s di %(chi)s eliminato.", n=numero or "?", chi=chi or "?")
        else:
            registra_log(db, t("Tessera DESFire pulita (UID %(uid)s, lettore '%(varco)s')", uid=uid, varco=s["varco"]), categoria="persone")
            messaggio = t("Tessera pulita: è di nuovo vergine.")
        with _lock:
            s["stato"], s["messaggio"], s["uid"] = "fatto", messaggio, uid
        return
    with _lock:
        s["stato"] = "errore"
        s["messaggio"] = t(MESSAGGI.get(esito, MESSAGGI["errore"])) + (f" ({motivo})" if motivo and esito == "errore" else "")


MESSAGGI = {
    "nonnostra": "Non è una tessera di questo impianto: non è stata toccata.",
    "altro": "Tessera di un altro sistema: serve una DESFire vergine.",
    "nondesfire": "Non è una tessera DESFire.",
    "tolta": "Tessera tolta durante la scrittura: riappoggiala e riprova.",
    "errore": "Scrittura non riuscita.",
}


def esito_ricevuto(db: Session, varco: Varco, campi: list) -> Optional[str]:
    """DR dal varco: campi = [sessione, esito, uid, motivo]. Registra la credenziale. Ritorna la sessione (da
    chiudere con DW ... 0) o None se il pacchetto non corrisponde a nulla."""
    if len(campi) < 3:
        return None
    tok, esito, uid = campi[0], campi[1], (campi[2] or "").upper()
    motivo = campi[3] if len(campi) > 3 else ""
    ricevuto, pulizia = tok, tok.endswith(SUFFISSO_PULIZIA)
    if pulizia:
        tok = tok[:-len(SUFFISSO_PULIZIA)]
    with _lock:
        s = _sessioni.get(tok)
        if s is None or s["varco_id"] != varco.id:
            return ricevuto if s is None else None      # sessione scaduta: si chiude comunque sul varco
    if s.get("modo") == "pulisci":
        _esito_pulizia(db, tok, s, pulizia, esito, uid, motivo)
        return ricevuto
    with _lock:
        if s["stato"] != "attesa":
            return tok                                   # ripetizione dello stesso esito: gia' gestito
    nuovo_stato, messaggio, uid_ok = "errore", "", bool(UID.match(uid))
    if esito in ("scritta", "gia") and uid_ok:
        nuovo_stato, messaggio = _registra(db, s, uid, esito)
    else:
        messaggio = t(MESSAGGI.get(esito, MESSAGGI["errore"])) + (f" ({motivo})" if motivo and esito == "errore" else "")
    with _lock:
        s["stato"], s["messaggio"], s["uid"] = nuovo_stato, messaggio, uid
    return tok


def _registra(db: Session, s: dict, uid: str, esito: str) -> tuple[str, str]:
    from services.logger import registra_log
    c = codice(uid)
    esistente = db.query(AccessMedia).filter(AccessMedia.codice == c).first()
    persona = db.get(Persona, s["persona_id"])
    if persona is None:
        return "errore", t("La persona non esiste più.")
    if esistente is not None and esistente.id != s["sostituisce"]:
        p = esistente.persona
        return "errore", t("Tessera già scritta: è la N° %(n)s di %(chi)s. Eliminala lì prima di riusarla.",
                           n=esistente.numero or "?", chi=f"{p.nome} {p.cognome}" if p else "?")
    altra = con_numero(db, s["numero"])
    if altra is not None and altra.id != s["sostituisce"]:
        return "errore", t("Il N° %(n)s è già usato da un'altra tessera.", n=s["numero"])
    if s["sostituisce"]:
        m = db.get(AccessMedia, s["sostituisce"])
        if m is None:
            return "errore", t("La tessera da sostituire non esiste più.")
        vecchio = m.uid
        m.codice, m.uid, m.numero = c, uid, s["numero"]
        db.commit()
        _manda_cache_dopo_revoca(db)                    # la tessera vecchia non deve piu' aprire
        registra_log(db, t("Tessera DESFire N° %(n)s di %(chi)s riscritta su una tessera nuova (UID %(uid)s, prima %(vecchio)s)",
                           n=s["numero"], chi=f"{persona.nome} {persona.cognome}", uid=uid, vecchio=vecchio or "?"), categoria="persone")
    else:
        m = AccessMedia(codice=c, tipo=TipoMedia.desfire, numero=s["numero"], uid=uid, persona_id=persona.id)
        db.add(m)
        db.commit()
        registra_log(db, t("Tessera DESFire N° %(n)s scritta per %(chi)s (UID %(uid)s, lettore '%(varco)s')",
                           n=s["numero"], chi=f"{persona.nome} {persona.cognome}", uid=uid, varco=s["varco"]), categoria="persone")
    return "fatto", t("Tessera N° %(n)s scritta.", n=s["numero"]) if esito == "scritta" else t("Tessera N° %(n)s registrata (era già scritta da questo impianto).", n=s["numero"])
