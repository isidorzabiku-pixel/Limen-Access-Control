# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""
Funzionamento di un varco: lettori, uscite (relè), ingressi, avvisi.

La configurazione sta in una colonna JSON del varco (`Varco.funzionamento`) e descrive
COSA e' collegato e COSA fa; i pin fisici restano nel config.yaml dello slave. Arriva
al dispositivo dentro la cache (sezione "fn"), cosi' tutta la logica gira in locale.

Schema (v=1):
  lettori : 1-2 voci {"tec": tecnologia, "dir": "ingresso"|"uscita", "apre": [indici uscita]}
  uscite  : sempre 2 voci {"r": ruolo, "sec": secondi, "nome": str, "tipo": "impulso"|"interruttore"}
            ruolo: nessuno | apre | sirena | inseritore | altro   (tipo conta solo per "altro")
  ingressi: sempre 2 voci {"r": ruolo, "nc": bool, "max": secondi, "nome": str, "az": "registra"|indice uscita}
            ruolo: nessuno | sensore | pulsante | altro            (max solo per "sensore", az solo per "altro")
  avvisi  : {"aperta": bool, "forzata": bool}   (servono un sensore porta su un ingresso)
  inseritore: "livello" | "impulso"

Senza questa configurazione (varchi nati prima) si ricava un valore predefinito dai campi
storici del varco e lo slave si comporta come prima.
"""
import json
import re
from typing import Optional
from services.lingua import pigro, t

VERSIONE = 1
N_USCITE = 2
N_INGRESSI = 2
MAX_LETTORI = 2

# Modalita' in cui il comportamento del varco e' fisso: uscite, ingressi e avvisi non si configurano
# (nel popup restano visibili ma disabilitati) e un salvataggio non li tocca.
MODALITA_BLOCCANTI = ("manutenzione", "emergenza")

TECNOLOGIE = {
    "": (pigro("Non specificata"), None),
    "mf_sett": (pigro("MIFARE protetto"), "alta"),
    "mf_uid": (pigro("MIFARE (solo numero)"), "bassa"),
    "em125": ("125 kHz", "bassa"),
}
DIREZIONI = ("ingresso", "uscita")
RUOLI_USCITA = ("nessuno", "apre", "sirena", "inseritore", "altro")
RUOLI_INGRESSO = ("nessuno", "sensore", "pulsante", "altro")
TIPI_ALTRO = ("impulso", "interruttore")
TIPI_INSERITORE = ("livello", "impulso")

ETICHETTE_USCITA = {
    "nessuno": pigro("Non usata"), "apre": pigro("Apre"), "sirena": pigro("Sirena"),
    "inseritore": pigro("Inseritore allarme"), "altro": pigro("Altro"),
}
ETICHETTE_INGRESSO = {
    "nessuno": pigro("Non usato"), "sensore": pigro("Sensore porta"), "pulsante": pigro("Pulsante o telecomando"), "altro": pigro("Altro"),
}

SEC_APERTURA = (1, 60)
SEC_LUNGO = (5, 600)       # durata sirena e soglia "porta lasciata aperta"
LUNGHEZZA_NOME = 30

_LETTERA_USCITA = {"nessuno": "n", "apre": "a", "sirena": "s", "inseritore": "i", "altro": "x"}
_LETTERA_INGRESSO = {"nessuno": "n", "sensore": "s", "pulsante": "p", "altro": "x"}


# ---------- Predefiniti ----------

def _uscita(r="nessuno", sec=3, nome="", tipo="impulso") -> dict:
    return {"r": r, "sec": sec, "nome": nome, "tipo": tipo}


def _ingresso(r="nessuno", nc=None, max_=30, nome="", az="registra") -> dict:
    if nc is None:
        nc = r == "sensore"        # il sensore porta e' normalmente chiuso, un pulsante normalmente aperto
    return {"r": r, "nc": nc, "max": max_, "nome": nome, "az": az}


def predefinito(tec: str = "mf_sett", direzione: str = "ingresso", sec: int = 3) -> dict:
    """Varco neutro: un lettore che apre l'uscita 1, tutto il resto non usato."""
    sec = _limita(sec, *SEC_APERTURA)
    return {
        "v": VERSIONE,
        "lettori": [{"tec": tec, "dir": direzione if direzione in DIREZIONI else "ingresso", "apre": [0]}],
        "uscite": [_uscita("apre", sec), _uscita("nessuno", 3)],
        "ingressi": [_ingresso(), _ingresso()],
        "avvisi": {"aperta": True, "forzata": True},
        "inseritore": "livello",
    }


