# Limen

[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE) ![Status: early](https://img.shields.io/badge/status-early%20stage-orange) ![Docker](https://img.shields.io/badge/docker-ready-2496ED)

🇬🇧 [Read in English](README.md)

**Controllo accessi open source, pensato per rendere la gestione delle porte di livello enterprise facile, veloce e accessibile.**

Limen è un sistema di controllo accessi leggero e self-hosted. Una web app (il *master*) gestisce persone, credenziali,
varchi, fasce orarie e regole di accesso; piccoli dispositivi alle porte (gli *slave*, schede ESP32) decidono in locale,
in pochi millisecondi, anche quando il master o la rete non funzionano. Niente cloud, niente abbonamenti, nessun vincolo
con un fornitore: un container su una macchina che hai già.

> **Stato:** fase iniziale, in sviluppo attivo. Il master (web app, protocollo, API) è completo per l'uso quotidiano e
> coperto da test automatici eseguiti durante lo sviluppo. La logica del firmware ESP32 è provata contro il master vero su
> PC; **il collaudo su schede reali è ancora in corso**. Provalo su un banco di prova prima di affidargli una porta vera, e
> non farci mai affidamento come unica via di uscita d'emergenza.

## Schermate

| | |
|---|---|
| ![Dashboard](docs/img/screenshot-dashboard.jpg) | ![Monitoraggio in diretta](docs/img/screenshot-monitoraggio.jpg) |
| ![Pannello del varco](docs/img/screenshot-varco.jpg) | ![Persona: gruppi e regole](docs/img/screenshot-persona.jpg) |

## Punti di forza

- **Decisioni in locale.** Ogni varco tiene una cache delle credenziali ammesse e decide sul posto. L'apertura da remoto è
  un comando del master, mai una richiesta di permesso al master.
- **Protocollo semplice e firmato.** Master e slave parlano solo UDP (porta 57990), unicast, con pacchetti firmati
  HMAC-SHA256, controllo della finestra anti-replay, conferme di consegna e una coda eventi persistente sul dispositivo:
  un master spento o un pacchetto perso non fanno mai perdere un passaggio.
- **Una web app completa.** Persone, gruppi, visitatori con scadenza, badge, PIN e altre credenziali, regole di accesso,
  fasce orarie con festività, monitoraggio in diretta, alert, anti-passback, «chi è dentro adesso», import/export CSV,
  il motivo spiegato di ogni accesso negato, stato del sistema, backup e un log diagnostico dettagliato.
- **Comportamento per varco.** Lettori, uscite (serratura, sirena, inserimento allarme) e ingressi (sensore porta,
  pulsante, telecomando) si configurano dalla web app; modalità: normale, sblocco programmato, manutenzione, emergenza.
- **API REST pubblica** con autenticazione a token e documentazione in PDF (vedi `api/`).
- **HTTPS ovunque** (certificato autofirmato creato al primo avvio, oppure usa il tuo).
- **Interfaccia in italiano e inglese**; ogni utente sceglie la propria lingua, e il **manuale d'uso** (PDF) si scarica
  dall'app, nella tua lingua.
- **Gira ovunque.** Server Linux, mini PC, Raspberry Pi, una VM, Docker. SQLite, nessun servizio esterno. La web app non
  carica nulla da internet.

## Come funziona

```
  browser ──HTTPS──►  MASTER (FastAPI + SQLite)  ◄──UDP 57990, firmato──►  SLAVE (ESP32 alla porta)
                       persone · regole · log · API                         cache · decisione locale · relè · ingressi
```

Il master costruisce, per ogni varco, una cache compatta delle credenziali che hanno una regola valida su quel varco e la
invia al dispositivo. Il dispositivo controlla ogni badge con quella cache e il suo orologio, comanda il relè e segnala
l'evento dopo. Gli indirizzi IP dei dispositivi sono fissi e scritti solo sul master, che contatta per primo ogni
dispositivo; i dispositivi non fanno mai broadcast.
Dettagli (in inglese): [`docs/FEATURES.md`](docs/FEATURES.md) e [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Avvio rapido (Docker, Linux)

```bash
git clone https://github.com/isidorzabiku-pixel/Limen-Access-Control.git && cd Limen-Access-Control
docker compose up -d --build
```

Apri **https://&lt;ip-del-server&gt;:8443** (il browser avvisa una volta del certificato autofirmato) e accedi con
**admin / admin**. L'app mostra un avviso finché non cambi quella password in *Proprietà di sistema*.

Il file compose usa la rete dell'host, così il master raggiunge i dispositivi con l'indirizzo reale del server. Database,
certificati e chiave di sessione stanno nel volume `limen-data` e sopravvivono agli aggiornamenti
(`git pull && docker compose up -d --build`). Altro in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

**Senza Docker:** `cd backend && python3 -m venv venv && . venv/bin/activate && pip install -r requirements.txt && ./avvia.sh`
(un'unità systemd è in `deploy/limen.service`).
**Windows / macOS:** esegui direttamente con Python (la rete host di Docker Desktop non è affidabile per l'UDP); un
installer pronto è in programma.

### Primi passi

1. *Varchi* → crea un varco (nome, numero, IP del dispositivo).
2. Programma l'ESP32 (sotto), dagli lo stesso numero di varco e un IP fisso; quando compare nella pagina *Varchi* premi **Abbina**.
3. *Persone* → aggiungi una persona e un badge. *Regole di accesso* → collega la persona (o un gruppo) al varco con una fascia oraria.
4. Premi **Scarica dati ai dispositivi** per mandare la prima cache (parte anche in automatico ogni 6 ore).

## Slave (i dispositivi alla porta)

- **Firmware ESP32** — [`firmware/esp32/`](firmware/esp32/README.md) (in inglese). Nucleo C++ senza dipendenze da Arduino
  (decisione, cache, protocollo, HMAC-SHA256) più il livello per la piattaforma ESP32. Console di prova nella pagina web
  del dispositivo finché non è collegato un lettore. Due varianti hardware condividono lo stesso nucleo: una scheda con
  lettore RS-485 e un lettore PoE tutto-in-uno. L'interfaccia del dispositivo è solo in inglese.
- **Agent Python (versione di prova)** — [`agent/`](agent/INSTALL_PI.md). Un'implementazione per Raspberry Pi usata per
  provare il protocollo e il cablaggio dei GPIO. **Non è mantenuta** e non è lo slave supportato.

## Note di sicurezza

Limen è pensato per una rete chiusa: tieni i dispositivi su una VLAN dedicata e non esporre la porta 8443 a internet.
Cambia la password predefinita al primo accesso. La chiave di abbinamento viaggia una volta, in chiaro, sulla LAN quando
premi **Abbina**. Segnala le vulnerabilità in privato al maintainer prima di divulgarle.

## Documentazione

| | |
|---|---|
| Manuale d'uso (PDF, italiano) | nell'app: *Proprietà di sistema* / *Il mio account* → *Scarica il manuale* |
| [`docs/FEATURES.md`](docs/FEATURES.md) | Tutte le funzioni della web app e il protocollo UDP completo (inglese) |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Mappa del codice, cache, permessi, anti-passback (inglese) |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | Flusso git, esecuzione, test, rilascio, Docker (inglese) |
| [`api/`](api/README.md) | Documentazione dell'API REST (PDF, inglese) |

## Crediti

Limen è creato e mantenuto da **Isidor Zabiku**, che ne è il titolare e ne decide la direzione.

Gran parte del codice è stata scritta con l'aiuto di **Claude** (il modello di AI di Anthropic), il maggior contributore di
questo repository.

Copyright © 2026 Isidor Zabiku. Rilasciato con licenza AGPL-3.0 (vedi sotto).

## Licenza

Limen è software libero con licenza **GNU Affero General Public License v3.0** (`AGPL-3.0-only`, vedi [`LICENSE`](LICENSE)):
puoi usarlo, studiarlo, modificarlo e condividerlo; se lo distribuisci o esegui una versione modificata come servizio in
rete, devi rendere disponibile il tuo sorgente con la stessa licenza.

**Licenza commerciale.** Se l'AGPL non è adatta al tuo uso (per esempio vuoi integrare Limen in un prodotto a sorgente
chiuso), il maintainer offre una licenza commerciale a parte.

Il nome «Limen» e il suo logo non sono coperti dall'AGPL: la licenza dà diritti sul codice, non sul marchio.

I contributi sono benvenuti: vedi [`CONTRIBUTING.md`](CONTRIBUTING.md) e la [`CLA`](CLA.md). Componenti di terze parti e
relative licenze: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
