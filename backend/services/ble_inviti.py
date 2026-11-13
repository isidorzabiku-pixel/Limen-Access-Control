# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Telefoni (credenziale BLE): chiave dell'impianto, inviti, abbinamento.

Flusso: l'admin crea un invito per una persona e un varco; il master apre la finestra su quel varco passando
allo slave la chiave derivata dall'invito (pacchetto BP); la persona scrive l'invito nell'app, davanti al
varco; lo slave inoltra (AB) solo le richieste con la prova giusta; qui si ricontrolla e nasce subito il
titolo del telefono, senza altra approvazione: l'invito e' gia' l'autorizzazione dell'admin (una persona,
un varco, 10 minuti, una volta sola). Il segreto del telefono lo calcola lo slave dal titolo (AR) e lo passa
al telefono cifrato. I calcoli sono in `ble_abbinamento.py`.
"""
import hmac
from datetime import datetime, timedelta

from models import AccessMedia, Impostazione, InvitoBle, RichiestaAbbinamentoBle, TipoMedia, Persona
from services import ble_abbinamento as ba

MAX_ERRORI_INVITO = 5          # richieste sbagliate arrivate al master per lo stesso invito: poi si annulla
CHIAVE_IMPOSTAZIONE = "chiave_telefoni"
motivo_ultimo = ""             # perche' l'ultima richiesta e' stata respinta (per il log)


# ---------- Chiave dei telefoni dell'impianto ----------

def chiave_telefoni(db) -> bytes:
    """La chiave nasce da sola al primo uso e resta nel database (quindi nel backup)."""
    riga = db.get(Impostazione, CHIAVE_IMPOSTAZIONE)
    if riga is not None:
        try:
            k = bytes.fromhex(riga.valore)
            if len(k) == 32:
                return k
        except ValueError:
            pass
    k = ba.nuova_chiave_telefoni()
    if riga is None:
        db.add(Impostazione(chiave=CHIAVE_IMPOSTAZIONE, valore=k.hex()))
    else:
        riga.valore = k.hex()
    db.commit()
    return k


def conta_telefoni(db) -> int:
    return db.query(AccessMedia).filter(AccessMedia.tipo == TipoMedia.ble).count()


def rigenera_chiave_telefoni(db) -> int:
    """Chiave nuova (per esempio dopo il furto di un dispositivo): i segreti dei telefoni derivano dalla vecchia,
    quindi tutti i titoli dei telefoni si eliminano e i telefoni vanno abbinati di nuovo. Ritorna quanti erano.
    Chi chiama manda poi la cache."""
    telefoni = db.query(AccessMedia).filter(AccessMedia.tipo == TipoMedia.ble).all()
    for m in telefoni:
        db.delete(m)
    for inv in db.query(InvitoBle).all():
        db.delete(inv)
    riga = db.get(Impostazione, CHIAVE_IMPOSTAZIONE)
    nuova = ba.nuova_chiave_telefoni().hex()
    if riga is None:
        db.add(Impostazione(chiave=CHIAVE_IMPOSTAZIONE, valore=nuova))
    else:
        riga.valore = nuova
    db.commit()
    return len(telefoni)


# ---------- Inviti ----------

def crea_invito(db, persona_id: int, varco_id: int) -> tuple[str, bytes, str]:
    """Un solo invito aperto per persona e per varco (lo slave tiene una chiave alla volta).
    Ritorna (codice da mostrare una volta, chiave derivata, identificativo)."""
    aperti = db.query(InvitoBle).filter(InvitoBle.usato_il.is_(None),
                                        (InvitoBle.persona_id == persona_id) | (InvitoBle.varco_id == varco_id)).all()
    for vecchio in aperti:
        db.delete(vecchio)
    codice = ba.nuovo_invito()
    chiave = ba.deriva_chiave(codice)
    kid = ba.id_invito(chiave)
    db.add(InvitoBle(persona_id=persona_id, varco_id=varco_id, hash_codice=kid, kid=kid, chiave=chiave.hex(),
                     scade_il=datetime.utcnow() + timedelta(seconds=ba.SCADENZA_INVITO_SEC)))
    db.commit()
    return codice, chiave, kid


def annulla_invito(db, kid: str) -> None:
    riga = db.query(InvitoBle).filter(InvitoBle.kid == kid).first()
    if riga is not None:
        db.delete(riga)
        db.commit()


def pulisci_scaduti(db) -> None:
    """Le chiavi degli inviti scaduti non restano nel database."""
    for riga in db.query(InvitoBle).filter(InvitoBle.chiave.isnot(None), InvitoBle.scade_il < datetime.utcnow()).all():
        riga.chiave = None
    db.commit()


def gestisci_richiesta(db, varco_id: int, campi: list) -> tuple[str, str, AccessMedia | None, bool]:
    """Campi del pacchetto AB: id invito, sfida, nonce del telefono, prova (esadecimali).
    Ritorna (esito "ok"/"no", id invito, credenziale, True se e' stata creata adesso).
    Lo slave ripete la richiesta se la risposta si perde: la stessa richiesta riceve lo stesso titolo."""
    global motivo_ultimo
    motivo_ultimo = ""

    def no(motivo: str, kid: str = ""):
        global motivo_ultimo
        motivo_ultimo = motivo
        return "no", kid, None, False

    if len(campi) != 4:
        return no("pacchetto incompleto (firmware da aggiornare?)")
    kid, sfida_hex, nonce_hex, prova_hex = campi
    try:
        sfida, nonce_tel, prova = bytes.fromhex(sfida_hex), bytes.fromhex(nonce_hex), bytes.fromhex(prova_hex)
    except ValueError:
        return no("dati non validi", kid)
    riga = db.query(InvitoBle).filter(InvitoBle.kid == kid, InvitoBle.varco_id == varco_id).first()
    adesso = datetime.utcnow()
    if riga is None:
        return no("invito sconosciuto per questo varco", kid)
    if riga.scade_il < adesso or not riga.chiave:
        riga.chiave = None
        db.commit()
        return no("invito scaduto o annullato", kid)
    k = bytes.fromhex(riga.chiave)
    giusta = (len(sfida) == ba.LUNGHEZZA_NONCE and len(nonce_tel) == ba.LUNGHEZZA_NONCE
              and hmac.compare_digest(ba.prova_telefono(k, sfida, nonce_tel), prova))
    if riga.usato_il is not None:
        # Gia' usato: vale solo la ripetizione della stessa richiesta (risposta persa per strada).
        if giusta and riga.titolo and riga.sfida_usata == sfida_hex.lower() + nonce_hex.lower():
            media = db.query(AccessMedia).filter(AccessMedia.codice == riga.titolo).first()
            if media is not None:
                return "ok", kid, media, False
        return no("invito gia' usato", kid)
    if not giusta:
        riga.tentativi_errati = (riga.tentativi_errati or 0) + 1
        if riga.tentativi_errati >= MAX_ERRORI_INVITO:
            riga.chiave = None
            riga.usato_il = adesso
        db.commit()
        return no("prova dell'invito sbagliata", kid)
    if db.get(Persona, riga.persona_id) is None:
        riga.chiave = None
        db.commit()
        return no("la persona non esiste piu'", kid)
    chiave_telefoni(db)                         # deve esistere prima che lo slave calcoli il segreto
    titolo = ba.nuovo_titolo()
    while db.query(AccessMedia).filter(AccessMedia.codice == titolo).first() is not None:
        titolo = ba.nuovo_titolo()
    media = AccessMedia(tipo=TipoMedia.ble, codice=titolo, attivo=True, persona_id=riga.persona_id)
    db.add(media)
    db.add(RichiestaAbbinamentoBle(persona_id=riga.persona_id, varco_id=varco_id, impronta=titolo,
                                   chiave_pubblica="", esito="approvata"))
    riga.usato_il = adesso
    riga.titolo = titolo
    riga.sfida_usata = sfida_hex.lower() + nonce_hex.lower()
    db.commit()
    return "ok", kid, media, True


def abbinati_di_recente(db, ore: int = 24) -> list[RichiestaAbbinamentoBle]:
    limite = datetime.utcnow() - timedelta(hours=ore)
    return (db.query(RichiestaAbbinamentoBle)
            .filter(RichiestaAbbinamentoBle.esito == "approvata", RichiestaAbbinamentoBle.creata_il >= limite)
            .order_by(RichiestaAbbinamentoBle.creata_il.desc()).all())
