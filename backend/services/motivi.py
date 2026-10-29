# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Motivo preciso di un accesso negato.

Lo slave decide in locale con la cache che ha, e la cache contiene SOLO le credenziali che
hanno almeno una policy attiva su quel varco (e un badge valido): tutto il resto, per lui,
e' "credenziale sconosciuta" anche quando il badge e' di una persona ben nota. Il master
conosce l'intera configurazione, quindi qui, quando riceve l'evento, trasforma quel generico
«non riconosciuta» nel motivo vero e lo scrive nell'evento (esito + dettaglio), cosi' resta
nello storico anche se poi la configurazione cambia:

  - persona sospesa / badge disattivato / badge scaduto
  - nessuna regola: non e' in nessun gruppo e non ha permessi diretti (o i suoi gruppi sono vuoti)
  - non autorizzata su questo varco: ha regole, ma per altri varchi
  - regola disattivata: la regola per questo varco c'e' ma e' spenta
  - dati non aggiornati: ha un permesso valido, ma lo slave non lo sa ancora («Scarica dati»)

Funziona anche con gli slave gia' installati: non cambia il protocollo.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from models import AccessMedia, EsitoEvento, Persona, Varco
from services import modifiche, tempo
from services.lingua import t
from services.permessi import descrivi_fasce

MAX_DETTAGLIO = 120


def _nome(policy) -> str:
    return policy.nome or t("Regola #%(id)s", id=policy.id)


def _elenco(nomi, massimo: int = 3) -> str:
    nomi = list(dict.fromkeys(nomi))
    if len(nomi) <= massimo:
        return ", ".join(nomi)
    return ", ".join(nomi[:massimo]) + t(" e altri %(p)s", p=len(nomi) - massimo)


def tronca(testo: Optional[str]) -> Optional[str]:
    if not testo:
        return None
    return testo if len(testo) <= MAX_DETTAGLIO else testo[: MAX_DETTAGLIO - 1] + "…"


def policy_con_origine(persona: Persona) -> list[tuple]:
    """Tutte le policy della persona (anche disattivate) con l'origine: 'diretta' o 'gruppo X'."""
    out = [(p, t("diretta")) for p in persona.policy_dirette]
    for g in persona.gruppi:
        out.extend((p, t("gruppo %(nome)s", nome=g.nome)) for p in g.policies)
    return out


def raffina_sconosciuta(db: Session, varco: Varco, media: AccessMedia, istante: datetime) -> tuple[EsitoEvento, str]:
    """Lo slave ha detto «credenziale sconosciuta» per un badge che il master conosce:
    ritorna (esito, dettaglio) con il motivo vero."""
    persona = media.persona
    if not persona.attivo:
        return EsitoEvento.negato_credenziale_disattivata, t("Persona sospesa")
    if not media.attivo:
        return EsitoEvento.negato_credenziale_disattivata, t("Badge disattivato")
    da, a = persona.finestra(media)
    if a and istante > a:
        return EsitoEvento.negato_credenziale_scaduta, t("Badge scaduto il %(p)s", p=tempo.formatta(a, '%d/%m/%Y %H:%M'))
    if da and istante < da:
        return EsitoEvento.negato_credenziale_scaduta, t("Badge valido solo dal %(p)s", p=tempo.formatta(da, '%d/%m/%Y %H:%M'))

    tutte = policy_con_origine(persona)
    if not tutte:
        if persona.gruppi:
            return EsitoEvento.negato_nessuna_policy, tronca(
                t("I suoi gruppi (%(p)s) non hanno nessuna regola; nessun permesso diretto", p=_elenco(g.nome for g in persona.gruppi)))
        return EsitoEvento.negato_nessuna_policy, t("Non e' in nessun gruppo e non ha permessi diretti")

    qui = [(p, o) for p, o in tutte if p.vale_su(varco.id)]
    if not qui:
        altri = sorted({v.nome for p, _ in tutte if p.attiva for v in p.varchi})
        if altri:
            return EsitoEvento.negato_varco_non_autorizzato, tronca(
                t("Non ha nessuna regola per questo varco; ha accesso solo a: %(p)s", p=_elenco(altri)))
        return EsitoEvento.negato_policy_disattivata, t("Le regole che ha assegnate sono tutte disattivate")
    if not any(p.attiva for p, _ in qui):
        p, o = qui[0]
        return EsitoEvento.negato_policy_disattivata, tronca(t("La regola «%(p)s» (%(o)s) per questo varco e' disattivata", p=_nome(p), o=o))

    # Ha un permesso attivo qui, eppure lo slave non lo conosce: cache vecchia o non applicata.
    if varco.nome in modifiche.stato_dati(db)["da_inviare"]:
        return EsitoEvento.negato_dati_non_aggiornati, t("Ha un permesso valido, ma i dati di questo varco non sono aggiornati: premi «Scarica dati»")
    return EsitoEvento.negato_dati_non_aggiornati, t("Ha un permesso valido, ma il dispositivo non lo conosce (cache non applicata?): premi «Scarica dati»")


def dettaglio_esito(persona: Persona, varco: Varco, media: Optional[AccessMedia], esito: EsitoEvento) -> Optional[str]:
    """Dettaglio da scrivere per gli esiti che lo slave riporta correttamente ma senza spiegare."""
    if esito == EsitoEvento.negato_fuori_fascia:
        qui = [(p, o) for p, o in policy_con_origine(persona) if p.vale_su(varco.id) and p.attiva]
        if not qui:
            return None
        parti = []
        for p, o in qui[:2]:
            fascia = p.fascia_oraria
            parti.append(t("«%(nome)s»: %(p)s", nome=fascia.nome, p=descrivi_fasce(fascia.righe)) if fascia else t("sempre"))
        extra = t(" (+%(p)s)", p=len(qui) - 2) if len(qui) > 2 else ""
        return tronca(t("Consentito solo %(parti)s%(extra)s", parti="; ".join(parti), extra=extra))
    if esito == EsitoEvento.negato_anti_passback:
        return t("Anti-passback: risulta già dentro")
    if esito == EsitoEvento.negato_credenziale_scaduta and media is not None:
        da, a = persona.finestra(media)
        if a:
            return t("Badge valido fino al %(p)s", p=tempo.formatta(a, '%d/%m/%Y %H:%M'))
        if da:
            return t("Badge valido dal %(p)s", p=tempo.formatta(da, '%d/%m/%Y %H:%M'))
    if esito == EsitoEvento.negato_credenziale_disattivata:
        if not persona.attivo:
            return t("Persona sospesa")
        if media is not None and not media.attivo:
            return t("Badge disattivato")
    return None