def da_varco(varco) -> dict:
    """Funzionamento effettivo del varco: quello salvato, oppure il predefinito ricavato
    dai campi storici (direzione, durata di apertura)."""
    esplicito = leggi(varco)
    if esplicito is not None:
        return esplicito
    return predefinito("", varco.direzione or "ingresso", varco.apertura_durata_sec or 3)


def e_esplicito(varco) -> bool:
    return leggi(varco) is not None


def leggi(varco) -> Optional[dict]:
    """Il funzionamento salvato sul varco (gia' normalizzato), None se non c'e' o e' illeggibile."""
    testo = getattr(varco, "funzionamento", None)
    if not testo:
        return None
    try:
        return normalizza(json.loads(testo))
    except (ValueError, TypeError):
        return None


def a_json(fn: dict) -> str:
    return json.dumps(fn, separators=(",", ":"), ensure_ascii=False)


# ---------- Validazione ----------

def _limita(valore, minimo: int, massimo: int) -> int:
    try:
        n = int(valore)
    except (TypeError, ValueError):
        raise ValueError(t("valore numerico non valido"))
    return max(minimo, min(massimo, n))


def _nome(valore) -> str:
    testo = re.sub(r"[\x00-\x1f\x7f]", " ", str(valore or ""))
    return re.sub(r"\s+", " ", testo).strip()[:LUNGHEZZA_NOME]


def _booleano(valore, predefinito: bool = False) -> bool:
    if isinstance(valore, bool):
        return valore
    if valore in (1, "1", "true", "True"):
        return True
    if valore in (0, "0", "false", "False", None, ""):
        return False
    return predefinito


def eleggibile(uscita: dict) -> bool:
    """Un'uscita che un lettore puo' aprire: "apre", oppure "altro" a impulso."""
    return uscita["r"] == "apre" or (uscita["r"] == "altro" and uscita["tipo"] == "impulso")


