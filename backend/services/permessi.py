# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Permessi effettivi di una persona e spiegazione di un passaggio negato.

Tutto calcolato dalla configurazione ATTUALE (gruppi, policy, fasce, festivita',
validita' dei badge): serve a rispondere a "dove puo' entrare Mario e quando?" e
"perche' ieri sera non e' entrato?". Se la configurazione e' cambiata dopo
l'evento, la spiegazione lo dice.
"""
from services.lingua import pigro, t
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from models import AccessMedia, EsitoEvento, Evento, Persona, Varco
from services import tempo
from services.policy_engine import (
    Festivi, carica_festivi, esito_persona_su_varco, fascia_valida_ora, policy_della_persona,
)

GIORNI_BREVI = [pigro(g) for g in ("Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom")]


def _hhmm(t) -> str:
    return t.strftime("%H:%M")


def _intervalli_per_giorno(righe) -> dict[int, list[tuple[str, str]]]:
    per_giorno: dict[int, list[tuple]] = {g: [] for g in range(8)}   # 7 = giorni festivi
    for r in righe:
        per_giorno[r.giorno_settimana].append((r.ora_inizio, r.ora_fine))
    risultato = {}
    for g, lista in per_giorno.items():
        unito = []
        for ini, fine in sorted(lista):
            if unito and ini <= unito[-1][1]:
                unito[-1] = (unito[-1][0], max(unito[-1][1], fine))
            else:
                unito.append((ini, fine))
        risultato[g] = [(_hhmm(i), _hhmm(f)) for i, f in unito]
    return risultato


def descrivi_fasce(righe) -> str:
    """'Lun-Ven 08:00-19:00; Sab 08:00-13:00' oppure 'Sempre' se le righe coprono tutto."""
    per_giorno = _intervalli_per_giorno(righe)
    if not any(per_giorno.values()):
        return t("Mai (fascia vuota)")
    if all(per_giorno[g] == [("00:00", "23:59")] for g in range(8)):
        return t("Sempre")
    gruppi = []   # (giorno_inizio, giorno_fine, intervalli)
    for g in range(7):
        if not per_giorno[g]:
            continue
        if gruppi and gruppi[-1][1] == g - 1 and gruppi[-1][2] == per_giorno[g]:
            gruppi[-1][1] = g
        else:
            gruppi.append([g, g, per_giorno[g]])
    parti = []
    for a, b, intervalli in gruppi:
        giorni = GIORNI_BREVI[a] if a == b else f"{GIORNI_BREVI[a]}-{GIORNI_BREVI[b]}"
        parti.append(giorni + " " + ", ".join(f"{i}-{f}" for i, f in intervalli))
    if per_giorno[7]:
        parti.append(t("Festivi ") + ", ".join(t("%(i)s-%(f)s", i=i, f=f) for i, f in per_giorno[7]))
    else:
        parti.append(t("non nei festivi"))
    return "; ".join(parti)


def _prossima_apertura(pols, dopo_utc: datetime, festivi: Festivi) -> datetime | None:
    """Primo istante (UTC naive) nei prossimi 8 giorni in cui una policy della lista si apre."""
    locale = tempo.utc_a_locale(dopo_utc).replace(tzinfo=None)
    candidati = []
    for giorni in range(0, 9):
        giorno = (locale + timedelta(days=giorni)).date()
        for pol in pols:
            if not pol.completa:
                continue
            if pol.sempre:
                return None   # sempre valida: gia' aperto, non c'e' una "prossima"
            fascia = pol.fascia_oraria
            giorno_riga = 7 if giorno in festivi else giorno.weekday()   # nei festivi vale la riga «Festivi» della griglia
            for r in fascia.righe:
                if r.giorno_settimana != giorno_riga:
                    continue
                inizio = datetime.combine(giorno, r.ora_inizio)
                if inizio > locale:
                    candidati.append(inizio)
    if not candidati:
        return None
    return tempo.locale_a_utc(min(candidati))


MOTIVI = {
    EsitoEvento.concesso: pigro("Puo' entrare"),
    EsitoEvento.negato_fuori_fascia: pigro("Fuori dalla fascia oraria"),
    EsitoEvento.negato_nessuna_policy: pigro("Nessuna regola assegnata"),
    EsitoEvento.negato_varco_non_autorizzato: pigro("Non autorizzata su questo varco"),
    EsitoEvento.negato_policy_disattivata: pigro("Regola disattivata"),
    EsitoEvento.negato_dati_non_aggiornati: pigro("Dati del varco non aggiornati"),
    EsitoEvento.negato_credenziale_disattivata: pigro("Persona sospesa o badge disattivato"),
    EsitoEvento.negato_credenziale_scaduta: pigro("Badge scaduto o non ancora valido"),
    EsitoEvento.negato_varco_disattivato: pigro("Varco disattivato"),
    EsitoEvento.negato_varco_manutenzione: pigro("Varco in manutenzione"),
    EsitoEvento.negato_anti_passback: pigro("Anti-passback"),
    EsitoEvento.negato_credenziale_sconosciuta: pigro("Credenziale sconosciuta"),
}


def permessi_persona(db: Session, persona: Persona, quando_utc: datetime | None = None) -> list[dict]:
    """Per ogni varco: se la persona puo' entrare a `quando_utc` (default adesso), perche',
    attraverso quali policy/gruppi e con quali orari."""
    quando_utc = quando_utc or datetime.utcnow()
    festivi = carica_festivi(db)
    badge_ok = [m for m in persona.media if m.attivo]
    per_varco: dict[int, list] = {}
    for pol, origine in policy_della_persona(persona):
        for v in pol.varchi:
            per_varco.setdefault(v.id, []).append((pol, origine))

    risultato = []
    for varco in db.query(Varco).order_by(Varco.nome).all():
        voci = per_varco.get(varco.id, [])
        # La persona entra se ALMENO UN badge supera i controlli; senza badge, si valuta come persona.
        if badge_ok:
            esiti = [esito_persona_su_varco(persona, varco, quando_utc, m, festivi) for m in badge_ok]
            esito = EsitoEvento.concesso if EsitoEvento.concesso in esiti else esiti[0]
        else:
            esito = esito_persona_su_varco(persona, varco, quando_utc, None, festivi)
        origini = []
        for pol, origine in voci:
            fascia = pol.fascia_oraria
            origini.append({
                "policy": pol.nome or t("Regola #%(id)s", id=pol.id),
                "origine": origine,
                "fascia": pol.fascia_nome,
                "orari": t("Sempre") if pol.sempre else (descrivi_fasce(fascia.righe) if fascia else t("Regola incompleta: non concede accesso")),
            })
        riga = {
            "varco": varco, "esito": esito, "consentito": esito == EsitoEvento.concesso,
            "motivo": MOTIVI.get(esito, esito.value), "origini": origini, "prossima_apertura": None,
        }
        if esito == EsitoEvento.negato_fuori_fascia:
            riga["prossima_apertura"] = _prossima_apertura([p for p, _ in voci], quando_utc, festivi)
        risultato.append(riga)
    return risultato


def spiega_evento(db: Session, evento: Evento) -> dict:
    """
    Spiegazione in italiano di un esito: {"titolo", "righe": [...], "suggerimento", "persona_id", "codice_da_assegnare"}.
    Le righe descrivono lo stato ATTUALE della configurazione; se e' cambiata, e' scritto.
    """
    esito = evento.esito
    persona = evento.persona
    varco = evento.varco
    quando_locale = tempo.utc_a_locale(evento.timestamp)
    quando_txt = t("%(giorno)s %(data)s alle %(ora)s", giorno=GIORNI_BREVI[quando_locale.weekday()],
                   data=quando_locale.strftime("%d/%m"), ora=quando_locale.strftime("%H:%M"))
    out = {"titolo": "", "righe": [], "suggerimento": "", "persona_id": persona.id if persona else None,
           "codice_da_assegnare": None}
    nome_persona = f"{persona.nome} {persona.cognome}" if persona else t("la persona")
    nome_varco = varco.nome if varco else t("il varco")

    if esito == EsitoEvento.concesso:
        out["titolo"] = t("Accesso consentito")
        out["righe"].append(t("%(nome_persona)s aveva un permesso valido su %(nome_varco)s %(quando_txt)s.", nome_persona=nome_persona, nome_varco=nome_varco, quando_txt=quando_txt))
        if evento.allarme:
            out["righe"].append(t("E' segnato come allarme: risultava gia' nella posizione in cui il varco la porta (anti-passback)."))
        return out
    if esito == EsitoEvento.apertura_remota:
        out["titolo"] = t("Apertura remota")
        out["righe"].append(t("%(nome_varco)s e' stato aperto dalla webapp, non con un badge.", nome_varco=nome_varco))
        return out
    if esito == EsitoEvento.apertura_comando_esterno:
        out["titolo"] = t("Apertura da comando esterno")
        out["righe"].append(t("%(nome_varco)s e' stato aperto da un pulsante o un telecomando collegato al dispositivo (%(p)s), non con un badge.", nome_varco=nome_varco, p=evento.dettaglio or 'ingresso'))
        return out
    if esito == EsitoEvento.ingresso_attivato:
        out["titolo"] = t("Ingresso attivato")
        out["righe"].append(t("Su %(nome_varco)s si e' attivato l'ingresso «%(p)s».", nome_varco=nome_varco, p=evento.dettaglio or 'Altro'))
        return out
    if esito == EsitoEvento.uscita_azionata:
        out["titolo"] = t("Uscita azionata")
        out["righe"].append(t("Dalla webapp e' stata azionata un'uscita di %(nome_varco)s: %(p)s.", nome_varco=nome_varco, p=evento.dettaglio or t('uscita')))
        return out
    if esito in (EsitoEvento.porta_forzata, EsitoEvento.porta_lasciata_aperta):
        out["titolo"] = t("Porta forzata") if esito == EsitoEvento.porta_forzata else t("Porta lasciata aperta")
        out["righe"].append(
            t("Il sensore della porta ha segnalato l'apertura senza un accesso valido.") if esito == EsitoEvento.porta_forzata
            else t("Il sensore della porta indica che e' rimasta aperta oltre il tempo consentito.")
        )
        out["suggerimento"] = t("Controlla il varco e prendi in carico l'allarme dalla pagina Alert.")
        return out

    out["titolo"] = MOTIVI.get(esito, esito.value)

    if esito == EsitoEvento.negato_credenziale_sconosciuta:
        esiste = (db.query(AccessMedia).filter(AccessMedia.codice == evento.media_codice).first()
                  if evento.media_codice else None)
        if esiste is None:
            out["righe"].append(t("Il codice %(p)s non era associato a nessuna persona.", p=evento.media_codice or t('(non letto)')))
        if evento.media_codice:
            if esiste is not None:
                p = esiste.persona
                out["righe"].append(
                    t("Oggi il codice appartiene a %(nome)s %(cognome)s. Il dispositivo non lo riconosceva: o non era ancora assegnato, oppure %(nome2)s non aveva nessuna regola attiva su %(nome_varco)s (lo slave riceve solo i badge autorizzati su quel varco).", nome=p.nome, cognome=p.cognome, nome2=p.nome, nome_varco=nome_varco)
                )
                riga_oggi = next((r for r in permessi_persona(db, p) if varco and r["varco"].id == varco.id), None)
                if riga_oggi is not None:
                    out["righe"].append(t("Oggi, con la configurazione attuale: %(motivo)s.", motivo=str(riga_oggi["motivo"]).lower()))
                    if not riga_oggi["consentito"]:
                        out["suggerimento"] = t("Controlla gruppi e regole della persona.")
                out["persona_id"] = p.id
            else:
                out["codice_da_assegnare"] = evento.media_codice
                out["suggerimento"] = t("Se e' un badge da abilitare, assegnalo a una persona.")
        return out

    if esito == EsitoEvento.negato_varco_disattivato:
        out["righe"].append(t("%(nome_varco)s era disattivato: nega tutti, qualunque policy.", nome_varco=nome_varco))
        out["righe"].append(t("Oggi il varco e' ancora disattivato.") if varco and not varco.attivo else t("Oggi il varco e' attivo."))
        return out
    if esito == EsitoEvento.negato_varco_manutenzione:
        out["righe"].append(t("%(nome_varco)s era in modalita' manutenzione: nessun badge apre, resta possibile l'apertura dalla webapp.", nome_varco=nome_varco))
        return out
    if esito == EsitoEvento.negato_anti_passback:
        out["righe"].append(t("%(nome_persona)s risultava gia' dentro (o gia' fuori): ha passato il badge due volte nello stesso verso senza passare dal varco opposto.", nome_persona=nome_persona))
        out["suggerimento"] = t("Se la posizione e' sbagliata, usa \"Reset anti-passback\" nella scheda della persona.")
        return out

    if persona is None:
        out["righe"].append(t("La credenziale non risulta associata a nessuna persona."))
        return out

    # Da qui: esiti che dipendono dalla persona. Si descrive la situazione di oggi.
    oggi = permessi_persona(db, persona)
    riga = next((r for r in oggi if varco and r["varco"].id == varco.id), None)
    media = next((m for m in persona.media if m.codice == evento.media_codice), None)

    if esito == EsitoEvento.negato_credenziale_disattivata:
        if not persona.attivo:
            out["righe"].append(t("%(nome_persona)s risulta sospesa.", nome_persona=nome_persona))
            out["suggerimento"] = t("Riattivala dalla sua scheda in Persone e poi usa \"Scarica dati\".")
        elif media is not None and not media.attivo:
            out["righe"].append(t("Il badge %(codice)s e' disattivato.", codice=media.codice))
            out["suggerimento"] = t("Riattivalo dalla scheda della persona e poi usa \"Scarica dati\".")
        else:
            out["righe"].append(t("Il badge o la persona erano disattivati al momento del passaggio; oggi risultano attivi."))
        return out
    if esito == EsitoEvento.negato_credenziale_scaduta:
        da_e, a_e = persona.finestra(media) if media is not None else (None, None)
        if media is not None and (da_e or a_e):
            if da_e:
                out["righe"].append(t("Il badge vale dal %(p)s.", p=tempo.formatta(da_e, '%d/%m/%Y %H:%M')))
            if a_e:
                out["righe"].append(t("Il badge vale fino al %(p)s.", p=tempo.formatta(a_e, '%d/%m/%Y %H:%M')))
            out["suggerimento"] = t("Per prorogarlo modifica la validita' del badge nella scheda della persona.")
        else:
            out["righe"].append(t("Il badge era fuori dal suo periodo di validita'; oggi non ha limiti."))
        return out

    if esito in (EsitoEvento.negato_varco_non_autorizzato, EsitoEvento.negato_policy_disattivata, EsitoEvento.negato_dati_non_aggiornati):
        if evento.dettaglio:
            out["righe"].append(evento.dettaglio + ".")
        if esito == EsitoEvento.negato_varco_non_autorizzato:
            out["righe"].append(t("%(nome_persona)s ha delle regole, ma nessuna vale per %(nome_varco)s.", nome_persona=nome_persona, nome_varco=nome_varco))
            out["suggerimento"] = t("Per farla entrare assegna alla persona (o a un suo gruppo) una regola per %(nome_varco)s, poi usa \"Scarica dati\".", nome_varco=nome_varco)
        elif esito == EsitoEvento.negato_policy_disattivata:
            out["righe"].append(t("Una regola con quel varco esiste ma e' spenta, quindi non vale per nessuno."))
            out["suggerimento"] = t("Riattivala dalla pagina Regole (pulsante Modifica) e poi usa \"Scarica dati\".")
        else:
            out["righe"].append(t("Il permesso c'era nella webapp, ma il dispositivo non aveva ancora i dati aggiornati."))
            out["suggerimento"] = t("Usa \"Scarica dati ai dispositivi\" dopo ogni modifica a persone, gruppi o regole.")
        if riga is not None and riga["consentito"]:
            out["righe"].append(t("Oggi, con i dati attuali, passerebbe."))
        return out
    if riga is None:
        return out
    if esito == EsitoEvento.negato_nessuna_policy:
        if riga["origini"]:
            out["righe"].append(t("Oggi %(nome_persona)s ha una policy su %(nome_varco)s (la configurazione e' cambiata dopo l'evento).", nome_persona=nome_persona, nome_varco=nome_varco))
        else:
            out["righe"].append(t("%(nome_persona)s non ha nessuna policy attiva su %(nome_varco)s, ne' diretta ne' tramite i suoi gruppi.", nome_persona=nome_persona, nome_varco=nome_varco))
            out["suggerimento"] = t("Assegna alla persona (o a un suo gruppo) una policy per questo varco.")
        return out
    if esito == EsitoEvento.negato_fuori_fascia:
        out["righe"].append(t("Il passaggio e' avvenuto %(quando_txt)s, fuori dalle fasce consentite.", quando_txt=quando_txt))
        for o in riga["origini"]:
            out["righe"].append(f"{o['policy']} [{o['origine']}] — {o['fascia']}: {o['orari']}")
        if not riga["origini"]:
            out["righe"].append(t("Oggi non ha piu' policy su questo varco."))
        out["suggerimento"] = t("Per consentire quell'orario modifica la fascia oraria o assegna una policy piu' ampia.")
        return out
    return out
