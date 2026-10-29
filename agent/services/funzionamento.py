# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Funzionamento del varco lato slave: lettura della sezione "fn" della cache.

Il master manda una forma compatta (vedi backend/services/funzionamento.py, per_cache):
  l: [tecnologia, "i"|"u", [uscite che apre]]            per lettore
  u: [ruolo(n,a,s,i,x), secondi, "i"|"t"]                per uscita (t = acceso/spento)
  g: [ruolo(n,s,p,x), nc 1/0, soglia secondi, azione]    per ingresso (azione -1 = solo registra)
  av: [porta lasciata aperta 1/0, porta forzata 1/0]
  ins: "l" livello | "i" impulso

Qui si trasforma in oggetti semplici. Senza "fn" (master vecchio, o varco mai configurato
dal popup) la funzione `leggi` ritorna None e lo slave si comporta come prima.
"""
from __future__ import annotations
from dataclasses import dataclass, field

RUOLI_USCITA = {"n": "nessuno", "a": "apre", "s": "sirena", "i": "inseritore", "x": "altro"}
RUOLI_INGRESSO = {"n": "nessuno", "s": "sensore", "p": "pulsante", "x": "altro"}
N_USCITE = 2
N_INGRESSI = 2


@dataclass
class Lettore:
    tec: str
    direzione: str
    apre: list[int] = field(default_factory=list)


@dataclass
class Uscita:
    ruolo: str
    sec: int
    interruttore: bool          # solo per "altro": acceso/spento invece di impulso

    @property
    def e_interruttore(self) -> bool:
        """Ha uno stato acceso/spento: inseritore, oppure "altro" configurato acceso/spento."""
        return self.ruolo == "inseritore" or (self.ruolo == "altro" and self.interruttore)

    @property
    def e_impulso_comandabile(self) -> bool:
        return self.ruolo == "altro" and not self.interruttore


@dataclass
class Ingresso:
    ruolo: str
    nc: bool
    max_sec: int
    azione: int                 # indice uscita da azionare, -1 = solo registra


@dataclass
class Funzionamento:
    lettori: list[Lettore]
    uscite: list[Uscita]
    ingressi: list[Ingresso]
    avvisa_aperta: bool
    avvisa_forzata: bool
    inseritore_impulso: bool

    def indici_apre(self) -> list[int]:
        return [i for i, u in enumerate(self.uscite) if u.ruolo == "apre"]

    def indici_sirena(self) -> list[int]:
        return [i for i, u in enumerate(self.uscite) if u.ruolo == "sirena"]


def _int(v, predefinito: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return predefinito


def leggi(raw) -> Funzionamento | None:
    """Dalla forma compatta della cache; None se manca o e' malformata (si usa il comportamento di prima)."""
    if not isinstance(raw, dict):
        return None
    try:
        lettori = []
        for l in raw["l"][:2]:
            apre = sorted({i for i in l[2] if isinstance(i, int) and 0 <= i < N_USCITE})
            lettori.append(Lettore(tec=str(l[0]), direzione="uscita" if l[1] == "u" else "ingresso", apre=apre))
        uscite = []
        for u in raw["u"]:
            uscite.append(Uscita(RUOLI_USCITA.get(u[0], "nessuno"), max(1, _int(u[1], 3)), u[2] == "t"))
        ingressi = []
        for g in raw["g"]:
            azione = _int(g[3], -1)
            ingressi.append(Ingresso(RUOLI_INGRESSO.get(g[0], "nessuno"), bool(g[1]), max(1, _int(g[2], 30)),
                                     azione if 0 <= azione < N_USCITE else -1))
        av = raw.get("av", [1, 1])
        if not lettori or len(uscite) != N_USCITE or len(ingressi) != N_INGRESSI:
            return None
        return Funzionamento(lettori, uscite, ingressi, bool(av[0]), bool(av[1]), raw.get("ins") == "i")
    except (KeyError, IndexError, TypeError, ValueError):
        return None
