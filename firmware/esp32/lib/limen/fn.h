// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Funzionamento del varco (sezione "fn" della cache): lettori, uscite, ingressi, avvisi.
// Forma compatta mandata dal master (vedi backend/services/funzionamento.py, per_cache):
//   l: [tecnologia, "i"|"u", [uscite che apre]]       per lettore
//   u: [ruolo(n,a,s,i,x), secondi, "i"|"t"]           per uscita (t = acceso/spento)
//   g: [ruolo(n,s,p,x), nc 1/0, soglia s, azione]     per ingresso (azione -1 = solo registra)
//   av: [lasciata aperta 1/0, forzata 1/0]    ins: "l" livello | "i" impulso
#pragma once
#include <ArduinoJson.h>
#include <string>
#include <vector>

namespace gk {

constexpr int N_USCITE = 2;
constexpr int N_INGRESSI = 2;

enum class RuoloUscita { Nessuno, Apre, Sirena, Inseritore, Altro };
enum class RuoloIngresso { Nessuno, Sensore, Pulsante, Altro };

struct Lettore {
  std::string tec;
  bool uscita = false;            // direzione: false = ingresso
  std::vector<int> apre;
};

struct Uscita {
  RuoloUscita ruolo = RuoloUscita::Nessuno;
  int sec = 3;
  bool interruttore = false;      // solo per "altro": acceso/spento invece di impulso
  bool eInterruttore() const { return ruolo == RuoloUscita::Inseritore || (ruolo == RuoloUscita::Altro && interruttore); }
  bool eImpulsoComandabile() const { return ruolo == RuoloUscita::Altro && !interruttore; }
};

struct Ingresso {
  RuoloIngresso ruolo = RuoloIngresso::Nessuno;
  bool nc = true;
  int maxSec = 30;
  int azione = -1;                // indice uscita da azionare, -1 = solo registra
};

struct Funzionamento {
  std::vector<Lettore> lettori;
  std::vector<Uscita> uscite;
  std::vector<Ingresso> ingressi;
  bool avvisaAperta = true;
  bool avvisaForzata = true;
  bool inseritoreImpulso = false;

  std::vector<int> indiciApre() const;
  std::vector<int> indiciSirena() const;
  // false se manca o e' malformata (si resta nel comportamento di base).
  static bool leggi(JsonVariantConst raw, Funzionamento& out);
};

}  // namespace gk
