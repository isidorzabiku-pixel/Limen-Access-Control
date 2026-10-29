// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#include "fn.h"
#include <algorithm>

namespace gk {

std::vector<int> Funzionamento::indiciApre() const {
  std::vector<int> r;
  for (size_t i = 0; i < uscite.size(); i++) if (uscite[i].ruolo == RuoloUscita::Apre) r.push_back(int(i));
  return r;
}

std::vector<int> Funzionamento::indiciSirena() const {
  std::vector<int> r;
  for (size_t i = 0; i < uscite.size(); i++) if (uscite[i].ruolo == RuoloUscita::Sirena) r.push_back(int(i));
  return r;
}

static RuoloUscita ruoloU(const char* s) {
  switch (s ? s[0] : 0) { case 'a': return RuoloUscita::Apre; case 's': return RuoloUscita::Sirena;
    case 'i': return RuoloUscita::Inseritore; case 'x': return RuoloUscita::Altro; default: return RuoloUscita::Nessuno; }
}
static RuoloIngresso ruoloG(const char* s) {
  switch (s ? s[0] : 0) { case 's': return RuoloIngresso::Sensore; case 'p': return RuoloIngresso::Pulsante;
    case 'x': return RuoloIngresso::Altro; default: return RuoloIngresso::Nessuno; }
}

bool Funzionamento::leggi(JsonVariantConst raw, Funzionamento& out) {
  if (!raw.is<JsonObjectConst>()) return false;
  JsonArrayConst l = raw["l"], u = raw["u"], g = raw["g"];
  if (l.isNull() || u.isNull() || g.isNull()) return false;
  Funzionamento f;
  int n = 0;
  for (JsonVariantConst x : l) {
    if (n++ >= 2) break;
    JsonArrayConst a = x.as<JsonArrayConst>();
    if (a.size() < 3) return false;
    Lettore le;
    le.tec = a[0].as<const char*>() ? a[0].as<const char*>() : "";
    le.uscita = a[1].is<const char*>() && std::string(a[1].as<const char*>()) == "u";
    for (JsonVariantConst i : a[2].as<JsonArrayConst>()) {
      int v = i | -1;
      if (v >= 0 && v < N_USCITE && std::find(le.apre.begin(), le.apre.end(), v) == le.apre.end()) le.apre.push_back(v);
    }
    std::sort(le.apre.begin(), le.apre.end());
    f.lettori.push_back(le);
  }
  for (JsonVariantConst x : u) {
    JsonArrayConst a = x.as<JsonArrayConst>();
    if (a.size() < 3) return false;
    Uscita us;
    us.ruolo = ruoloU(a[0] | "n");
    us.sec = std::max(1, int(a[1] | 3));
    us.interruttore = a[2].is<const char*>() && std::string(a[2].as<const char*>()) == "t";
    f.uscite.push_back(us);
  }
  for (JsonVariantConst x : g) {
    JsonArrayConst a = x.as<JsonArrayConst>();
    if (a.size() < 4) return false;
    Ingresso in;
    in.ruolo = ruoloG(a[0] | "n");
    in.nc = (int(a[1] | 0) != 0);
    in.maxSec = std::max(1, int(a[2] | 30));
    int az = a[3] | -1;
    in.azione = (az >= 0 && az < N_USCITE) ? az : -1;
    f.ingressi.push_back(in);
  }
  JsonArrayConst av = raw["av"];
  if (!av.isNull() && av.size() >= 2) { f.avvisaAperta = int(av[0] | 1) != 0; f.avvisaForzata = int(av[1] | 1) != 0; }
  const char* ins = raw["ins"] | "l";
  f.inseritoreImpulso = (ins[0] == 'i');
  if (f.lettori.empty() || int(f.uscite.size()) != N_USCITE || int(f.ingressi.size()) != N_INGRESSI) return false;
  out = f;
  return true;
}

}  // namespace gk
