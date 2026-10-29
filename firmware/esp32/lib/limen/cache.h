// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Cache delle credenziali ricevuta dal master: badge autorizzati con le loro fasce, festivi,
// modalita' del varco e funzionamento. La decisione di accesso e' tutta locale su questi dati.
#pragma once
#include <ArduinoJson.h>
#include <cstdint>
#include <map>
#include <set>
#include <string>
#include <vector>
#include "fn.h"

namespace gk {

struct Riga {                       // fascia: giorno 0 = lunedi', minuti dalla mezzanotte
  uint8_t giorno = 0;
  uint16_t ini = 0, fine = 0;
  bool escludiFestivi = false;
};

constexpr int LUNGHEZZA_CODICE = 24;       // 23 caratteri + terminatore

struct Voce {
  char codice[LUNGHEZZA_CODICE] = {0};
  int32_t profilo = -1;             // -1 = sempre (H24), altrimenti indice in Cache::profili
  uint32_t da = 0, a = 0;           // validita' del badge (unix), 0 = nessun limite
  uint32_t bloccoFino = 0;          // anti-passback a blocco: fino a questo istante (unix) la persona risulta dentro e i lettori di ingresso non aprono; 0 = nessun blocco
};

struct Cache {
  std::vector<Voce> voci;                              // ordinate per codice
  std::map<int, std::vector<Riga>> profili;
  std::set<std::string> festivi;                       // "AAAA-MM-GG"
  std::vector<Riga> sblocco;
  char modo = 'n';                                     // n normale, s sblocco programmato, m manutenzione, e emergenza
  int64_t versione = 0;
  int durataApertura = 3;
  bool attivo = true;
  bool haFuso = false;
  int fusoMin = 0;
  bool haFn = false;
  Funzionamento fn;
  std::string fnRaw;                                   // JSON di "fn", per salvarlo cosi' com'e'

  const Voce* trova(const char* codice) const;
  const std::vector<Riga>* fasce(int idx) const;
  void ordina();
};

// Un pezzo della cache (un datagramma): badge e profili che porta.
struct Pezzo {
  std::vector<Voce> voci;
  std::map<int, std::vector<Riga>> profili;
  int scartati = 0;                                    // badge con codice troppo lungo
};

bool leggiRighe(JsonVariantConst arr, std::vector<Riga>& out);
bool leggiPezzo(JsonObjectConst o, Pezzo& out);
// Meta (campi comuni a ogni pezzo) -> cache; l'ora del master e' gestita da chi chiama.
void leggiMeta(JsonObjectConst o, Cache& c);

std::string serializza(const Cache& c);
bool deserializza(const std::string& testo, Cache& out);

}  // namespace gk
