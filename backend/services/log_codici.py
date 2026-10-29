# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Catalogo dei codici evento del log di sistema: per ognuno un'etichetta breve e, per chi fa
troubleshooting, che cosa significa e che cosa controllare. I codici sono stabili (si possono
citare in un ticket o filtrare nel Log); le righe di vecchie versioni possono non averne.
"""
from typing import Optional
from services.lingua import pigro, t

CODICI = {
    "DEV_ONLINE": (pigro("Dispositivo collegato"),
                   pigro("Il master ha ricevuto il primo pacchetto valido dopo un periodo di silenzio. I dettagli dicono da quanto era offline e dall'ultimo contatto.")),
    "DEV_OFFLINE": (pigro("Dispositivo scollegato"),
                    pigro("Nessun pacchetto valido oltre la soglia. Controlla alimentazione, cavo di rete e che lo slave sia acceso; se l'orologio e' sballato i suoi pacchetti vengono scartati (vedi PKT_OROLOGIO).")),
    "PKT_VARCO_IGNOTO": (pigro("Pacchetto per un varco inesistente"),
                         pigro("Arriva traffico firmato con un numero di varco che qui non esiste: crea il varco con quel numero oppure correggi l'id sullo slave.")),
    "PKT_IP_ERRATO": (pigro("Pacchetto da IP non registrato"),
                      pigro("Il master accetta i pacchetti di un varco solo dal suo IP registrato. Se lo slave ha cambiato indirizzo (DHCP) correggi l'IP in Varchi o dagli un IP fisso.")),
    "PKT_FIRMA": (pigro("Firma non valida"),
                  pigro("La chiave dello slave non coincide con quella del master (o qualcuno sta mandando pacchetti non autorizzati). Riabbina lo slave: Varchi -> Reimposta la chiave.")),
    "PKT_OROLOGIO": (pigro("Orologio dello slave fuori sincronia"),
                     pigro("Lo scarto supera la finestra di sicurezza e i pacchetti vengono scartati. Il master rimanda la cache con l'ora corretta: lo slave si riallinea da solo; se persiste controlla l'ora della scheda.")),
    "PKT_ESITO_IGNOTO": (pigro("Esito sconosciuto dallo slave"), pigro("Master e slave sono di versioni diverse: aggiorna prima il master e poi gli slave.")),
    "PKT_PORTA_IGNOTA": (pigro("Stato porta sconosciuto"), pigro("Lo slave ha mandato uno stato del sensore porta che il master non conosce: versioni diverse.")),
    "PKT_RX": (pigro("Pacchetto ricevuto"), pigro("Riga di diagnostica: un pacchetto valido ricevuto da un dispositivo (solo in modalita' diagnostica).")),
    "PKT_TX": (pigro("Pacchetto inviato"), pigro("Riga di diagnostica: un pacchetto inviato dal master a un dispositivo (solo in modalita' diagnostica).")),
    "TX_ERRORE": (pigro("Invio al dispositivo fallito"), pigro("La rete del master non ha spedito il pacchetto: controlla cavo, indirizzo IP del dispositivo e firewall.")),
    "ABB_RICHIESTA": (pigro("Richiesta di abbinamento"), pigro("Uno slave senza chiave si e' presentato: in Varchi compare «Dispositivo in attesa», un amministratore deve premere «Abbina».")),
    "ABB_VARCO_IGNOTO": (pigro("Abbinamento per un varco inesistente"), pigro("Lo slave chiede di abbinarsi con un numero di varco non presente: crealo da Varchi o correggi l'id sullo slave.")),
    "ABB_VARCO_ATTIVO": (pigro("Abbinamento ignorato"), pigro("Il varco sta gia' comunicando con la sua chiave: forse due dispositivi hanno lo stesso id.")),
    "ABB_ERRORE": (pigro("Abbinamento non riuscito"), pigro("Il master non e' riuscito a mandare la chiave allo slave: controlla la rete e riprova.")),
    "CACHE_INVIATA": (pigro("Dati inviati al varco"), pigro("Il master ha mandato la cache (credenziali, fasce, funzionamento). Attendi la conferma (CACHE_CONFERMATA).")),
    "CACHE_CONFERMATA": (pigro("Dati confermati dal varco"), pigro("Il dispositivo ha applicato la cache: ora usa i dati aggiornati.")),
    "CACHE_RICHIESTA": (pigro("Il dispositivo chiede i dati"), pigro("Di solito lo slave si e' appena avviato: il master gli manda la cache.")),
    "CACHE_ERRORE": (pigro("Invio dei dati fallito"), pigro("Il broadcast della cache non e' partito: controlla la rete del master.")),
    "SYS_AVVIO": (pigro("Master avviato"), pigro("L'applicazione e' partita; lo stato dei dispositivi si ricostruisce dai loro heartbeat.")),
    "SYS_ARRESTO": (pigro("Master in arresto"), pigro("L'applicazione si sta fermando o riavviando: i dispositivi risultano offline finche' non riparte.")),
    "AUTH_LOGIN": (pigro("Accesso alla webapp"), pigro("Un utente ha effettuato il login.")),
    "AUTH_LOGIN_KO": (pigro("Login fallito"), pigro("Credenziali errate. Molti tentativi dallo stesso indirizzo possono indicare un attacco.")),
    "AUTH_LOGOUT": (pigro("Uscita dalla webapp"), pigro("Un utente ha chiuso la sessione.")),
}


def etichetta(codice: Optional[str]) -> str:
    if not codice:
        return ""
    if codice in CODICI:
        return CODICI[codice][0]
    if codice.startswith("OP_"):
        return t("Azione di un operatore · ") + codice[3:].replace("_", " ").lower()
    return codice


def descrizione(codice: Optional[str]) -> str:
    if codice in CODICI:
        return CODICI[codice][1]
    if codice and codice.startswith("OP_"):
        return t("Modifica fatta da un utente della webapp (chi e da quale indirizzo e' indicato nella riga).")
    return ""
