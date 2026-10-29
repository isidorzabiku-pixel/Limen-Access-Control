# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Modello dati del sistema di controllo accessi.

Persona --1:N--> AccessMedia (badge/credenziali, "titoli di accesso")
Persona --N:M--> Gruppo (una persona può appartenere a più gruppi)
Persona --N:M--> Policy (regole assegnate direttamente alla singola persona)
Gruppo  --N:M--> Policy (regole assegnate a tutte le persone del gruppo)
Varco --1:N--> Evento
Varco --1:N--> RichiestaAperturaRemota (LEGACY: coda del vecchio canale HTTP, non piu' usata)
FasciaOraria --1:N--> FasciaOrariaRiga (righe giorno/ora)
Policy collega uno o piu' Varchi + una FasciaOraria (il "dove" e il "quando"); il "chi"
si assegna separatamente su Persona o Gruppo (un Gruppo e' un insieme di policy), così la stessa policy è
riusabile su più persone/gruppi senza doverla ricreare.
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Boolean, Date, DateTime, ForeignKey, Text, Time, Enum, Table
)
from sqlalchemy.orm import relationship
import enum

from database import Base
from services.lingua import t


# ---------- Utenti webapp (admin/operatori, NON le persone che accedono ai varchi) ----------

class User(Base):
    """Utente che fa login sulla webapp per amministrare il sistema."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    is_admin = Column(Boolean, default=True)
    lingua = Column(String(5), nullable=True)      # it / en; vuoto = quella di sistema
    password_predefinita = Column(Boolean, default=False, nullable=False, server_default="0")   # admin/admin del primo avvio, finché non la cambia
    created_at = Column(DateTime, default=datetime.utcnow)


# ---------- Tabelle di associazione molti-a-molti ----------

persona_gruppi = Table(
    "persona_gruppi", Base.metadata,
    Column("persona_id", Integer, ForeignKey("persone.id"), primary_key=True),
    Column("gruppo_id", Integer, ForeignKey("gruppi.id"), primary_key=True),
)

persona_policy = Table(
    "persona_policy", Base.metadata,
    Column("persona_id", Integer, ForeignKey("persone.id"), primary_key=True),
    Column("policy_id", Integer, ForeignKey("policies.id"), primary_key=True),
)

policy_varchi = Table(
    "policy_varchi", Base.metadata,
    Column("policy_id", Integer, ForeignKey("policies.id"), primary_key=True),
    Column("varco_id", Integer, ForeignKey("varchi.id"), primary_key=True),
)

gruppo_policy = Table(
    "gruppo_policy", Base.metadata,
    Column("gruppo_id", Integer, ForeignKey("gruppi.id"), primary_key=True),
    Column("policy_id", Integer, ForeignKey("policies.id"), primary_key=True),
)


# ---------- Persone e credenziali ----------

class Persona(Base):
    __tablename__ = "persone"

    id = Column(Integer, primary_key=True)
    nome = Column(String(100), nullable=False)
    cognome = Column(String(100), nullable=False)
    email = Column(String(150))
    attivo = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Ospite (visitatore): badge temporaneo, di solito con una data di scadenza.
    # Serve a distinguerlo nell'elenco e nei report dal personale stabile.
    ospite = Column(Boolean, default=False, nullable=False)
    # Validita' della persona (usata dai visitatori: «dal» facoltativo, «fino al» obbligatorio per loro). Limita
    # ogni suo badge: vale l'intervallo piu' stretto fra badge e persona.
    valido_da = Column(DateTime, nullable=True)
    valido_a = Column(DateTime, nullable=True)

    def finestra(self, media):
        """(da, a) effettivi di un badge: l'intervallo comune a quello del badge e a quello della persona (UTC naive)."""
        da = [x for x in (media.valido_da, self.valido_da) if x is not None]
        a = [x for x in (media.valido_a, self.valido_a) if x is not None]
        return (max(da) if da else None), (min(a) if a else None)

    # Anti-passback: ultima posizione nota della persona ("dentro"/"fuori", null = sconosciuta)
    passback_stato = Column(String(10), nullable=True)
    passback_varco_id = Column(Integer, nullable=True)   # da quale varco e' passata l'ultima volta (per mostrare «dentro, dal varco X»)
    # Anti-passback a due livelli: deve essere attivo sul varco E sulla persona (per le persone nuove e' spento).
    anti_passback = Column(Boolean, default=False, nullable=False)
    passback_aggiornato_at = Column(DateTime, nullable=True)

    # Presenze: dove risulta la persona secondo i soli varchi con "conta le presenze"
    # (separato dall'anti-passback, che ha le sue regole e il suo timeout).
    presenza_stato = Column(String(10), nullable=True)
    presenza_aggiornato_at = Column(DateTime, nullable=True)

    media = relationship("AccessMedia", back_populates="persona", cascade="all, delete-orphan")
    gruppi = relationship("Gruppo", secondary=persona_gruppi, back_populates="persone")
    policy_dirette = relationship("Policy", secondary=persona_policy, back_populates="persone")


class Gruppo(Base):
    """Gruppo di persone (es. 'Uffici', 'Manutenzione') per assegnare policy in blocco."""
    __tablename__ = "gruppi"

    id = Column(Integer, primary_key=True)
    nome = Column(String(100), unique=True, nullable=False)

    persone = relationship("Persona", secondary=persona_gruppi, back_populates="gruppi")
    policies = relationship("Policy", secondary=gruppo_policy, back_populates="gruppi")


class TipoMedia(str, enum.Enum):
    rfid = "rfid"   # badge (125 kHz / MIFARE)
    pin = "pin"     # numero da digitare sulla tastiera
    qr = "qr"
    uhf = "uhf"     # tag UHF
    ble = "ble"     # credenziale su telefono (Bluetooth)


class AccessMedia(Base):
    """Una credenziale fisica/logica (badge RFID, PIN, QR...) legata a una persona."""
    __tablename__ = "access_media"

    id = Column(Integer, primary_key=True)
    tipo = Column(Enum(TipoMedia), default=TipoMedia.rfid, nullable=False)
    codice = Column(String(100), unique=True, nullable=False, index=True)  # UID letto dal lettore
    attivo = Column(Boolean, default=True)
    valido_da = Column(DateTime, nullable=True)
    valido_a = Column(DateTime, nullable=True)
    persona_id = Column(Integer, ForeignKey("persone.id"), nullable=False)

    persona = relationship("Persona", back_populates="media")


# ---------- Varchi ----------

class Varco(Base):
    __tablename__ = "varchi"

    id = Column(Integer, primary_key=True)
    nome = Column(String(100), nullable=False)
    descrizione = Column(String(255))
    api_key = Column(String(64), unique=True, nullable=False)  # usata dall'agent per autenticarsi
    apertura_durata_sec = Column(Integer, default=3)

    # Verso di transito: "ingresso" porta la persona dentro, "uscita" fuori (serve all'anti-passback)
    direzione = Column(String(10), default="ingresso", nullable=False)

    # Presenze: i passaggi da questo varco contano per "chi e' dentro" e per il report.
    # Spento di default: un varco di solo ingresso non dice se la persona e' uscita.
    conta_presenze = Column(Boolean, default=False, nullable=False)

    # Anti-passback: "soft" = accesso concesso ma evento marcato allarme, "hard" = accesso negato
    anti_passback_enabled = Column(Boolean, default=False, nullable=False)
    anti_passback_modalita = Column(String(10), default="soft", nullable=False)

    # Stato del dispositivo fisico (controller/agent) che gestisce il varco
    ip_address = Column(String(45), nullable=True)  # 45 = lunghezza max IPv6
    ultimo_heartbeat = Column(DateTime, nullable=True)

    # Disattivazione manuale (solo amministratori, da /varchi): un varco
    # disattivato nega sempre l'accesso, qualunque sia la policy — serve
    # a chiudere un varco per manutenzione/emergenza senza doverne toccare
    # le policy associate. Diverso da "online/offline", che è lo stato del
    # dispositivo fisico calcolato dall'heartbeat, non una scelta manuale.
    attivo = Column(Boolean, default=True, nullable=False)

    # Anti-passback a tempo: dopo questi minuti dall'ultimo transito la posizione
    # della persona non e' piu' considerata nota (nessuna violazione). Null = non
    # scade mai. Evita che chi dimentica di strisciare in uscita resti "dentro" per sempre.
    anti_passback_timeout_min = Column(Integer, nullable=True)

    # Modalita' di funzionamento: "normale", "sblocco" (porta libera nelle fasce di
    # `sblocco_fascia_id`), "manutenzione" (nessun badge entra, resta l'apertura da
    # webapp), "emergenza" (porta libera finche' non si torna a normale).
    modalita = Column(String(20), default="normale", nullable=False)
    sblocco_fascia_id = Column(Integer, ForeignKey("fasce_orarie.id"), nullable=True)

    # Sensore porta (contatto magnetico, se cablato): ultimo stato noto
    # ("aperta"/"chiusa") e quando, riportato dallo slave con pacchetti SP.
    porta_stato = Column(String(20), nullable=True)
    porta_stato_at = Column(DateTime, nullable=True)

    # Conferma di consegna della cache UDP (vedi services/udp_agent.py):
    # "versione" è il timestamp (ms) incluso nel pacchetto cache mandato a
    # questo varco; "confermata" è l'ultima versione per cui è arrivato un
    # pacchetto CA (cache-ack) dallo slave. Se i due non coincidono, l'ultima
    # cache mandata non è (ancora, o mai stata) confermata ricevuta.
    ultima_cache_versione = Column(Integer, nullable=True)
    ultima_cache_confermata = Column(Integer, nullable=True)

    # Numero progressivo dell'ultimo evento ricevuto da questo slave (coda con
    # conferma, vedi services/udp_agent.py): serve a scartare i duplicati quando
    # uno slave ritrasmette perche' la conferma AE si e' persa.
    ultimo_evento_seq = Column(BigInteger, nullable=True)

    # Funzionamento (lettori, uscite, ingressi, avvisi): JSON, vedi services/funzionamento.py.
    # Vuoto = varco nato prima di questa funzione, si ricava un predefinito dai campi storici.
    funzionamento = Column(Text, nullable=True)
    # Stato delle uscite acceso/spento (inseritore, "altro" a interruttore): {"<indice>": bool},
    # riportato dallo slave (pacchetto SU).
    uscite_stato = Column(Text, nullable=True)
    # Abbinamento: richiesta (PR) ricevuta da uno slave senza chiave, in attesa che l'admin confermi.
    abbinamento_ip = Column(String(45), nullable=True)
    abbinamento_nonce = Column(String(32), nullable=True)
    abbinamento_at = Column(DateTime, nullable=True)

    sblocco_fascia = relationship("FasciaOraria", foreign_keys=[sblocco_fascia_id])

    eventi = relationship("Evento", back_populates="varco", passive_deletes="all")   # eliminando il varco i passaggi restano (vedi elimina_varco)
    richieste_apertura = relationship(
        "RichiestaAperturaRemota", back_populates="varco", cascade="all, delete-orphan"
    )


# ---------- Fasce orarie ----------

class FasciaOraria(Base):
    __tablename__ = "fasce_orarie"

    id = Column(Integer, primary_key=True)
    nome = Column(String(100), unique=True, nullable=False)

    # Se vero, nei giorni di festivita' (tabella `festivita`) la fascia non vale:
    # chi ha solo questa fascia non entra. Falso = le festivita' non contano.
    escludi_festivi = Column(Boolean, default=False, nullable=False)

    righe = relationship("FasciaOrariaRiga", back_populates="fascia", cascade="all, delete-orphan")


class FasciaOrariaRiga(Base):
    """Una riga: es. lunedì-venerdì 08:00-19:00."""
    __tablename__ = "fasce_orarie_righe"

    id = Column(Integer, primary_key=True)
    fascia_id = Column(Integer, ForeignKey("fasce_orarie.id"), nullable=False)
    giorno_settimana = Column(Integer, nullable=False)  # 0=lunedì ... 6=domenica, 7=giorni festivi
    ora_inizio = Column(Time, nullable=False)
    ora_fine = Column(Time, nullable=False)

    fascia = relationship("FasciaOraria", back_populates="righe")


# ---------- Policy ----------

class Policy(Base):
    """
    Combina uno o piu' Varchi e una Fascia Oraria: definisce DOVE e QUANDO è valida.
    Non indica CHI: l'assegnazione a Persone (regole dirette) o Gruppi
    avviene sulle rispettive pagine, così la stessa policy è riusabile.
    Un Gruppo e' un insieme di policy; la Persona riceve le policy dai suoi gruppi e dirette.
    """
    __tablename__ = "policies"

    id = Column(Integer, primary_key=True)
    nome = Column(String(100))
    # Fascia scelta oppure «Sempre» (24 ore su 24) esplicito. Senza nessuna delle due la regola e' INCOMPLETA
    # (es. la fascia e' stata eliminata): non concede niente finche' non la si completa o la si elimina.
    fascia_oraria_id = Column(Integer, ForeignKey("fasce_orarie.id"), nullable=True)
    sempre = Column(Boolean, default=False, nullable=False)
    attiva = Column(Boolean, default=True)

    varchi = relationship("Varco", secondary=policy_varchi, order_by="Varco.nome")
    fascia_oraria = relationship("FasciaOraria")
    gruppi = relationship("Gruppo", secondary=gruppo_policy, back_populates="policies")
    persone = relationship("Persona", secondary=persona_policy, back_populates="policy_dirette")

    @property
    def completa(self) -> bool:
        """Ha almeno un varco e una fascia (o «Sempre»): solo allora puo' concedere accessi."""
        return bool(self.varchi) and (self.sempre or self.fascia_oraria_id is not None)

    @property
    def fascia_nome(self) -> str:
        return t("Sempre") if self.sempre else (self.fascia_oraria.nome if self.fascia_oraria else t("nessuna fascia"))

    def vale_su(self, varco_id: int) -> bool:
        return self.completa and any(v.id == varco_id for v in self.varchi)

    @property
    def nomi_varchi(self) -> str:
        return ", ".join(v.nome for v in self.varchi) or t("nessun varco")


# ---------- Log eventi ----------

class EsitoEvento(str, enum.Enum):
    concesso = "concesso"
    negato_credenziale_sconosciuta = "negato_credenziale_sconosciuta"
    negato_fuori_fascia = "negato_fuori_fascia"
    negato_nessuna_policy = "negato_nessuna_policy"
    negato_credenziale_disattivata = "negato_credenziale_disattivata"
    negato_anti_passback = "negato_anti_passback"
    negato_varco_disattivato = "negato_varco_disattivato"
    negato_credenziale_scaduta = "negato_credenziale_scaduta"
    negato_varco_manutenzione = "negato_varco_manutenzione"
    # Motivi precisi di una credenziale che lo slave dice «sconosciuta» ma il master conosce
    # (vedi services/motivi.py). Nomi entro 30 caratteri, come gli altri.
    negato_varco_non_autorizzato = "negato_varco_non_autorizzato"
    negato_policy_disattivata = "negato_policy_disattivata"
    negato_dati_non_aggiornati = "negato_dati_non_aggiornati"
    porta_forzata = "porta_forzata"
    porta_lasciata_aperta = "porta_lasciata_aperta"
    apertura_remota = "apertura_remota"
    apertura_comando_esterno = "apertura_comando_esterno"
    ingresso_attivato = "ingresso_attivato"
    uscita_azionata = "uscita_azionata"


class Evento(Base):
    __tablename__ = "eventi"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    varco_id = Column(Integer, ForeignKey("varchi.id"), nullable=False)
    media_codice = Column(String(100), nullable=True)
    persona_id = Column(Integer, ForeignKey("persone.id"), nullable=True)
    esito = Column(Enum(EsitoEvento), nullable=False)
    allarme = Column(Boolean, default=False, nullable=False, index=True)  # es. violazione anti-passback
    # Dettaglio libero e breve: quale lettore (se il varco ne ha piu' d'uno), nome dell'ingresso o dell'uscita.
    dettaglio = Column(String(120), nullable=True)
    # Presa in carico dell'allarme (pagina Alert): finché è vuoto l'allarme
    # resta "da gestire"; una volta valorizzato esce dall'elenco attivo.
    gestito_da = Column(String(64), nullable=True)
    gestito_at = Column(DateTime, nullable=True)

    # Nome del varco al momento dell'eliminazione: i passaggi restano nel registro anche se il varco non esiste piu'.
    varco_nome = Column(String(100), nullable=True)

    varco = relationship("Varco", back_populates="eventi")
    persona = relationship("Persona")


# ---------- Aperture remote ----------

class RichiestaAperturaRemota(Base):
    """
    LEGACY. Coda del vecchio canale HTTP (polling su /api/pending-open), rimosso:
    ora l'apertura remota passa solo dall'UDP. La tabella resta per non perdere
    lo storico di database esistenti; nessun codice la scrive piu'.
    """
    __tablename__ = "richieste_apertura_remota"

    id = Column(Integer, primary_key=True)
    varco_id = Column(Integer, ForeignKey("varchi.id"), nullable=False, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    consumata = Column(Boolean, default=False, nullable=False)
    consumata_at = Column(DateTime, nullable=True)
    origine = Column(String(100))  # es. "webapp:admin"

    varco = relationship("Varco", back_populates="richieste_apertura")


# ---------- Log di sistema (audit azioni operatore + eventi applicativi) ----------

class LivelloLog(str, enum.Enum):
    debug = "debug"      # solo in modalita' diagnostica (un riga per pacchetto): si cancellano da sole
    info = "info"
    warning = "warning"
    error = "error"


class LogSistema(Base):
    """Log generico del sistema: azioni degli operatori sulla webapp (creazioni,
    modifiche, login/logout) ed eventi applicativi non legati a un singolo varco.
    Distinto da Evento, che registra solo i passaggi/validazioni ai varchi."""
    __tablename__ = "log_sistema"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    livello = Column(Enum(LivelloLog), default=LivelloLog.info, nullable=False, index=True)
    categoria = Column(String(50), nullable=True)
    messaggio = Column(String(500), nullable=False)
    # Varco a cui si riferisce la riga (connessioni, pacchetti scartati, cache...):
    # senza vincolo, cosi' lo storico di un varco eliminato resta leggibile.
    varco_id = Column(Integer, nullable=True, index=True)
    # Per il troubleshooting: codice stabile dell'evento (es. DEV_ONLINE, PKT_FIRMA), chi l'ha causato
    # (utente della webapp e indirizzo da cui e' arrivata la richiesta) e i dati strutturati (JSON).
    codice = Column(String(40), nullable=True, index=True)
    utente = Column(String(80), nullable=True)
    origine_ip = Column(String(45), nullable=True)
    dettagli = Column(Text, nullable=True)


# ---------- Impostazioni e festivita' ----------

class Impostazione(Base):
    """Impostazioni di sistema modificabili dalla webapp (chiave/valore testuale)."""
    __tablename__ = "impostazioni"

    chiave = Column(String(64), primary_key=True)
    valore = Column(String(255), nullable=False)


class Festivita(Base):
    """Giorno festivo (valido per le fasce con `escludi_festivi`). `ricorrente`: si
    ripete ogni anno nella stessa data (Natale), altrimenti vale solo per quell'anno (Pasquetta)."""
    __tablename__ = "festivita"

    id = Column(Integer, primary_key=True)
    data = Column(Date, nullable=False, index=True)
    nome = Column(String(100), nullable=False)
    ricorrente = Column(Boolean, default=False, nullable=False)


class CredenzialeIgnorata(Base):
    """Credenziale letta e non riconosciuta che l'utente ha scelto di non assegnare («Ignora»):
    sparisce dall'elenco delle sconosciute finche' non viene letta di nuovo."""
    __tablename__ = "credenziali_ignorate"

    codice = Column(String(100), primary_key=True)
    ignorata_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ---------- API in ingresso ----------

class ApiAccount(Base):
    """Account per i programmi esterni che usano l'API (/api/v1). Password solo come hash bcrypt."""
    __tablename__ = "api_accounts"

    id = Column(Integer, primary_key=True)
    nome = Column(String(80), nullable=False)                 # a cosa serve (es. «Portineria»)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    attivo = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    ultimo_uso = Column(DateTime, nullable=True)

    tokens = relationship("ApiToken", back_populates="account", cascade="all, delete-orphan")


class ApiToken(Base):
    """Token di accesso (valido 48 ore). Nel database c'è solo l'impronta SHA-256: il token in chiaro esiste solo nella risposta al login."""
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey("api_accounts.id"), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    scade_at = Column(DateTime, nullable=False)

    account = relationship("ApiAccount", back_populates="tokens")
