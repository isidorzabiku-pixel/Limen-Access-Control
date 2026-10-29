# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""Prova rapida del sistema di traduzione: python3 tools/test_lingua.py (dalla cartella backend)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from markupsafe import Markup

from services import lingua
from services.lingua import pigro, t

COSTANTE = pigro("Accesso consentito")      # costante di modulo, creata prima di sapere la lingua


def con_lingua(codice):
    return lingua._corrente.set(codice)


tok = con_lingua("it")
assert t("Accesso consentito") == "Accesso consentito"
assert str(COSTANTE) == "Accesso consentito"
assert t("Varco %(nome)s", nome="X") == "Varco X"

con_lingua("en")
assert t("Accesso consentito") != "Accesso consentito", "manca la traduzione inglese"
assert str(COSTANTE) == t("Accesso consentito") and f"{COSTANTE}" == str(COSTANTE)
assert t("frase che non esiste nel catalogo") == "frase che non esiste nel catalogo"   # ripiego sull'italiano
assert t("Varco %(testo)s", testo="X") == "Varco X"                                    # un parametro può chiamarsi «testo»
assert t("Altro")  == lingua.catalogo("en")["Altro"]
assert t("  Altro  ") == "  " + lingua.catalogo("en")["Altro"] + "  "                  # gli spazi ai bordi restano

h = t("Varchi %(p1)s", p1=Markup("<b>x</b>"))                                          # parametro HTML: non va escapato
assert "<b>x</b>" in h and isinstance(h, Markup)
assert "&lt;i&gt;" in t("Varchi %(p1)s %(p2)s", p1=Markup("<b>x</b>"), p2="<i>")

assert lingua.valida("en") == "en" and lingua.valida("xx") is None
lingua.imposta_di_sistema("xx"); assert lingua.di_sistema() == "it"
print("LINGUA OK")
