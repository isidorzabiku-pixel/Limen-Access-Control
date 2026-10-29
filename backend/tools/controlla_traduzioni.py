# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Controlla il catalogo delle traduzioni (lingue/<codice>.json) contro le frasi usate nel codice.

  python tools/controlla_traduzioni.py            # mostra chiavi mancanti e chiavi non piu' usate (codice di uscita 1 se mancano)
  python tools/controlla_traduzioni.py --elenco   # stampa tutte le frasi italiane usate (una per riga, JSON)

Le frasi si trovano cosi': t("..."), pigro("..."), T('...') nei template (Jinja e JavaScript) e nei file Python.
Le chiavi sono le frasi italiane con gli spazi normalizzati; i segnaposto (%(nome)s, {0}) devono ricomparire uguali nella traduzione.
"""
import ast
import json
import re
import sys
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
LINGUA = "en"
CHIAMATA = re.compile(r"""(?<![\w.])(?:t|pigro|T)\(\s*("(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')""")
SEGNAPOSTO = re.compile(r"%\((\w+)\)s|\{(\d+)\}|%%")


def normalizza(testo: str) -> str:
    return " ".join(testo.split())


def frasi_usate() -> dict[str, set[str]]:
    trovate: dict[str, set[str]] = {}
    file = [p for p in RADICE.glob("templates/*.html")] + [p for d in ("routers", "services") for p in (RADICE / d).glob("*.py")] + [RADICE / "main.py", RADICE / "models.py"]
    for percorso in file:
        if percorso.name in ("lingua.py", "api.py"):
            continue
        testo = percorso.read_text(encoding="utf-8")
        for m in CHIAMATA.finditer(testo):
            try:
                valore = ast.literal_eval(m.group(1))
            except (ValueError, SyntaxError):
                continue
            if isinstance(valore, str) and valore.strip():
                trovate.setdefault(normalizza(valore), set()).add(percorso.name)
    return trovate


def segnaposto(testo: str) -> list[str]:
    return sorted(m.group(0) for m in SEGNAPOSTO.finditer(testo))


def main() -> int:
    usate = frasi_usate()
    if "--elenco" in sys.argv:
        print(json.dumps(sorted(usate), ensure_ascii=False, indent=0))
        return 0
    catalogo = json.loads((RADICE / "lingue" / f"{LINGUA}.json").read_text(encoding="utf-8"))
    mancanti = sorted(k for k in usate if k not in catalogo)
    inutili = sorted(k for k in catalogo if k not in usate)
    sbagliati = sorted(k for k in usate if k in catalogo and segnaposto(k) != segnaposto(catalogo[k]))
    vuoti = sorted(k for k, v in catalogo.items() if not str(v).strip())
    for titolo, elenco in (("Mancanti", mancanti), ("Segnaposto diversi", sbagliati), ("Vuote", vuoti), ("Non piu' usate", inutili)):
        if elenco:
            print(f"== {titolo} ({len(elenco)})")
            for k in elenco:
                print("  ", k[:140])
    print(f"Frasi usate: {len(usate)} · tradotte: {len(usate) - len(mancanti)} · mancanti: {len(mancanti)}")
    return 1 if (mancanti or sbagliati or vuoti) else 0


if __name__ == "__main__":
    sys.exit(main())
