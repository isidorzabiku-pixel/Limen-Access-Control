# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Lingue dell'interfaccia (italiano e inglese).

Le frasi nel codice e nei template sono scritte in italiano (e' la lingua di partenza) e passano da `t("frase")`:
con l'italiano si ottiene la frase stessa, con l'inglese la traduzione presa da `lingue/en.json`
(chiave = la frase italiana con gli spazi normalizzati). Se una traduzione manca si vede l'italiano, mai un errore.
Parametri: `t("Varco %(nome)s aggiornato", nome=v.nome)`.

La lingua della richiesta in corso sta in una variabile di contesto (impostata da LinguaMiddleware): la scelta
dell'utente salvata nella sessione, altrimenti il cookie «lingua» (pagina di login), altrimenti quella di sistema.
Non si traducono: database, protocollo, firmware, e i testi gia' scritti nel Log (restano nella lingua del momento).
"""
import contextvars
import json
from pathlib import Path

LINGUE = {"it": "Italiano", "en": "English"}
DEFAULT = "it"
COOKIE = "lingua"

_di_sistema = DEFAULT                      # per chi non ha scelto: si cambia da Proprietà di sistema
_corrente: contextvars.ContextVar = contextvars.ContextVar("lingua", default=None)
_cataloghi: dict[str, dict[str, str]] = {}


def _normalizza(testo: str) -> str:
    return " ".join(testo.split())


def catalogo(codice: str) -> dict[str, str]:
    if codice not in _cataloghi:
        percorso = Path(__file__).resolve().parent.parent / "lingue" / f"{codice}.json"
        try:
            _cataloghi[codice] = json.loads(percorso.read_text(encoding="utf-8")) if percorso.exists() else {}
        except ValueError:
            _cataloghi[codice] = {}
    return _cataloghi[codice]


def versione(codice: str) -> int:
    """Per l'indirizzo del catalogo JavaScript: cambia quando cambia il file delle traduzioni (cosi' il browser lo riscarica)."""
    try:
        return int((Path(__file__).resolve().parent.parent / "lingue" / f"{codice}.json").stat().st_mtime)
    except OSError:
        return 0


def ricarica() -> None:
    _cataloghi.clear()


def valida(codice) -> str | None:
    return codice if codice in LINGUE else None


def imposta_di_sistema(codice) -> None:
    global _di_sistema
    _di_sistema = valida(codice) or DEFAULT


def di_sistema() -> str:
    return _di_sistema


def lingua_corrente() -> str:
    return _corrente.get() or _di_sistema


def t(testo: str, /, **parametri) -> str:
    """Traduce una frase italiana nella lingua della richiesta in corso."""
    risultato = testo
    lingua = lingua_corrente()
    if lingua != DEFAULT:
        nudo = testo.strip()
        if nudo:      # gli spazi a inizio/fine della frase originale (usata come pezzo di una riga) restano
            tradotto = catalogo(lingua).get(_normalizza(nudo))
            if tradotto is not None:
                risultato = testo[: len(testo) - len(testo.lstrip())] + tradotto + testo[len(testo.rstrip()):]
    if not parametri:
        return risultato
    if any(hasattr(v, "__html__") for v in parametri.values()):
        # un parametro è già HTML (es. l'output di una macro): la frase resta HTML, gli altri parametri vengono escapati
        from markupsafe import Markup
        return Markup(risultato) % parametri
    return risultato % parametri


class Pigra(str):
    """Frase italiana che si traduce solo quando viene scritta (str/format, quindi nei template e negli f-string):
    serve per le costanti a livello di modulo, create prima che si sappia la lingua della richiesta."""

    def __str__(self):
        return t(str.__add__(self, ""))

    def __format__(self, spec):
        return format(str(self), spec)

    def __add__(self, altro):      # «etichetta + testo» deve tradurre come str()/format
        return str(self) + altro

    def __radd__(self, altro):
        return altro + str(self)


def pigro(testo: str) -> str:
    return Pigra(testo)


def tradotta(testo: str, lingua: str) -> str:
    """Come t(), ma per una lingua precisa (es. per i test)."""
    token = _corrente.set(lingua)
    try:
        return t(testo)
    finally:
        _corrente.reset(token)


class LinguaMiddleware:
    """ASGI: imposta la lingua della richiesta. Va aggiunto PRIMA di SessionMiddleware (cosi' gli sta dentro)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        scelta = valida((scope.get("session") or {}).get("lingua"))
        if scelta is None:
            for nome, valore in scope.get("headers", []):
                if nome == b"cookie":
                    for parte in valore.decode("latin-1").split(";"):
                        chiave, _, v = parte.strip().partition("=")
                        if chiave == COOKIE:
                            scelta = valida(v)
        token = _corrente.set(scelta)
        try:
            await self.app(scope, receive, send)
        finally:
            _corrente.reset(token)
