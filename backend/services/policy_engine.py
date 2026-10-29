# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Regole di accesso lato master: quali policy valgono per una persona, se una
fascia oraria e' valida in un dato momento (festivita' comprese), quale esito
ne verrebbe su un varco.

NB: la decisione vera ad ogni lettura badge la prende lo slave in locale, con
la cache che riceve (vedi agent/services/udp_client.py); il master applica le
stesse regole per costruire quella cache, per spiegare un esito ("perche' e'
stato negato?"), per mostrare i permessi effettivi di una persona e per tenere
l'anti-passback.
"""
import calendar
from datetime import date, datetime, timedelta
from sqlalchemy.orm import Session

from models import AccessMedia, Policy, EsitoEvento, Varco, Festivita
from services import funzionamento, tempo
from services.lingua import t


# ---------- Festivita' ----------

class Festivi:
    """Giorni festivi in memoria: date fisse + ricorrenti (mese, giorno)."""

    def __init__(self, righe=()):
        self.date: set[date] = set()
        self.ricorrenti: set[tuple[int, int]] = set()
        for r in righe:
            if r.ricorrente:
                self.ricorrenti.add((r.data.month, r.data.day))
            else:
                self.date.add(r.data)

    def __contains__(self, giorno: date) -> bool:
        return giorno in self.date or (giorno.month, giorno.day) in self.ricorrenti


def carica_festivi(db: Session) -> Festivi:
    return Festivi(db.query(Festivita).all())


def fascia_valida_ora(fascia_oraria, momento: datetime, festivi: Festivi | None = None) -> bool:
    """Nessuna fascia oraria collegata = sempre valida (H24).
    `momento` deve essere in ora LOCALE (le fasce sono scritte in ora locale).
    Nei giorni festivi vale la riga «Festivi» della griglia (giorno 7), non quella del giorno della settimana:
    se la riga e' vuota, la fascia nei festivi non vale."""
    if fascia_oraria is None:
        return True
    giorno = momento.weekday()  # 0=lunedì
    if festivi is not None and momento.date() in festivi:
        giorno = 7
    ora = momento.time()
    for riga in fascia_oraria.righe:
        if riga.giorno_settimana == giorno and riga.ora_inizio <= ora <= riga.ora_fine:
            return True
    return False


# ---------- Policy applicabili ----------

def policy_della_persona(persona) -> list[tuple[Policy, str]]:
    """Tutte le policy attive della persona con l'origine: ("diretta" | nome del gruppo).
    Una stessa policy assegnata per piu' vie compare piu' volte (una per via)."""
    risultato = []
    for pol in persona.policy_dirette:
        if pol.attiva:
            risultato.append((pol, t("diretta")))
    for gruppo in persona.gruppi:
        for pol in gruppo.policies:
            if pol.attiva:
                risultato.append((pol, t("gruppo %(nome)s", nome=gruppo.nome)))
    return risultato


def esito_persona_su_varco(
    persona, varco: Varco, quando_utc: datetime, media: AccessMedia | None = None,
    festivi: Festivi | None = None,
) -> EsitoEvento:
    """
    Esito che avrebbe il passaggio di `persona` su `varco` al momento `quando_utc`
    (UTC naive), secondo la configurazione ATTUALE. Con `media` verifica anche
    quella credenziale (attiva e nel periodo di validita').
    Ordine dei controlli = quello dello slave: emergenza (porta libera per tutti),
    poi il varco, poi la credenziale, poi policy e fasce.
    """
    locale = tempo.utc_a_locale(quando_utc).replace(tzinfo=None)
    if varco.modalita == "emergenza" or (
        varco.modalita == "sblocco" and varco.sblocco_fascia is not None
        and fascia_valida_ora(varco.sblocco_fascia, locale, festivi)
    ):
        return EsitoEvento.concesso   # porta libera: nessun badge richiesto
    if not varco.attivo:
        return EsitoEvento.negato_varco_disattivato
    if varco.modalita == "manutenzione":
        return EsitoEvento.negato_varco_manutenzione
    if media is not None:
        if not media.attivo or not persona.attivo:
            return EsitoEvento.negato_credenziale_disattivata
        da, a = persona.finestra(media)
        if (da and quando_utc < da) or (a and quando_utc > a):
            return EsitoEvento.negato_credenziale_scaduta
    elif not persona.attivo:
        return EsitoEvento.negato_credenziale_disattivata
    elif (persona.valido_da and quando_utc < persona.valido_da) or (persona.valido_a and quando_utc > persona.valido_a):
        return EsitoEvento.negato_credenziale_scaduta
    pols = [p for p, _ in policy_della_persona(persona) if p.vale_su(varco.id)]
    if not pols:
        return EsitoEvento.negato_nessuna_policy
    if any(p.sempre or fascia_valida_ora(p.fascia_oraria, locale, festivi) for p in pols):
        return EsitoEvento.concesso
    return EsitoEvento.negato_fuori_fascia


# ---------- Anti-passback ----------

def _stato_dopo_transito(varco: Varco, lettore: int = 0) -> str:
    """Dove si trova la persona dopo essere passata da questo varco, da quel lettore
    (la direzione e' del lettore: un varco con un lettore per lato ha un ingresso e un'uscita)."""
    return "fuori" if funzionamento.direzione_lettore(varco, lettore) == "uscita" else "dentro"


def posizione_scaduta(varco: Varco, persona, momento: datetime) -> bool:
    """Anti-passback a tempo: se il varco ha un timeout e l'ultimo transito della
    persona e' piu' vecchio, la sua posizione non e' piu' considerata nota."""
    minuti = varco.anti_passback_timeout_min
    if not minuti or persona.passback_aggiornato_at is None:
        return False
    return (momento - persona.passback_aggiornato_at).total_seconds() > minuti * 60


def violazione_anti_passback(varco: Varco, persona, momento: datetime | None = None, lettore: int = 0) -> bool:
    """
    Violazione = la persona risulta già nello stato in cui il varco la porterebbe
    (es. entra due volte senza essere uscita: badge passato a un altro).
    Stato sconosciuto (mai transitata o resettata) = nessuna violazione.
    Con il timeout del varco (anti-passback a tempo) uno stato troppo vecchio
    conta come sconosciuto: chi ha dimenticato di strisciare in uscita non
    genera un falso allarme il giorno dopo. `momento` e' l'istante del
    transito (UTC naive); se manca, adesso.
    """
    if not varco.anti_passback_enabled or not persona.anti_passback or persona.passback_stato is None:
        return False
    if posizione_scaduta(varco, persona, momento or datetime.utcnow()):
        return False
    return persona.passback_stato == _stato_dopo_transito(varco, lettore)


def ingresso_bloccato(varco: Varco, persona, momento: datetime | None = None) -> bool:
    """
    Anti-passback «Blocca l'accesso»: la persona risulta gia' dentro, quindi i lettori di INGRESSO di questo varco
    non devono aprirle. Le uscite passano sempre. Stesso criterio della violazione (posizione scaduta = sconosciuta).
    """
    if not varco.anti_passback_enabled or varco.anti_passback_modalita != "hard" or not persona.anti_passback:
        return False
    if persona.passback_stato != "dentro" or "ingresso" not in funzionamento.direzioni(varco):
        return False
    return not posizione_scaduta(varco, persona, momento or datetime.utcnow())


SENZA_SCADENZA = 4102444800  # 2100-01-01: «x» senza timeout del varco


def blocco_ingresso_fino(varco: Varco, persona, momento: datetime | None = None) -> int:
    """
    Per la cache dello slave: 0 = nessun blocco, altrimenti l'istante unix fino al quale i lettori di ingresso
    non aprono. Con il timeout del varco e' la scadenza della posizione, cosi' lo slave la rispetta da solo.
    """
    if not ingresso_bloccato(varco, persona, momento):
        return 0
    minuti = varco.anti_passback_timeout_min
    if not minuti or persona.passback_aggiornato_at is None:
        return SENZA_SCADENZA
    return calendar.timegm(persona.passback_aggiornato_at.timetuple()) + int(minuti) * 60


def aggiorna_posizione_anti_passback(persona, varco: Varco, momento: datetime, lettore: int = 0) -> None:
    """
    Aggiorna la posizione nota della persona dopo un accesso concesso. Estratta
    a parte: serve al canale UDP (services/udp_agent.py), dove la decisione è già stata presa dal
    slave in locale, il master si limita a registrare cosa è successo, ma la
    posizione anti-passback va comunque tenuta aggiornata allo stesso modo.
    """
    persona.passback_stato = _stato_dopo_transito(varco, lettore)
    persona.passback_varco_id = varco.id
    persona.passback_aggiornato_at = momento
