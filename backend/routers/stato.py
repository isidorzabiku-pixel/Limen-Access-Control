# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""La vecchia pagina /stato ora vive dentro "Info sistema" (/sistema/info):
qui resta solo il reindirizzamento, cosi' i segnalibri continuano a funzionare."""
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/stato")
def pagina_stato():
    return RedirectResponse("/sistema/info", status_code=307)
