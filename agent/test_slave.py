# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Slave di PROVA, senza hardware: serve a controllare dalla webapp (anche da una Pi Zero 2 W
con nient'altro che l'alimentazione e la rete) che master e slave si parlino davvero.

Usa esattamente la stessa logica di agent.py (stesso protocollo UDP, stessa cache, stesse
uscite, ingressi e sensore porta); cambia solo l'hardware, che qui e' simulato:
  - lettori badge: si "passa" un badge scrivendo il codice a tastiera
  - uscite (2 relè): al posto del relè stampa cosa farebbe
  - ingressi (2) e sensore porta: si azionano con i comandi `ingresso` e `porta`

Si abbina da solo: serve solo il numero del varco (la chiave arriva con «Abbina» nella
pagina Varchi della webapp e resta in `--stato`).

Avvio:   python3 test_slave.py --id 1
Scrivi `aiuto` al prompt per i comandi.
"""
from __future__ import annotations
import os
import time
import logging
import argparse
import threading

from hardware.ingresso import Ingresso
from services.udp_client import ClienteUDP
from services.uscite import GestoreUscite
from services.ingressi import GestoreIngressi
from services.porta import MonitorPorta
from agent import loop_ricezione, loop_heartbeat, loop_uscite, loop_ingressi

AIUTO = """Comandi:
  <codice>                 passa un badge sul lettore 1 (oppure `badge <codice> [lettore]`)
  porta aperta | chiusa    simula il sensore porta (serve un ingresso «sensore porta» nel popup Funzionamento)
  ingresso <1|2> on|off    aziona/rilascia un ingresso (pulsante, telecomando, «Altro»)
  stato                    abbinamento, cache, modalita', funzionamento, uscite, coda eventi
  dimentica                cancella la chiave salvata: lo slave si ripresenta e va riabbinato
  aiuto | quit
