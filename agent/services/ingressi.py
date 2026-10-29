# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Lettura degli ingressi del varco (2): in base al funzionamento mandato dal master decide
cosa fa ognuno. Logica pura: i pin veri (hardware.ingresso.Ingresso) si passano da fuori.

  sensore   contatto della porta -> MonitorPorta (aperta / forzata / lasciata aperta)
  pulsante  pulsante o telecomando -> apre le uscite "apre"
  altro     azione scelta: azionare un'uscita o solo registrare

Senza funzionamento dal master (cliente.fn None) resta il comportamento di prima: l'ingresso 1,
se c'e', e' il sensore porta, con la soglia e il contatto del config.yaml dello slave.
"""
from __future__ import annotations
import logging

logger = logging.getLogger("agent.ingressi")


class GestoreIngressi:
    def __init__(self, cliente, ingressi: list, monitor, gestore_uscite, legacy_nc: bool = True):
        """`ingressi`: lista di oggetti con attivo(nc) -> bool (uno per pin configurato; puo' contenere None)."""
        self.cliente = cliente
        self.ingressi = ingressi
        self.monitor = monitor
        self.uscite = gestore_uscite
        self.legacy_nc = legacy_nc
        self._ultimo: dict[int, bool] = {}

    def indice_sensore(self) -> int | None:
        fn = self.cliente.fn
        if fn is None:
            return 0 if self.ingressi and self.ingressi[0] is not None else None
        for i, g in enumerate(fn.ingressi):
            if g.ruolo == "sensore" and i < len(self.ingressi) and self.ingressi[i] is not None:
                return i
        return None

    def soglia_aperta(self) -> float | None:
        """Secondi di "lasciata aperta" dal master, o None (si usa quella del config.yaml)."""
        fn = self.cliente.fn
        i = self.indice_sensore()
        if fn is None or i is None:
            return None
        return fn.ingressi[i].max_sec

    def campiona(self) -> None:
        """Un giro di lettura di tutti gli ingressi (da chiamare ~10 volte al secondo)."""
        fn = self.cliente.fn
        sensore = self.indice_sensore()
        for i, ing in enumerate(self.ingressi):
            if ing is None:
                continue
            if fn is None:
                if i == sensore:
                    self.monitor.lettura(ing.attivo(self.legacy_nc))
                continue
            if i >= len(fn.ingressi):
                continue
            g = fn.ingressi[i]
            if g.ruolo == "nessuno":
                self._ultimo.pop(i, None)
                continue
            attivo = ing.attivo(g.nc)
            if g.ruolo == "sensore":
                if i == sensore:
                    self.monitor.lettura(attivo)       # attivo = contatto aperto = porta aperta
                continue
            precedente = self._ultimo.get(i)
            self._ultimo[i] = attivo
            if precedente is None or precedente == attivo:
                continue                               # primo giro (stato iniziale) o nessun cambio
            self.uscite.ingresso(i, attivo)
        if self.monitor is not None and sensore is not None:
            self.monitor.tick()
