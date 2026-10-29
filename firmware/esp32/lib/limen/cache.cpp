// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#include "cache.h"
#include <algorithm>
#include <cstring>
#include "util.h"

namespace gk {

const Voce* Cache::trova(const char* codice) const {
  auto it = std::lower_bound(voci.begin(), voci.end(), codice,
                             [](const Voce& v, const char* c) { return strcmp(v.codice, c) < 0; });
  if (it != voci.end() && strcmp(it->codice, codice) == 0) return &*it;
  return nullptr;
}

const std::vector<Riga>* Cache::fasce(int idx) const {
  auto it = profili.find(idx);
  return it == profili.end() ? nullptr : &it->second;
}

void Cache::ordina() {
  std::sort(voci.begin(), voci.end(), [](const Voce& a, const Voce& b) { return strcmp(a.codice, b.codice) < 0; });
}

bool leggiRighe(JsonVariantConst arr, std::vector<Riga>& out) {
  out.clear();
  if (!arr.is<JsonArrayConst>()) return false;
  for (JsonVariantConst r : arr.as<JsonArrayConst>()) {
    JsonArrayConst a = r.as<JsonArrayConst>();
    if (a.size() < 3) continue;
    Riga x;
    x.giorno = uint8_t(int(a[0] | 0));
    x.ini = uint16_t(int(a[1] | 0));
    x.fine = uint16_t(int(a[2] | 0));
    x.escludiFestivi = a.size() > 3 && int(a[3] | 0) != 0;
    out.push_back(x);
  }
  return true;
}

bool leggiPezzo(JsonObjectConst o, Pezzo& out) {
  out = Pezzo();
  for (JsonPairConst kv : o["profili"].as<JsonObjectConst>()) {
    std::vector<Riga> righe;
    leggiRighe(kv.value(), righe);
    out.profili[atoi(kv.key().c_str())] = righe;
  }
  for (JsonVariantConst b : o["badge"].as<JsonArrayConst>()) {
    const char* c = b["c"] | "";
    if (!*c) continue;
    if (strlen(c) >= LUNGHEZZA_CODICE) { out.scartati++; continue; }
    Voce v;
    strncpy(v.codice, c, LUNGHEZZA_CODICE - 1);
    if (int(b["s"] | 0) || int(b["sempre"] | 0)) v.profilo = -1;
    else if (b["p"].is<int>()) v.profilo = b["p"].as<int>();
    else { v.profilo = -2; }                           // formato vecchio con fasce in linea: non supportato (nessun accesso)
    if (b["d"].is<double>()) v.da = uint32_t(b["d"].as<double>());
    if (b["e"].is<double>()) v.a = uint32_t(b["e"].as<double>());
    if (b["x"].is<double>()) v.bloccoFino = uint32_t(b["x"].as<double>());
    out.voci.push_back(v);
  }
  return true;
}

void leggiMeta(JsonObjectConst o, Cache& c) {
  c.versione = o["versione"] | int64_t(0);
  c.durataApertura = std::max(1, int(o["durata_apertura_sec"] | c.durataApertura));
  c.attivo = o["attivo"] | true;
  if (o["fuso_min"].is<int>()) { c.haFuso = true; c.fusoMin = o["fuso_min"].as<int>(); }
  c.festivi.clear();
  for (JsonVariantConst f : o["festivi"].as<JsonArrayConst>()) if (f.is<const char*>()) c.festivi.insert(f.as<const char*>());
  const char* modo = o["modo"] | "n";
  c.modo = (modo[0] == 's' || modo[0] == 'm' || modo[0] == 'e') ? modo[0] : 'n';
  c.sblocco.clear();
  if (o["sblocco"].is<JsonArrayConst>()) leggiRighe(o["sblocco"], c.sblocco);
  c.haFn = false; c.fnRaw.clear(); c.fn = Funzionamento();
  if (!o["fn"].isNull()) {
    Funzionamento f;
    if (Funzionamento::leggi(o["fn"], f)) {
      c.haFn = true; c.fn = f;
      serializeJson(o["fn"], c.fnRaw);
    }
  }
}

// ---------- su flash: testo, una riga per elemento ----------

static std::string righeTesto(const std::vector<Riga>& r) {
  std::string s;
  for (size_t i = 0; i < r.size(); i++) {
    if (i) s += ';';
    s += std::to_string(r[i].giorno) + "," + std::to_string(r[i].ini) + "," + std::to_string(r[i].fine) + "," + (r[i].escludiFestivi ? "1" : "0");
  }
  return s;
}

static void testoRighe(const std::string& s, std::vector<Riga>& out) {
  out.clear();
  if (s.empty()) return;
  for (const std::string& p : split(s, ';')) {
    auto c = split(p, ',');
    if (c.size() < 4) continue;
    Riga r; r.giorno = uint8_t(atoi(c[0].c_str())); r.ini = uint16_t(atoi(c[1].c_str())); r.fine = uint16_t(atoi(c[2].c_str()));
    r.escludiFestivi = c[3] == "1";
    out.push_back(r);
  }
}

std::string serializza(const Cache& c) {
  std::string s = "GKC1\n";
  s += "v|" + std::to_string(c.versione) + "|" + std::to_string(c.durataApertura) + "|" + (c.attivo ? "1" : "0") + "|" +
       (c.haFuso ? "1" : "0") + "|" + std::to_string(c.fusoMin) + "|" + std::string(1, c.modo) + "\n";
  s += "f|";
  bool primo = true;
  for (const auto& f : c.festivi) { if (!primo) s += ';'; s += f; primo = false; }
  s += "\ns|" + righeTesto(c.sblocco) + "\n";
  if (c.haFn) s += "n|" + c.fnRaw + "\n";
  for (const auto& p : c.profili) s += "p|" + std::to_string(p.first) + "|" + righeTesto(p.second) + "\n";
  for (const Voce& v : c.voci)
    s += std::string("b|") + v.codice + "|" + std::to_string(v.profilo) + "|" + std::to_string(v.da) + "|" + std::to_string(v.a) + "|" + std::to_string(v.bloccoFino) + "\n";
  return s;
}

bool deserializza(const std::string& testo, Cache& out) {
  Cache c;
  bool intestazione = false;
  size_t pos = 0;
  while (pos < testo.size()) {
    size_t fine = testo.find('\n', pos);
    if (fine == std::string::npos) fine = testo.size();
    std::string riga = testo.substr(pos, fine - pos);
    pos = fine + 1;
    if (riga.empty()) continue;
    if (!intestazione) { if (riga != "GKC1") return false; intestazione = true; continue; }
    if (riga.size() < 2 || riga[1] != '|') continue;
    std::string resto = riga.substr(2);
    switch (riga[0]) {
      case 'v': {
        auto p = split(resto, '|');
        if (p.size() < 6) return false;
        int64_t n;
        if (parseInt(p[0], n)) c.versione = n;
        c.durataApertura = std::max(1, atoi(p[1].c_str()));
        c.attivo = p[2] == "1"; c.haFuso = p[3] == "1"; c.fusoMin = atoi(p[4].c_str());
        c.modo = p[5].empty() ? 'n' : p[5][0];
        break;
      }
      case 'f': if (!resto.empty()) for (const auto& d : split(resto, ';')) c.festivi.insert(d); break;
      case 's': testoRighe(resto, c.sblocco); break;
      case 'n': {
        JsonDocument d;
        if (!deserializeJson(d, resto)) {
          Funzionamento f;
          if (Funzionamento::leggi(d.as<JsonVariantConst>(), f)) { c.haFn = true; c.fn = f; c.fnRaw = resto; }
        }
        break;
      }
      case 'p': {
        size_t q = resto.find('|');
        if (q == std::string::npos) break;
        std::vector<Riga> r; testoRighe(resto.substr(q + 1), r);
        c.profili[atoi(resto.substr(0, q).c_str())] = r;
        break;
      }
      case 'b': {
        auto p = split(resto, '|');
        if (p.size() < 4 || p[0].size() >= size_t(LUNGHEZZA_CODICE)) break;
        Voce v; strncpy(v.codice, p[0].c_str(), LUNGHEZZA_CODICE - 1);
        v.profilo = atoi(p[1].c_str()); v.da = uint32_t(strtoul(p[2].c_str(), nullptr, 10)); v.a = uint32_t(strtoul(p[3].c_str(), nullptr, 10));
        v.bloccoFino = p.size() > 4 ? uint32_t(strtoul(p[4].c_str(), nullptr, 10)) : 0;
        c.voci.push_back(v);
        break;
      }
    }
  }
  if (!intestazione) return false;
  c.ordina();
  out = c;
  return true;
}

}  // namespace gk