"""

MODALITA = {"n": "normale", "s": "sblocco programmato", "m": "manutenzione", "e": "emergenza"}


class RelaySimulato:
    """Al posto del relè vero stampa cosa farebbe."""
    def __init__(self, numero: int):
        self.numero = numero
        self.mantenuto = False

    def apri(self, durata_sec):
        print(f"\n  >>> USCITA {self.numero}: IMPULSO per {durata_sec}s (relè simulato)\n> ", end="", flush=True)

    def mantieni(self, acceso):
        if acceso != self.mantenuto:
            self.mantenuto = acceso
            print(f"\n  >>> USCITA {self.numero}: {'ECCITATA (mantenuta)' if acceso else 'rilasciata'}\n> ", end="", flush=True)


class IngressoSimulato(Ingresso):
    """Ingresso senza GPIO: lo stato lo decide la tastiera, il contatto NC/NO lo decide il master."""
    def __init__(self):
        # niente super().__init__: non c'e' nessun pin da aprire (ne' l'avviso «gpiozero non disponibile»)
        self.pin = -1
        self.letture_stabili = 2
        self._stabile = self._candidato = None
        self._conteggio = 0
        self._device = None
        self.attivo_sim = False

    def _grezza(self, nc: bool) -> bool:
        # attivo = contatto aperto per NC (pin alto), chiuso per NO (pin basso)
        return nc if self.attivo_sim else (not nc)


class Simulatore:
    """Tiene insieme cliente UDP, uscite, ingressi e comandi da tastiera (testabile senza socket)."""

    def __init__(self, cliente: ClienteUDP, stato_dir: str, avvia_thread: bool = True):
        self.cliente = cliente
        self.relays = [RelaySimulato(1), RelaySimulato(2)]
        self.ingressi = [IngressoSimulato(), IngressoSimulato()]
        self.monitor = MonitorPorta(cliente, 30)
        self.uscite = GestoreUscite(cliente, self.relays, stato_dir=stato_dir, monitor=self.monitor)
        self.monitor._su_allarme = lambda tipo: self.uscite.sirena()
        self.gestore = GestoreIngressi(cliente, self.ingressi, self.monitor, self.uscite, legacy_nc=True)
        self.monitor._soglia = lambda: self.gestore.soglia_aperta() or 30
        cliente.stato_uscite = self.uscite.stato_testo
        cliente.stato_porta = lambda: self.monitor.stato_testo() if self.gestore.indice_sensore() is not None and cliente.fn is not None else None
        self.stop = threading.Event()
        if avvia_thread:
            for f, a in ((loop_ricezione, (cliente, self.uscite, self.stop)),
                         (loop_heartbeat, (cliente, 3, self.stop)),
                         (loop_uscite, (self.uscite, self.stop)),
                         (loop_ingressi, (self.gestore, self.stop))):
                threading.Thread(target=f, args=a, daemon=True).start()

    # ---- comandi ----
    def badge(self, codice: str, lettore: int = 1) -> str:
        c = self.cliente
        if not c.abbinato:
            return "Non abbinato: premi «Abbina» nella pagina Varchi della webapp."
        indice = lettore - 1
        fn = c.fn
        if fn is not None and indice >= len(fn.lettori):
            return f"Il lettore {lettore} non e' usato nel funzionamento di questo varco: badge ignorato."
        concesso, esito = c.valuta_accesso(codice, c.ora_locale())
        if concesso:
            self.uscite.apri_per_lettore(indice)
        c.manda_evento(codice, esito, lettore=indice if fn is not None else None)
        return f"Lettore {lettore}: {'ACCESSO CONCESSO' if concesso else 'accesso negato'} ({esito}). Report mandato al master."

    def porta(self, stato: str) -> str:
        i = self.gestore.indice_sensore()
        if i is None:
            return "Nessun ingresso configurato come «sensore porta»: sceglilo nel popup Funzionamento del varco e premi «Scarica dati»."
        self.ingressi[i].attivo_sim = (stato == "aperta")
        return f"Sensore porta (ingresso {i + 1}): porta {stato}."

    def ingresso(self, n: int, attivo: bool) -> str:
        if n not in (1, 2):
            return "Gli ingressi sono 1 e 2."
        self.ingressi[n - 1].attivo_sim = attivo
        return f"Ingresso {n}: {'attivo' if attivo else 'a riposo'}."

    def stato(self) -> str:
        c = self.cliente
        righe = [f"Varco {c.varco_id}: " + ("abbinato" + ("" if c.chiave_da_file else " [chiave dal config]")
                                              if c.abbinato else "NON abbinato (si presenta al master)")]
        if c.cache_ricevuta:
            righe.append(f"Cache: versione {c.versione_cache}, {len(c.autorizzazioni)} credenziali, "
                         f"{'fresca' if c.cache_fresca else 'da disco, la chiedo al master'}, "
                         f"varco {'attivo' if c.varco_attivo else 'DISATTIVATO'}")
            righe.append("Modalita': " + MODALITA.get(c.modo, c.modo))
        else:
            righe.append("Cache: non ancora ricevuta (premi «Scarica dati ai dispositivi» nella webapp)")
        if c.fn is not None:
            righe.append(f"Funzionamento: {len(c.fn.lettori)} lettori, uscite {[u.ruolo for u in c.fn.uscite]}, "
                         f"ingressi {[g.ruolo for g in c.fn.ingressi]}")
        else:
            righe.append("Funzionamento: non configurato (comportamento di base)")
        righe.append("Uscite: " + ", ".join(f"{r.numero}={'ON' if r.mantenuto else 'riposo'}" for r in self.relays))
        righe.append(f"Eventi in coda verso il master: {c.eventi_in_coda()}")
        return "\n".join(righe)

    def dimentica(self) -> str:
        c = self.cliente
        if not c.chiave_da_file:
            return "La chiave e' scritta nel config: non e' un abbinamento, non la cancello."
        c.api_key = None
        if c.percorso_chiave and os.path.exists(c.percorso_chiave):
            os.remove(c.percorso_chiave)
        return "Chiave dimenticata: mi ripresento al master, premi di nuovo «Abbina»."

    def esegui(self, riga: str) -> str | None:
        """Una riga di comando -> testo da stampare (None = esci)."""
        p = riga.strip().split()
        if not p:
            return ""
        cmd = p[0].lower()
        if cmd in ("quit", "exit", "esci"):
            return None
        if cmd in ("aiuto", "help", "?"):
            return AIUTO
        if cmd == "stato":
            return self.stato()
        if cmd == "dimentica":
            return self.dimentica()
        if cmd == "porta" and len(p) == 2 and p[1] in ("aperta", "chiusa"):
            return self.porta(p[1])
        if cmd == "ingresso" and len(p) == 3 and p[1].isdigit() and p[2] in ("on", "off"):
            return self.ingresso(int(p[1]), p[2] == "on")
        if cmd == "badge" and len(p) in (2, 3):
            return self.badge(p[1], int(p[2]) if len(p) == 3 and p[2].isdigit() else 1)
        if len(p) == 1:
            return self.badge(p[0])
        return "Comando non capito: scrivi `aiuto`."


def main():
    ap = argparse.ArgumentParser(description="Slave di prova senza hardware (stesso protocollo di agent.py).")
    ap.add_argument("--id", type=int, help="numero del varco (lo mostra la webapp nella scheda Dispositivo)")
    ap.add_argument("--config", help="config.yaml da cui leggere varco_id (e, facoltativa, api_key)")
    ap.add_argument("--stato", default="stato_test", help="cartella dove restano chiave, cache e coda (default: stato_test)")
    ap.add_argument("--porta-ascolto", type=int, default=57990, help="solo per provare master e slave sulla stessa macchina")
    ap.add_argument("--porta-master", type=int, default=57990, help="idem")
    ap.add_argument("--verboso", action="store_true", help="mostra anche i log del protocollo")
    a = ap.parse_args()

    api_key = None
    varco_id = a.id
    if a.config:
        import yaml
        with open(a.config) as f:
            cfg = yaml.safe_load(f) or {}
        varco_id = varco_id or cfg.get("varco_id")
        api_key = cfg.get("api_key") or None
    if not varco_id:
        ap.error("serve --id <numero del varco> (o --config con varco_id)")

    logging.basicConfig(level=logging.INFO if a.verboso else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cliente = ClienteUDP(varco_id, api_key, stato_dir=a.stato, porta_ascolto=a.porta_ascolto, porta_master=a.porta_master)
    sim = Simulatore(cliente, a.stato)

    print(f"Slave di prova avviato: varco {varco_id}, porta UDP {cliente.sock.getsockname()[1]}, stato in {a.stato}")
    if cliente.abbinato:
        print("Chiave gia' presente: parlo col master. Se non arriva la cache, premi «Scarica dati ai dispositivi» nella webapp.")
    else:
        print(f"Non abbinato. Nella webapp (da admin): Varchi -> crea un varco (deve avere il numero {varco_id}) ->\n"
              "compare «Dispositivo in attesa» in cima alla pagina -> premi «Abbina».")
    print("\nScrivi `aiuto` per i comandi. Un codice badge da solo = badge passato sul lettore 1.\n")

    def sentinella():
        """Avvisi utili senza --verboso: abbinamento riuscito e cache arrivata."""
        visto_abb, visto_cache = cliente.abbinato, cliente.cache_ricevuta and cliente.cache_fresca
        while not sim.stop.is_set():
            time.sleep(0.5)
            if cliente.abbinato and not visto_abb:
                print("\n  *** ABBINATO: chiave ricevuta e salvata. Ora chiedo la cache. ***\n> ", end="", flush=True)
            if cliente.cache_ricevuta and cliente.cache_fresca and not visto_cache:
                print(f"\n  *** CACHE dal master: {len(cliente.autorizzazioni)} credenziali. ***\n> ", end="", flush=True)
            visto_abb = cliente.abbinato
            visto_cache = cliente.cache_ricevuta and cliente.cache_fresca
    threading.Thread(target=sentinella, daemon=True).start()

    try:
        while True:
            out = sim.esegui(input("> "))
            if out is None:
                break
            if out:
                print(out)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        print("\nChiusura.")
        sim.stop.set()


if __name__ == "__main__":
    main()
