# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Timestamp di avvio di questo processo backend. Serve alla pagina Sistema
per mostrare da quanto tempo è su — utile soprattutto per confermare che un
riavvio richiesto da lì sia davvero avvenuto (il valore torna a zero).
Modulo separato e minuscolo solo per evitare un giro di importazioni
circolari tra main.py e routers/admin.py.
"""
from datetime import datetime

AVVIATO_IL = datetime.utcnow()