def normalizza(dato) -> dict:
    """Controlla e ripulisce un funzionamento arrivato dall'esterno (form, DB).
    Alza ValueError con un messaggio leggibile se la struttura non e' valida."""
    if not isinstance(dato, dict):
        raise ValueError(t("configurazione non valida"))

    uscite_in = dato.get("uscite")
    if not isinstance(uscite_in, list) or len(uscite_in) != N_USCITE:
        raise ValueError(t("servono esattamente %(N_USCITE)s uscite", N_USCITE=N_USCITE))
    uscite = []
    for u in uscite_in:
        if not isinstance(u, dict):
            raise ValueError(t("uscita non valida"))
        r = u.get("r")
        if r not in RUOLI_USCITA:
            raise ValueError(t("funzione dell'uscita non valida"))
        tipo = u.get("tipo") if u.get("tipo") in TIPI_ALTRO else "impulso"
        limite = SEC_LUNGO if r == "sirena" else SEC_APERTURA
        sec = _limita(u.get("sec", 3), *limite)
        uscite.append({"r": r, "sec": sec, "nome": _nome(u.get("nome")) if r == "altro" else "", "tipo": tipo if r == "altro" else "impulso"})

    ingressi_in = dato.get("ingressi")
    if not isinstance(ingressi_in, list) or len(ingressi_in) != N_INGRESSI:
        raise ValueError(t("servono esattamente %(N_INGRESSI)s ingressi", N_INGRESSI=N_INGRESSI))
    ingressi = []
    for g in ingressi_in:
        if not isinstance(g, dict):
            raise ValueError(t("ingresso non valido"))
        r = g.get("r")
        if r not in RUOLI_INGRESSO:
            raise ValueError(t("funzione dell'ingresso non valida"))
        az = "registra"
        if r == "altro":
            candidato = g.get("az")
            if isinstance(candidato, bool):
                candidato = None
            try:
                idx = int(candidato)
            except (TypeError, ValueError):
                idx = None
            if idx is not None and 0 <= idx < N_USCITE and uscite[idx]["r"] != "nessuno":
                az = idx
        ingressi.append({
            "r": r,
            "nc": _booleano(g.get("nc"), r == "sensore"),
            "max": _limita(g.get("max", 30), *SEC_LUNGO) if r == "sensore" else 30,
            "nome": _nome(g.get("nome")) if r == "altro" else "",
            "az": az,
        })

    lettori_in = dato.get("lettori")
    if not isinstance(lettori_in, list) or not 1 <= len(lettori_in) <= MAX_LETTORI:
        raise ValueError(t("servono da 1 a %(MAX_LETTORI)s lettori", MAX_LETTORI=MAX_LETTORI))
    elegg = [i for i, u in enumerate(uscite) if eleggibile(u)]
    lettori = []
    for l in lettori_in:
        if not isinstance(l, dict):
            raise ValueError(t("lettore non valido"))
        tec = l.get("tec", "")
        if tec not in TECNOLOGIE:
            raise ValueError(t("tecnologia del lettore non valida"))
        if l.get("dir") not in DIREZIONI:
            raise ValueError(t("direzione del lettore non valida"))
        apre = l.get("apre", [])
        if not isinstance(apre, list):
            raise ValueError(t("uscite del lettore non valide"))
        pulite = sorted({int(i) for i in apre if isinstance(i, (int, str)) and str(i).lstrip("-").isdigit() and int(i) in elegg})
        lettori.append({"tec": tec, "dir": l["dir"], "apre": pulite})

    avvisi_in = dato.get("avvisi") if isinstance(dato.get("avvisi"), dict) else {}
    avvisi = {"aperta": _booleano(avvisi_in.get("aperta"), True), "forzata": _booleano(avvisi_in.get("forzata"), True)}
    ins = dato.get("inseritore") if dato.get("inseritore") in TIPI_INSERITORE else "livello"
    return {"v": VERSIONE, "lettori": lettori, "uscite": uscite, "ingressi": ingressi, "avvisi": avvisi, "inseritore": ins}


# ---------- Lettura del funzionamento ----------

def direzione_lettore(varco, lettore: int = 0) -> str:
    """Direzione del lettore `lettore` (0 = primo). Senza funzionamento salvato vale quella del varco."""
    fn = leggi(varco)
    if fn is not None and 0 <= lettore < len(fn["lettori"]):
        return fn["lettori"][lettore]["dir"]
    return varco.direzione or "ingresso"


def direzioni(varco) -> set[str]:
    fn = leggi(varco)
    if fn is not None:
        return {l["dir"] for l in fn["lettori"]}
    return {varco.direzione or "ingresso"}


def indici_apre(fn: dict) -> list[int]:
    return [i for i, u in enumerate(fn["uscite"]) if u["r"] == "apre"]


def nome_uscita(fn: dict, i: int) -> str:
    u = fn["uscite"][i]
    if u["r"] == "altro" and u["nome"]:
        return u["nome"]
    return t("Uscita %(p)s", p=i + 1)


def uscite_comandabili(fn: dict) -> list[int]:
    """Uscite che si comandano dal pannello: inseritore e "altro" (gli "apre" hanno il bottone Apri)."""
    return [i for i, u in enumerate(fn["uscite"]) if u["r"] in ("inseritore", "altro")]


def e_interruttore(uscita: dict) -> bool:
    """True se l'uscita ha uno stato acceso/spento (inseritore, o "altro" acceso/spento)."""
    return uscita["r"] == "inseritore" or (uscita["r"] == "altro" and uscita["tipo"] == "interruttore")


def ha_sensore_porta(fn: dict) -> bool:
    return any(g["r"] == "sensore" for g in fn["ingressi"])


