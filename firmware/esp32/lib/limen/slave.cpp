// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
#include "slave.h"
#include <algorithm>
#include <cstring>
#include "sha256.h"
#include "util.h"

namespace gk {

static const int IMPULSO_INSERITORE_SEC = 1;
static const int TOLLERANZA_SEC = 5;
static const uint64_t RITRASMISSIONE_MIN_MS = 3000, RITRASMISSIONE_MAX_MS = 60000;
static const uint64_t TOCCO_VALIDITA_MS = 60000;            // una PA vale solo se arriva da chi ci ha toccato negli ultimi 60 s
static const uint64_t INTERVALLO_RICHIESTA_CACHE_MS = 30000;

static std::string unisci(const std::vector<std::string>& v, char sep) {
  std::string s;
  for (size_t i = 0; i < v.size(); i++) { if (i) s += sep; s += v[i]; }
  return s;
}

Slave::Slave(Hal& hal, uint32_t varcoId, int intervalloBattitoSec)
    : hal_(hal), id_(varcoId), intervalloBattitoMs_(std::max(1, intervalloBattitoSec) * 1000) {}

// ---------------------------------------------------------------- avvio

void Slave::begin() {
  nonce_ = hal_.randomHex(8);
  std::string k;
  if (hal_.load("chiave", k)) {
    while (!k.empty() && (k.back() == '\n' || k.back() == '\r' || k.back() == ' ')) k.pop_back();
    if (chiaveValida(k)) chiave_ = k;
  }
  std::string m;
  if (hal_.load("master", m)) {
    while (!m.empty() && (m.back() == '\n' || m.back() == '\r' || m.back() == ' ')) m.pop_back();
    if (ipValido(m)) masterIp_ = m;
  }
  std::string c;
  if (hal_.load("cache", c) && !c.empty()) {
    Cache x;
    if (deserializza(c, x)) {
      cache_ = x; cacheRicevuta_ = true;     // usabile subito; cacheFresca_ resta falso: la chiede al master
      hal_.log(0, "Cache loaded from flash: " + std::to_string(cache_.voci.size()) + " credentials, asking the master for it anyway");
    } else {
      hal_.log(1, "Cache in flash unreadable: ignored");
    }
  }
  caricaCoda();
  std::string u;
  if (hal_.load("uscite", u)) {
    auto p = split(u, ',');
    for (int i = 0; i < N_USCITE && i < int(p.size()); i++) statoUscita_[i] = (p[i] == "1");
  }
  if (!abbinato()) hal_.log(0, "Not paired: waiting for the master to make contact (gate " + std::to_string(id_) + "). Press \"Pair\" on the Gates page.");
}

void Slave::dimenticaAbbinamento() {
  chiave_.clear();
  hal_.save("chiave", "");
  cache_ = Cache(); cacheRicevuta_ = false; cacheFresca_ = false;
  hal_.save("cache", "");
  coda_.clear(); salvaCoda();
  hal_.log(1, "Pairing forgotten: I will introduce myself at the next master contact");
}

// ---------------------------------------------------------------- tempo

Slave::Locale Slave::oraLocale() const {
  int64_t t = ora() + (cache_.haFuso ? int64_t(cache_.fusoMin) * 60 : 0);
  int64_t giorni = t / 86400, sec = t % 86400;
  if (sec < 0) { sec += 86400; giorni -= 1; }
  Locale l;
  l.ora = int(sec / 3600); l.minuto = int((sec % 3600) / 60);
  int64_t gs = (giorni + 3) % 7; if (gs < 0) gs += 7;
  l.giornoSettimana = int(gs);                       // 0 = lunedi'
  int64_t z = giorni + 719468;                       // data civile (Howard Hinnant)
  int64_t era = (z >= 0 ? z : z - 146096) / 146097;
  int64_t doe = z - era * 146097;
  int64_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  int64_t y = yoe + era * 400;
  int64_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  int64_t mp = (5 * doy + 2) / 153;
  l.giorno = int(doy - (153 * mp + 2) / 5 + 1);
  l.mese = int(mp < 10 ? mp + 3 : mp - 9);
  l.anno = int(y + (l.mese <= 2 ? 1 : 0));
  char b[16]; snprintf(b, sizeof(b), "%04d-%02d-%02d", l.anno, l.mese, l.giorno);
  l.data = b;
  return l;
}

// ---------------------------------------------------------------- invio

void Slave::mandaFirmato(const std::vector<std::string>& campi) {
  if (chiave_.empty() || masterIp_.empty()) return;    // senza chiave non si firma; senza master si aspetta il suo tocco
  std::string corpo = unisci(campi, '|');
  std::string pk = corpo + "|" + hmacHex32(chiave_, corpo);
  hal_.unicast(masterIp_, reinterpret_cast<const uint8_t*>(pk.data()), pk.size());
}

void Slave::impostaMaster(const std::string& ip) {
  if (ip == masterIp_) return;
  masterIp_ = ip;
  hal_.save("master", ip + "\n");
  hal_.log(0, "Master: " + ip);
}

void Slave::mandaPresentazione(const std::string& ip) {
  // PR non e' firmato (non c'e' ancora una chiave) e non porta segreti: il master lo mostra all'admin, che decide.
  // Va a chi ci ha appena toccato.
  if (ip.empty()) return;
  std::string pk = "PR|" + std::to_string(id_) + "|" + std::to_string(hal_.unixTime()) + "|" + nonce_;
  hal_.unicast(ip, reinterpret_cast<const uint8_t*>(pk.data()), pk.size());
}

std::string Slave::statoPortaTesto() const {
  if (!cache_.haFn || indiceSensore() < 0 || portaAperta_ < 0) return "-";
  return portaAperta_ ? "a" : "c";
}

std::string Slave::statoUsciteTesto() const {
  if (!cache_.haFn) return "-";
  std::string t; bool uno = false;
  for (int i = 0; i < N_USCITE; i++) {
    if (cache_.fn.uscite[i].eInterruttore()) { t += statoUscita_[i] ? '1' : '0'; uno = true; } else t += '-';
  }
  return uno ? t : "-";
}

void Slave::mandaBattito() {
  if (chiave_.empty() || masterIp_.empty()) return;
  mandaFirmato({"HB", std::to_string(id_), std::to_string(ora()), std::to_string(deriva_), std::to_string(coda_.size()),
                statoPortaTesto(), statoUsciteTesto()});
}

void Slave::mandaRichiestaCache() { mandaFirmato({"RC", std::to_string(id_), std::to_string(ora())}); }
void Slave::mandaCacheAck(int64_t v) { mandaFirmato({"CA", std::to_string(id_), std::to_string(ora()), std::to_string(v)}); }

// ---------------------------------------------------------------- coda eventi con conferma

int64_t Slave::prossimoSeq() {
  ultimoSeq_ = std::max(ultimoSeq_ + 1, hal_.unixTime() * 1000);   // cresce sempre, anche se l'ora torna indietro
  return ultimoSeq_;
}

void Slave::accoda(const std::string& tipo, const std::vector<std::string>& campi, const std::vector<std::string>& extra) {
  Evento e{prossimoSeq(), tipo, ora(), campi, extra};
  coda_.push_back(e);
  while (int(coda_.size()) > MAX_EVENTI_IN_CODA) { coda_.pop_front(); hal_.log(1, "Event queue full: oldest event dropped"); }
  salvaCoda();
  pompa();
}

void Slave::pompa() {
  if (coda_.empty()) return;
  const Evento& t = coda_.front();
  uint64_t now = hal_.millis();
  uint64_t attesa = std::min<uint64_t>(RITRASMISSIONE_MIN_MS << std::min(tentativi_, 6), RITRASMISSIONE_MAX_MS);
  if (inviatoSeq_ == t.seq && now - inviatoAlle_ < attesa) return;
  if (inviatoSeq_ == t.seq) tentativi_++; else tentativi_ = 0;
  inviatoSeq_ = t.seq; inviatoAlle_ = now;
  std::vector<std::string> c{t.tipo, std::to_string(id_), std::to_string(ora())};
  c.insert(c.end(), t.campi.begin(), t.campi.end());
  c.push_back(std::to_string(t.seq)); c.push_back(std::to_string(t.ts));
  c.insert(c.end(), t.extra.begin(), t.extra.end());
  mandaFirmato(c);
}

void Slave::confermaEvento(int64_t seq) {
  size_t prima = coda_.size();
  while (!coda_.empty() && coda_.front().seq <= seq) coda_.pop_front();
  if (coda_.size() != prima) { inviatoSeq_ = -1; tentativi_ = 0; salvaCoda(); }
  pompa();
}

void Slave::salvaCoda() {
  std::string s = "S|" + std::to_string(ultimoSeq_) + "\n";
  for (const Evento& e : coda_)
    s += "E|" + std::to_string(e.seq) + "|" + e.tipo + "|" + std::to_string(e.ts) + "|" + unisci(e.campi, ',') + "|" + unisci(e.extra, ',') + "\n";
  hal_.save("coda", s);
}

void Slave::caricaCoda() {
  std::string s;
  if (!hal_.load("coda", s)) return;
  for (const std::string& riga : split(s, '\n')) {
    if (riga.size() < 3) continue;
    auto p = split(riga.substr(2), '|');
    int64_t n;
    if (riga[0] == 'S' && parseInt(p[0], n)) ultimoSeq_ = n;
    else if (riga[0] == 'E' && p.size() >= 5) {
      Evento e;
      if (!parseInt(p[0], e.seq) || !parseInt(p[2], e.ts)) continue;
      e.tipo = p[1];
      if (!p[3].empty()) e.campi = split(p[3], ',');
      if (!p[4].empty()) e.extra = split(p[4], ',');
      coda_.push_back(e);
    }
  }
  if (!coda_.empty()) hal_.log(0, "Event queue restored from flash: " + std::to_string(coda_.size()) + " events still to deliver");
}

// ---------------------------------------------------------------- ricezione

void Slave::onDatagram(const uint8_t* dati, size_t n, const std::string& ip) {
  if (n == 0) return;
  std::string testo(reinterpret_cast<const char*>(dati), n);
  if (testo.compare(0, 3, "HI|") == 0) { gestisciTocco(testo, ip); return; }
  if (testo.compare(0, 3, "PA|") == 0) { gestisciAbbinamento(testo, ip); return; }
  if (chiave_.empty()) return;                        // non abbinato: il resto e' firmato e non si puo' verificare
  if (testo[0] == '{' || testo.find('\n') != std::string::npos) { gestisciCache(dati, n); return; }
  gestisciPiccolo(testo);
}

void Slave::gestisciAbbinamento(const std::string& testo, const std::string& ip) {
  auto p = split(testo, '|');                         // PA|varco_id|ts|nonce|chiave
  int64_t id;
  if (p.size() != 5 || !parseInt(p[1], id) || id != int64_t(id_)) return;
  // Vale solo da chi ci ha toccato poco fa (stesso IP) e con il nonce di questa esecuzione.
  if (ip.empty() || ip != toccoIp_ || hal_.millis() - toccoAlle_ > TOCCO_VALIDITA_MS) return;
  if (!uguali(p[3], nonce_) || !chiaveValida(p[4])) return;
  chiave_ = p[4];
  hal_.save("chiave", chiave_ + "\n");
  impostaMaster(ip);
  hal_.log(0, "Paired with the master: key received and saved.");
  mandaRichiestaCache();
  haRichiestoCache_ = true; ultimaRichiesta_ = hal_.millis();
}

void Slave::gestisciTocco(const std::string& testo, const std::string& ip) {
  // HI|varco_id|ts|firma dal master. Senza chiave: risponde con una PR. Con la chiave: se la firma e' valida impara l'IP
  // del master e risponde subito con un battito; se non lo e' (varco ricreato sul master, chiave reimpostata) si
  // ripresenta con una PR solo se il tocco arriva dal master che conosce.
  if (ip.empty()) return;
  auto p = split(testo, '|');
  int64_t id, ts;
  if (p.size() != 4 || !parseInt(p[1], id) || id != int64_t(id_) || !parseInt(p[2], ts)) return;
  if (chiave_.empty()) {
    toccoIp_ = ip; toccoAlle_ = hal_.millis();
    mandaPresentazione(ip);
    return;
  }
  if (uguali(hmacHex32(chiave_, p[0] + "|" + p[1] + "|" + p[2]), p[3])) {
    int64_t d = ora() - ts; if (d < 0) d = -d;
    if (!oraValida() || d <= FINESTRA_REPLAY_SEC) { impostaMaster(ip); mandaBattito(); }
    else if (ip == masterIp_) mandaBattito();          // orologio non allineato: il master lo insegna con la cache
    return;                                           // fuori finestra da uno sconosciuto: puo' essere un replay, nulla
  }
  if (ip == masterIp_) { toccoIp_ = ip; toccoAlle_ = hal_.millis(); mandaPresentazione(ip); }
}

void Slave::gestisciPiccolo(const std::string& testo) {
  auto p = split(testo, '|');
  if (p.size() < 4) return;
  int64_t idPk, ts;
  if (!parseInt(p[1], idPk) || !parseInt(p[2], ts)) return;
  std::string firma = p.back();
  std::string canonico = unisci(std::vector<std::string>(p.begin(), p.end() - 1), '|');
  if (!uguali(hmacHex32(chiave_, canonico), firma)) return;       // firmato per un altro varco: normale
  if (idPk != int64_t(id_)) return;
  int64_t d = ora() - ts; if (d < 0) d = -d;
  if (d > FINESTRA_REPLAY_SEC) { hal_.log(1, "Command '" + p[0] + "' dropped: outside the time window (clock not synced?)"); return; }
  std::vector<std::string> campi(p.begin() + 3, p.end() - 1);
  const std::string& tipo = p[0];
  if (tipo == "AP") {
    int64_t dur = 0;
    if (!campi.empty()) parseInt(campi[0], dur);
    hal_.log(0, "Remote opening requested by the master");
    apriPorte(int(dur));
    mandaFirmato({"EA", std::to_string(id_), std::to_string(ora()), "ok"});
  } else if (tipo == "UC") {
    int64_t idx;
    if (campi.size() >= 2 && parseInt(campi[0], idx) && (campi[1] == "i" || campi[1] == "1" || campi[1] == "0"))
      comandoUscita(int(idx), campi[1][0]);
  } else if (tipo == "AE") {
    int64_t seq;
    if (!campi.empty() && parseInt(campi[0], seq)) confermaEvento(seq);
  }
}

void Slave::gestisciCache(const uint8_t* dati, size_t n) {
  // JSON, "\n", firma (32 caratteri): la firma copre i byte del JSON cosi' come sono arrivati.
  const uint8_t* nl = nullptr;
  for (size_t i = n; i-- > 0;) if (dati[i] == '\n') { nl = dati + i; break; }
  if (!nl) return;
  size_t lunghezzaJson = size_t(nl - dati);
  std::string firma(reinterpret_cast<const char*>(nl + 1), n - lunghezzaJson - 1);
  while (!firma.empty() && (firma.back() == '\r' || firma.back() == ' ')) firma.pop_back();
  if (!uguali(hmacHex32(chiave_, dati, lunghezzaJson), firma)) return;   // cache di un altro varco: normale

  JsonDocument doc;
  if (deserializeJson(doc, reinterpret_cast<const char*>(dati), lunghezzaJson)) return;
  JsonObjectConst o = doc.as<JsonObjectConst>();
  if (int64_t(o["varco_id"] | int64_t(-1)) != int64_t(id_)) return;

  Pezzo pezzo;
  leggiPezzo(o, pezzo);
  if (pezzo.scartati) hal_.log(1, std::to_string(pezzo.scartati) + " badges dropped: code too long");

  int parti = o["parti"] | 1;
  Assemblaggio una;
  if (parti <= 1) {
    una.parti = 1; una.pezzi[0] = pezzo; una.metaJson.assign(reinterpret_cast<const char*>(dati), lunghezzaJson);
    applicaCache(una);
    return;
  }
  int64_t versione = o["versione"] | int64_t(0);
  if (asm_.parti != parti || asm_.versione != versione) { asm_ = Assemblaggio(); asm_.versione = versione; asm_.parti = parti; }
  int indice = o["parte"] | 0;
  asm_.pezzi[indice] = pezzo;
  if (indice == 0) asm_.metaJson.assign(reinterpret_cast<const char*>(dati), lunghezzaJson);
  if (int(asm_.pezzi.size()) < parti) return;
  for (int i = 0; i < parti; i++) if (!asm_.pezzi.count(i)) return;   // indici non coerenti: aspetta un altro invio
  Assemblaggio pronto = asm_;
  asm_ = Assemblaggio();
  applicaCache(pronto);
}

void Slave::applicaCache(const Assemblaggio& a) {
  JsonDocument doc;
  if (deserializeJson(doc, a.metaJson)) return;
  JsonObjectConst meta = doc.as<JsonObjectConst>();
  Cache nuova;
  nuova.durataApertura = cache_.durataApertura;
  nuova.haFuso = cache_.haFuso; nuova.fusoMin = cache_.fusoMin;
  leggiMeta(meta, nuova);
  for (int i = 0; i < a.parti; i++) {
    auto it = a.pezzi.find(i);
    if (it == a.pezzi.end()) return;
    nuova.voci.insert(nuova.voci.end(), it->second.voci.begin(), it->second.voci.end());
    for (const auto& pr : it->second.profili) nuova.profili[pr.first] = pr.second;
  }
  std::stable_sort(nuova.voci.begin(), nuova.voci.end(), [](const Voce& x, const Voce& y) { return strcmp(x.codice, y.codice) < 0; });
  // codici ripetuti: vale l'ultimo (come il dizionario del master)
  std::vector<Voce> uniche;
  uniche.reserve(nuova.voci.size());
  for (const Voce& v : nuova.voci) {
    if (!uniche.empty() && strcmp(uniche.back().codice, v.codice) == 0) uniche.back() = v; else uniche.push_back(v);
  }
  nuova.voci.swap(uniche);

  cache_ = nuova;                                      // sostituzione in un colpo solo, mai a meta'
  cacheRicevuta_ = true; cacheFresca_ = true;
  if (meta["ora"].is<double>()) { deriva_ = int64_t(meta["ora"].as<double>()) - hal_.unixTime(); sincronizzato_ = true; }
  if (cache_.modo != 'n') hal_.log(1, std::string("Gate mode: ") + cache_.modo + " (e=emergency, m=maintenance, s=scheduled unlock)");
  hal_.log(0, "Cache updated by the master: " + std::to_string(cache_.voci.size()) + " credentials");
  salvaCache();
  tickUscite();
  if (cache_.versione) mandaCacheAck(cache_.versione);
}

void Slave::salvaCache() { hal_.save("cache", serializza(cache_)); }

// ---------------------------------------------------------------- decisione di accesso (tutta locale)

bool Slave::inFasce(const std::vector<Riga>& righe) const {
  Locale l = oraLocale();
  int minuti = l.ora * 60 + l.minuto;
  bool festivo = cache_.festivi.count(l.data) > 0;
  for (const Riga& r : righe) {
    if (r.escludiFestivi && festivo) continue;
    // Nei festivi vale solo la riga «giorno 7» della griglia; negli altri giorni il giorno della settimana.
    int giornoRiga = festivo ? 7 : l.giornoSettimana;
    if (r.giorno == giornoRiga && r.ini <= minuti && minuti <= r.fine) return true;
  }
  return false;
}

bool Slave::portaLibera() const {
  if (cache_.modo == 'e') return true;
  return cache_.modo == 's' && oraValida() && inFasce(cache_.sblocco);
}

std::pair<bool, std::string> Slave::valutaAccesso(const std::string& codice, int lettore) {
  if (portaLibera()) return {true, "concesso"};
  if (!cache_.attivo) return {false, "negato_varco_disattivato"};
  if (cache_.modo == 'm') return {false, "negato_varco_manutenzione"};
  const Voce* v = codice.size() < size_t(LUNGHEZZA_CODICE) ? cache_.trova(codice.c_str()) : nullptr;
  if (!v) return {false, "negato_credenziale_sconosciuta"};
  if (v->da || v->a) {
    int64_t adesso = ora();
    if (!oraValida() || (v->da && adesso < int64_t(v->da)) || (v->a && adesso > int64_t(v->a))) return {false, "negato_credenziale_scaduta"};
  }
  // Anti-passback a blocco: chi risulta gia' dentro non puo' entrare di nuovo, ma l'uscita passa sempre.
  bool ingresso = !cache_.haFn || lettore < 0 || lettore >= int(cache_.fn.lettori.size()) || !cache_.fn.lettori[lettore].uscita;
  bool bloccato = ingresso && v->bloccoFino && (!oraValida() || ora() < int64_t(v->bloccoFino));
  if (v->profilo == -1) return bloccato ? std::make_pair(false, std::string("negato_anti_passback")) : std::make_pair(true, std::string("concesso"));
  if (!oraValida()) return {false, "negato_fuori_fascia"};      // senza ora attendibile le fasce non si possono valutare
  const std::vector<Riga>* f = cache_.fasce(v->profilo);
  if (f && inFasce(*f)) return bloccato ? std::make_pair(false, std::string("negato_anti_passback")) : std::make_pair(true, std::string("concesso"));
  return {false, "negato_fuori_fascia"};
}

EsitoBadge Slave::badge(const std::string& codice, int lettore) {
  EsitoBadge r;
  if (cache_.haFn && lettore >= int(cache_.fn.lettori.size())) {
    r.ignorato = true;
    hal_.log(0, "Reader " + std::to_string(lettore + 1) + " is not used in the gate operation: badge ignored");
    return r;
  }
  hal_.log(0, "Badge read (reader " + std::to_string(lettore + 1) + "): " + codice);
  auto d = valutaAccesso(codice, lettore);
  r.concesso = d.first; r.esito = d.second;
  hal_.log(0, "Result (decided locally): " + r.esito);
  if (r.concesso) apri(indiciLettore(lettore), 0);
  else { ledRossoFino_ = hal_.millis() + uint64_t(LED_NEGATO_SEC) * 1000; aggiornaLed(); }
  // Il report parte DOPO l'apertura: non e' una richiesta di permesso, solo una notifica.
  std::vector<std::string> extra;
  if (cache_.haFn) extra.push_back(std::to_string(lettore));
  accoda("EV", {codice, r.esito}, extra);
  return r;
}

// ---------------------------------------------------------------- uscite

std::vector<int> Slave::indiciLettore(int lettore) const {
  if (!cache_.haFn) return {0};
  if (lettore >= 0 && lettore < int(cache_.fn.lettori.size())) return cache_.fn.lettori[lettore].apre;
  return {};
}

int Slave::durataUscita(int i, int over) const {
  if (!cache_.haFn) return over > 0 ? over : cache_.durataApertura;
  return cache_.fn.uscite[i].sec;
}

void Slave::apriPorte(int durataOverride) {
  std::vector<int> idx = cache_.haFn ? cache_.fn.indiciApre() : std::vector<int>{0};
  apri(idx, durataOverride);
}

void Slave::apri(const std::vector<int>& indici, int durataOverride) {
  for (int i : indici) {
    if (i < 0 || i >= N_USCITE) continue;
    int sec = durataUscita(i, durataOverride);
    bool porta = !cache_.haFn || cache_.fn.uscite[i].ruolo == RuoloUscita::Apre;
    if (porta) portaAutorizzata(sec);
    impulsoFino_[i] = std::max(impulsoFino_[i], hal_.millis() + uint64_t(sec) * 1000);
    hal_.log(0, "Opening output " + std::to_string(i + 1) + " for " + std::to_string(sec) + " s");
  }
  aggiornaRelay();
}

void Slave::sirena() {
  if (!cache_.haFn) return;
  uint64_t adesso = hal_.millis();
  if (adesso < sirenaFino_ || portaLibera()) return;
  uint64_t fine = adesso;
  bool suonata = false;
  for (int i : cache_.fn.indiciSirena()) {
    int sec = cache_.fn.uscite[i].sec;
    fine = std::max(fine, adesso + uint64_t(sec) * 1000);
    impulsoFino_[i] = std::max(impulsoFino_[i], adesso + uint64_t(sec) * 1000);
    suonata = true;
  }
  sirenaFino_ = fine;
  if (suonata) { hal_.log(1, "Siren activated"); aggiornaRelay(); }
}

void Slave::impostaStato(int indice, bool acceso) {
  if (!cache_.haFn || indice < 0 || indice >= N_USCITE || !cache_.fn.uscite[indice].eInterruttore()) return;
  bool cambiato = statoUscita_[indice] != acceso;
  statoUscita_[indice] = acceso;
  salvaStatoUscite();
  if (cambiato) {
    accoda("SU", {std::to_string(indice), acceso ? "1" : "0"});
    hal_.log(0, "Output " + std::to_string(indice + 1) + (acceso ? ": on" : ": off"));
    if (cache_.fn.uscite[indice].ruolo == RuoloUscita::Inseritore && cache_.fn.inseritoreImpulso) {
      impulsoFino_[indice] = std::max(impulsoFino_[indice], hal_.millis() + uint64_t(IMPULSO_INSERITORE_SEC) * 1000);
    }
  }
  tickUscite();
}

bool Slave::comandoUscita(int indice, char azione) {
  if (!cache_.haFn || indice < 0 || indice >= N_USCITE) {
    hal_.log(1, "Command for output " + std::to_string(indice + 1) + " ignored: operation not configured");
    return false;
  }
  const Uscita& u = cache_.fn.uscite[indice];
  if (u.eImpulsoComandabile() && azione == 'i') { apri({indice}, 0); return true; }
  if (u.eInterruttore() && (azione == '1' || azione == '0')) { impostaStato(indice, azione == '1'); return true; }
  hal_.log(1, std::string("Command '") + azione + "' not valid for output " + std::to_string(indice + 1));
  return false;
}

void Slave::tickUscite() {
  bool libera = portaLibera();
  for (int i = 0; i < N_USCITE; i++) {
    bool voluto;
    if (!cache_.haFn) voluto = (i == 0) ? libera : false;
    else {
      const Uscita& u = cache_.fn.uscite[i];
      if (u.ruolo == RuoloUscita::Apre) voluto = libera;
      else if (u.eInterruttore() && !(u.ruolo == RuoloUscita::Inseritore && cache_.fn.inseritoreImpulso)) voluto = statoUscita_[i];
      else voluto = false;
    }
    if (voluto != mantenuto_[i]) {
      mantenuto_[i] = voluto;
      if (cache_.haFn && cache_.fn.uscite[i].ruolo == RuoloUscita::Apre)
        hal_.log(1, voluto ? "Door FREE: relay held energised" : "Door free ended: relay released");
    }
  }
  aggiornaRelay();
}

void Slave::aggiornaRelay() {
  uint64_t adesso = hal_.millis();
  for (int i = 0; i < N_USCITE; i++) {
    bool vuole = mantenuto_[i] || adesso < impulsoFino_[i];
    if (vuole != eccitato_[i]) { eccitato_[i] = vuole; hal_.setRelay(i, vuole); }
  }
  aggiornaLed();
}

// LED verde = una uscita che apre la porta e' eccitata (badge valido, apertura dal pannello, pulsante, porta libera);
// LED rosso = badge negato da pochi secondi. Servono a simulare/segnalare l'esito anche senza una serratura.
void Slave::aggiornaLed() {
  bool verde = false;
  for (int i = 0; i < N_USCITE; i++) {
    bool apre = cache_.haFn ? cache_.fn.uscite[i].ruolo == RuoloUscita::Apre : i == 0;
    if (apre && eccitato_[i]) verde = true;
  }
  bool rosso = hal_.millis() < ledRossoFino_;
  if (verde != ledVerde_) { ledVerde_ = verde; hal_.setLed(0, verde); }
  if (rosso != ledRosso_) { ledRosso_ = rosso; hal_.setLed(1, rosso); }
}

bool Slave::uscitaEccitata(int i) const { return i >= 0 && i < N_USCITE && eccitato_[i]; }

void Slave::salvaStatoUscite() {
  hal_.save("uscite", std::string(statoUscita_[0] ? "1" : "0") + "," + (statoUscita_[1] ? "1" : "0"));
}

// Un ingresso e' cambiato di stato (con il suo contatto NC/NO gia' applicato).
void Slave::ingressoCambiato(int indice, bool attivo) {
  if (!cache_.haFn || indice < 0 || indice >= N_INGRESSI) return;
  const Ingresso& g = cache_.fn.ingressi[indice];
  if (g.ruolo == RuoloIngresso::Pulsante) {
    if (attivo) {
      hal_.log(0, "Input " + std::to_string(indice + 1) + " (button/remote): opening");
      apriPorte(0);
      accoda("IN", {std::to_string(indice), "1"});
    }
  } else if (g.ruolo == RuoloIngresso::Altro) {
    if (attivo) accoda("IN", {std::to_string(indice), "1"});
    if (g.azione >= 0 && g.azione < N_USCITE) {
      const Uscita& u = cache_.fn.uscite[g.azione];
      if (u.eInterruttore()) impostaStato(g.azione, attivo);       // l'uscita segue l'ingresso
      else if (attivo) apri({g.azione}, 0);
    }
  }
}

// ---------------------------------------------------------------- ingressi

bool Slave::leggiIngresso(int i, bool nc, bool& attivo) {
  bool alto;
  if (!hal_.readInput(i, alto)) return false;
  StatoIngresso& s = ing_[i];
  if (!s.haStabile) { s.stabile = alto; s.haStabile = true; s.conta = 0; }
  else if (alto == s.stabile) { s.conta = 0; }
  else if (s.conta > 0 && alto == s.cand) { if (++s.conta >= 3) { s.stabile = alto; s.conta = 0; } }
  else { s.cand = alto; s.conta = 1; }
  attivo = (s.stabile == nc);           // NC: attivo col contatto aperto (pin alto); NO: col contatto chiuso (pin basso)
  return true;
}

int Slave::indiceSensore() const {
  if (!cache_.haFn) return -1;
  for (int i = 0; i < N_INGRESSI; i++) {
    bool alto;
    if (cache_.fn.ingressi[i].ruolo == RuoloIngresso::Sensore && hal_.readInput(i, alto)) return i;
  }
  return -1;
}

int Slave::sogliaAperta() const {
  int i = indiceSensore();
  return i < 0 ? 30 : cache_.fn.ingressi[i].maxSec;
}

void Slave::campionaIngressi() {
  if (!cache_.haFn) return;
  int sensore = indiceSensore();
  for (int i = 0; i < N_INGRESSI; i++) {
    const Ingresso& g = cache_.fn.ingressi[i];
    bool attivo;
    if (!leggiIngresso(i, g.nc, attivo)) continue;
    if (g.ruolo == RuoloIngresso::Nessuno) { ing_[i].haUltimo = false; continue; }
    if (g.ruolo == RuoloIngresso::Sensore) { if (i == sensore) portaLettura(attivo); continue; }   // attivo = contatto aperto = porta aperta
    if (!ing_[i].haUltimo) { ing_[i].ultimo = attivo; ing_[i].haUltimo = true; continue; }          // primo giro: stato iniziale
    if (ing_[i].ultimo == attivo) continue;
    ing_[i].ultimo = attivo;
    ingressoCambiato(i, attivo);
  }
  if (sensore >= 0) portaTick();
}

// ---------------------------------------------------------------- sensore porta

void Slave::portaAutorizzata(int sec) {
  autorizzataFino_ = std::max(autorizzataFino_, hal_.millis() + uint64_t(sec + TOLLERANZA_SEC) * 1000);
}

void Slave::portaLettura(bool aperta) {
  uint64_t adesso = hal_.millis();
  int8_t nuova = aperta ? 1 : 0;
  if (nuova == portaAperta_) return;
  bool primo = portaAperta_ < 0;
  portaAperta_ = nuova;
  bool avvisaForzata = cache_.haFn ? cache_.fn.avvisaForzata : true;
  if (aperta) {
    apertaDal_ = adesso; avvisata_ = false;
    bool legittima = adesso <= autorizzataFino_ || portaLibera();
    if (primo || legittima || !avvisaForzata) accoda("SP", {"aperta"});     // all'avvio era gia' aperta, o avviso spento: solo stato
    else {
      hal_.log(1, "Door opened without authorisation: FORCED");
      accoda("SP", {"forzata"});
      sirena();
    }
  } else {
    accoda("SP", {"chiusa"});
  }
}

void Slave::portaTick() {
  if (portaAperta_ != 1 || avvisata_) return;
  if (hal_.millis() - apertaDal_ < uint64_t(sogliaAperta()) * 1000) return;
  if (portaLibera()) return;                           // in emergenza/sblocco la porta puo' restare aperta
  avvisata_ = true;
  if (cache_.haFn && !cache_.fn.avvisaAperta) return;
  hal_.log(1, "Door open for over " + std::to_string(sogliaAperta()) + " s: LEFT OPEN");
  accoda("SP", {"lasciata_aperta"});
  sirena();
}

// ---------------------------------------------------------------- ciclo principale

void Slave::loop() {
  uint64_t now = hal_.millis();
  aggiornaRelay();
  if (now - ultimoCampione_ >= 100) { ultimoCampione_ = now; campionaIngressi(); }
  if (now - ultimoTick_ >= 1000) { ultimoTick_ = now; tickUscite(); }
  if (primoBattito_ || now - ultimoBattito_ >= uint64_t(intervalloBattitoMs_)) {
    primoBattito_ = false; ultimoBattito_ = now;
    mandaBattito();
    pompa();                                           // ritrasmette l'evento in coda se il master non ha ancora confermato
    // Finche' non e' arrivata una cache dal master in questa esecuzione la chiede da solo ogni 30 s.
    if (!chiave_.empty() && !masterIp_.empty() && !cacheFresca_ && (!haRichiestoCache_ || now - ultimaRichiesta_ >= INTERVALLO_RICHIESTA_CACHE_MS)) {
      mandaRichiestaCache(); haRichiestoCache_ = true; ultimaRichiesta_ = now;
    }
  }
}

// ---------------------------------------------------------------- stato per la pagina web

static const char* nomeRuoloU(RuoloUscita r) {
  switch (r) { case RuoloUscita::Apre: return "apre"; case RuoloUscita::Sirena: return "sirena";
    case RuoloUscita::Inseritore: return "inseritore"; case RuoloUscita::Altro: return "altro"; default: return "non usata"; }
}
static const char* nomeRuoloG(RuoloIngresso r) {
  switch (r) { case RuoloIngresso::Sensore: return "sensore porta"; case RuoloIngresso::Pulsante: return "pulsante";
    case RuoloIngresso::Altro: return "altro"; default: return "non usato"; }
}

std::string Slave::statoJson() {
  JsonDocument d;
  d["id"] = id_;
  d["abbinato"] = abbinato();
  d["cache_ricevuta"] = cacheRicevuta_;
  d["cache_fresca"] = cacheFresca_;
  d["versione"] = cache_.versione;
  d["badge"] = cache_.voci.size();
  d["attivo"] = cache_.attivo;
  d["modo"] = std::string(1, cache_.modo);
  d["ora_valida"] = oraValida();
  if (oraValida()) {
    Locale l = oraLocale(); char b[24]; snprintf(b, sizeof(b), "%s %02d:%02d", l.data.c_str(), l.ora, l.minuto);
    d["ora_locale"] = b;
  }
  d["deriva"] = deriva_;
  d["coda"] = coda_.size();
  d["funzionamento"] = cache_.haFn;
  JsonArray us = d["uscite"].to<JsonArray>();
  for (int i = 0; i < N_USCITE; i++) {
    JsonObject o = us.add<JsonObject>();
    o["ruolo"] = cache_.haFn ? nomeRuoloU(cache_.fn.uscite[i].ruolo) : (i == 0 ? "apre" : "non usata");
    o["eccitata"] = eccitato_[i];
    o["interruttore"] = cache_.haFn && cache_.fn.uscite[i].eInterruttore();
    o["acceso"] = statoUscita_[i];
    o["impulsoComandabile"] = cache_.haFn && cache_.fn.uscite[i].eImpulsoComandabile();
  }
  JsonArray in = d["ingressi"].to<JsonArray>();
  for (int i = 0; i < N_INGRESSI; i++) {
    JsonObject o = in.add<JsonObject>();
    bool alto; bool presente = hal_.readInput(i, alto);
    o["presente"] = presente;
    o["ruolo"] = cache_.haFn ? nomeRuoloG(cache_.fn.ingressi[i].ruolo) : "non usato";
    o["alto"] = presente && alto;
  }
  d["led_verde"] = ledVerde_;
  d["led_rosso"] = ledRosso_;
  d["porta"] = portaAperta_ < 0 ? "-" : (portaAperta_ ? "open" : "closed");
  d["sensore_porta"] = indiceSensore() >= 0;
  d["lettori"] = cache_.haFn ? int(cache_.fn.lettori.size()) : 1;
  std::string out; serializeJson(d, out);
  return out;
}

}  // namespace gk
