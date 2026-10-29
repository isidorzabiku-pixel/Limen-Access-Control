# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Un solo oggetto Jinja2Templates condiviso da tutti i router, con i filtri
comuni (per ora: "ora", che mostra un orario UTC del database in ora locale).
"""
from fastapi.templating import Jinja2Templates

from database import SessionLocal
from services import tempo, modifiche, lingua

templates = Jinja2Templates(directory="templates")
templates.env.filters["ora"] = tempo.formatta
templates.env.filters["a_input"] = tempo.a_input_locale   # valore per <input type="datetime-local">


def _dati_stato() -> dict:
    """Per il banner "dati non aggiornati" di base.html: sessione propria, mai un'eccezione in pagina."""
    db = SessionLocal()
    try:
        return modifiche.stato_dati(db)
    except Exception:
        return {"da_inviare": []}
    finally:
        db.close()


templates.env.globals["dati_stato"] = _dati_stato
templates.env.globals["t"] = lingua.t                          # {{ t("frase italiana") }}
templates.env.globals["lingua_corrente"] = lingua.lingua_corrente
templates.env.globals["catalogo_versione"] = lingua.versione
templates.env.globals["LINGUE"] = lingua.LINGUE
