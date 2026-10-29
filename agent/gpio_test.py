# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Prova dei cablaggi GPIO PRIMA di collegare lo slave al master: scatta i relè uno alla volta e
mostra in tempo reale lo stato degli ingressi. Non parla con la rete.

  python3 gpio_test.py [config.yaml]        (Ctrl+C per uscire)

Se un relè scatta «al contrario» (acceso a riposo, spento quando lo comandi) il modulo e' attivo
basso: metti `active_high: false` nel config.
"""
from __future__ import annotations
import sys
import time

import yaml
from gpiozero import OutputDevice, DigitalInputDevice


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    relays = [r for r in (cfg.get("relays") or []) if r]
    inputs = list(cfg.get("inputs") or [])

    for i, r in enumerate(relays, 1):
        dev = OutputDevice(r["pin"], active_high=r.get("active_high", True), initial_value=False)
        input(f"Uscita {i} (GPIO {r['pin']}): premi Invio, il relè deve scattare per 1 secondo... ")
        dev.on(); time.sleep(1); dev.off()
        print("  fatto")
        dev.close()

    devs = [(i, DigitalInputDevice(r["pin"], pull_up=True)) for i, r in enumerate(inputs, 1) if r]
    if not devs:
        print("Nessun ingresso nel config.")
        return
    print("\nIngressi (contatto verso massa = CHIUSO). Chiudi/apri i contatti, Ctrl+C per uscire.")
    ultimo = {}
    try:
        while True:
            for i, d in devs:
                stato = "ALTO (aperto)" if d.value else "BASSO (chiuso verso massa)"
                if ultimo.get(i) != stato:
                    print(f"  ingresso {i} (GPIO {inputs[i - 1]['pin']}): {stato}")
                    ultimo[i] = stato
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nFine.")


if __name__ == "__main__":
    main()