def sensore_porta_attivo(varco) -> bool:
    """Il varco ha davvero un sensore porta? Conta solo cio' che il master configura (un ingresso
    con ruolo «Sensore porta»): e' quello che manda allo slave nella cache, e lo slave non legge
    nessun altro sensore. Uno stato porta rimasto in memoria da prima non vale."""
    return ha_sensore_porta(da_varco(varco))


def riepilogo(fn: dict) -> str:
    """Una riga per la scheda Controllo."""
    n_l = len(fn["lettori"])
    parti = [t("1 lettore") if n_l == 1 else t("%(n)s lettori", n=n_l)]
    n_apre = len(indici_apre(fn))
    parti.append(t("nessuna uscita apre") if n_apre == 0 else (t("1 uscita apre") if n_apre == 1 else t("%(n)s uscite aprono", n=n_apre)))
    if ha_sensore_porta(fn):
        parti.append(t("sensore porta"))
    if any(u["r"] == "sirena" for u in fn["uscite"]):
        parti.append(t("sirena"))
    if any(u["r"] == "inseritore" for u in fn["uscite"]):
        parti.append(t("inseritore allarme"))
    return " · ".join(parti)


# ---------- Cache verso lo slave ----------

def per_cache(fn: dict) -> dict:
    """Forma compatta mandata allo slave nella cache (campo "fn"). Posizioni:
      l: [tecnologia, "i"|"u", [uscite che apre]]            per lettore
      u: [ruolo(n,a,s,i,x), secondi, "i"|"t"]                per uscita (t = acceso/spento)
      g: [ruolo(n,s,p,x), nc 1/0, soglia secondi, azione]    per ingresso (azione -1 = solo registra)
      av: [porta lasciata aperta 1/0, porta forzata 1/0]
      ins: "l" livello | "i" impulso"""
    return {
        "l": [[l["tec"], l["dir"][0], l["apre"]] for l in fn["lettori"]],
        "u": [[_LETTERA_USCITA[u["r"]], u["sec"], "t" if u["tipo"] == "interruttore" else "i"] for u in fn["uscite"]],
        "g": [[_LETTERA_INGRESSO[g["r"]], 1 if g["nc"] else 0, g["max"], -1 if g["az"] == "registra" else g["az"]] for g in fn["ingressi"]],
        "av": [1 if fn["avvisi"]["aperta"] else 0, 1 if fn["avvisi"]["forzata"] else 0],
        "ins": fn["inseritore"][0],
    }


# ---------- Stato delle uscite acceso/spento ----------

def leggi_stato_uscite(varco) -> dict[int, bool]:
    try:
        grezzo = json.loads(getattr(varco, "uscite_stato", None) or "{}")
    except ValueError:
        return {}
    return {int(k): bool(v) for k, v in grezzo.items() if str(k).isdigit()}


def scrivi_stato_uscita(varco, indice: int, acceso: bool) -> None:
    stato = leggi_stato_uscite(varco)
    stato[indice] = acceso
    varco.uscite_stato = json.dumps({str(k): v for k, v in sorted(stato.items())}, separators=(",", ":"))


def azzera_stati_cambiati(varco, vecchio: dict, nuovo: dict) -> None:
    """Dopo una modifica del funzionamento: lo stato acceso/spento ricordato per un'uscita vale
    solo finche' l'uscita e' la stessa. Se un'uscita cambia ruolo o tipo, o non e' piu' un
    interruttore, il vecchio stato sparisce (il dispositivo ne riportera' uno nuovo)."""
    stato = leggi_stato_uscite(varco)
    if not stato:
        return
    for i in list(stato):
        try:
            u_nuova = nuovo["uscite"][i]
            u_vecchia = vecchio["uscite"][i]
        except (IndexError, KeyError):
            stato.pop(i, None)
            continue
        if not e_interruttore(u_nuova) or (u_nuova["r"], u_nuova["tipo"]) != (u_vecchia["r"], u_vecchia["tipo"]):
            stato.pop(i, None)
    varco.uscite_stato = json.dumps({str(k): v for k, v in sorted(stato.items())}, separators=(",", ":")) if stato else None
