// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Limen slave per ESP32-S3.
//
// Primo avvio (nessuna configurazione): l'ESP32 crea la rete Wi-Fi «Limen-XXXX» (password «limen-setup»);
// collegati e apri http://192.168.4.1 per impostare ID del varco, Wi-Fi e indirizzo IP (proposto: 192.168.1.90).
// Dopo il salvataggio si riavvia, entra nella tua rete con quell'IP fisso e la stessa pagina e' raggiungibile li'.
// Poi nel master crei il varco con lo stesso ID e IP e premi «Abbina».
//
// Tutta la logica del varco e' in lib/limen (provata su PC contro il master); qui c'e' solo la piattaforma:
// rete, flash, pin, pagina web.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESPmDNS.h>
#include <LittleFS.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <deque>
#include "pagine.h"
#include "slave.h"

using namespace gk;

// ---------------------------------------------------------------- configurazione (flash NVS)

struct Config {
  bool presente = false;
  uint32_t id = 1;
  String ssid, wifiPass, ip = "192.168.1.90", gw = "192.168.1.1", mask = "255.255.255.0", adminPass;
  int relePin[2] = {4, 5};
  bool releAttivoAlto[2] = {true, true};
  int ingressoPin[2] = {6, 7};
  int ledPin[2] = {8, 9};                              // LED verde (concesso) e rosso (negato), -1 = non collegato
};

static Preferences prefs;
static Config cfg;

static void caricaConfig() {
  prefs.begin("gk", false);                              // lettura/scrittura: crea il contenitore al primo avvio
  cfg.presente = prefs.isKey("cfg");
  cfg.id = prefs.getUInt("id", 1);
  cfg.ssid = prefs.getString("ssid", "");
  cfg.wifiPass = prefs.getString("wpass", "");
  cfg.ip = prefs.getString("ip", "192.168.1.90");
  cfg.gw = prefs.getString("gw", "192.168.1.1");
  cfg.mask = prefs.getString("mask", "255.255.255.0");
  cfg.adminPass = prefs.getString("apass", "");
  for (int i = 0; i < 2; i++) {
    cfg.relePin[i] = prefs.getInt(i ? "r2" : "r1", i ? 5 : 4);
    cfg.releAttivoAlto[i] = prefs.getBool(i ? "r2h" : "r1h", true);
    cfg.ingressoPin[i] = prefs.getInt(i ? "i2" : "i1", i ? 7 : 6);
    cfg.ledPin[i] = prefs.getInt(i ? "lr" : "lv", i ? 9 : 8);
  }
  prefs.end();
}

static void salvaConfig() {
  prefs.begin("gk", false);
  prefs.putUInt("id", cfg.id);
  prefs.putString("ssid", cfg.ssid);
  prefs.putString("wpass", cfg.wifiPass);
  prefs.putString("ip", cfg.ip);
  prefs.putString("gw", cfg.gw);
  prefs.putString("mask", cfg.mask);
  prefs.putString("apass", cfg.adminPass);
  for (int i = 0; i < 2; i++) {
    prefs.putInt(i ? "r2" : "r1", cfg.relePin[i]);
    prefs.putBool(i ? "r2h" : "r1h", cfg.releAttivoAlto[i]);
    prefs.putInt(i ? "i2" : "i1", cfg.ingressoPin[i]);
    prefs.putInt(i ? "lr" : "lv", cfg.ledPin[i]);
  }
  prefs.putBool("cfg", true);
  prefs.end();
}

// ---------------------------------------------------------------- piattaforma per il core

static std::deque<String> registro;                     // ultime righe, per la pagina web
static int8_t simIngresso[2] = {-1, -1};                // -1 pin reale, 0/1 livello forzato dalla pagina

struct HalEsp32 : Hal {
  WiFiUDP udp;
  uint64_t millis() override { return uint64_t(esp_timer_get_time() / 1000); }
  int64_t unixTime() override { return int64_t(time(nullptr)); }
  void unicast(const std::string& ip, const uint8_t* d, size_t n) override {
    IPAddress dest;
    if (!dest.fromString(ip.c_str())) return;
    udp.beginPacket(dest, PORTA_UDP);
    udp.write(d, n);
    udp.endPacket();
  }
  void setRelay(int i, bool on) override {
    if (i < 0 || i > 1 || cfg.relePin[i] < 0) return;
    digitalWrite(cfg.relePin[i], (on == cfg.releAttivoAlto[i]) ? HIGH : LOW);
  }
  void setLed(int q, bool acceso) override {
    if (q < 0 || q > 1 || cfg.ledPin[q] < 0) return;
    digitalWrite(cfg.ledPin[q], acceso ? HIGH : LOW);
  }
  bool readInput(int i, bool& alto) override {
    if (i < 0 || i > 1 || cfg.ingressoPin[i] < 0) return false;
    alto = simIngresso[i] >= 0 ? simIngresso[i] == 1 : digitalRead(cfg.ingressoPin[i]) == HIGH;
    return true;
  }
  bool load(const char* nome, std::string& out) override {
    File f = LittleFS.open(String("/gk_") + nome, "r");
    if (!f) return false;
    String s = f.readString();
    out.assign(s.c_str(), s.length());
    f.close();
    return true;
  }
  bool save(const char* nome, const std::string& dati) override {
    String p = String("/gk_") + nome, tmp = p + ".tmp";
    File f = LittleFS.open(tmp, "w");
    if (!f) return false;
    size_t w = f.write(reinterpret_cast<const uint8_t*>(dati.data()), dati.size());
    f.close();
    if (w != dati.size()) { LittleFS.remove(tmp); return false; }
    LittleFS.remove(p);
    return LittleFS.rename(tmp, p);                      // scrittura atomica: un'interruzione di corrente non lascia file a meta'
  }
  void log(int livello, const std::string& m) override {
    const char* tag = livello == 0 ? "info" : (livello == 1 ? "warning" : "ERROR");
    String riga = String(uint32_t(millis() / 1000)) + " s " + tag + ": " + m.c_str();
    Serial.println(riga);
    registro.push_back(riga);
    while (registro.size() > 40) registro.pop_front();
  }
  std::string randomHex(int byte) override {
    std::string s; const char* H = "0123456789abcdef";
    for (int i = 0; i < byte; i++) { uint32_t v = esp_random() & 255; s += H[v >> 4]; s += H[v & 15]; }
    return s;
  }
};

static HalEsp32 hal;
static Slave* slave = nullptr;
static WebServer server(80);

// ---------------------------------------------------------------- web

static String esc(const String& s) {
  String o; o.reserve(s.length() + 8);
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '&') o += "&amp;"; else if (c == '<') o += "&lt;"; else if (c == '>') o += "&gt;";
    else if (c == '"') o += "&quot;"; else if (c == '\'') o += "&#39;"; else o += c;
  }
  return o;
}

// Con la configurazione fatta serve la password di amministrazione (utente «admin»); in modalita' di
// configurazione iniziale la pagina e' aperta perche' si raggiunge solo dalla rete Wi-Fi dedicata.
static bool autorizzato() {
  if (!cfg.presente || cfg.adminPass.length() == 0) return true;
  if (server.authenticate("admin", cfg.adminPass.c_str())) return true;
  server.requestAuthentication(BASIC_AUTH, "Limen", "Restricted access");
  return false;
}

static void rispondiJson(const JsonDocument& d, int codice = 200) {
  String s; serializeJson(d, s);
  server.send(codice, "application/json", s);
}

static void apiStato() {
  if (!autorizzato()) return;
  JsonDocument d;
  std::string base = slave ? slave->statoJson() : "{}";
  deserializeJson(d, base);
  d["ip"] = WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : WiFi.softAPIP().toString();
  d["rssi"] = WiFi.RSSI();
  d["uptime"] = uint32_t(hal.millis() / 1000);
  for (int i = 0; i < 2 && i < int(d["ingressi"].size()); i++) d["ingressi"][i]["sim"] = simIngresso[i];
  rispondiJson(d);
}

static void apiLog() {
  if (!autorizzato()) return;
  JsonDocument d;
  JsonArray a = d.to<JsonArray>();
  for (size_t i = registro.size(); i-- > 0;) a.add(registro[i]);       // dal piu' recente
  rispondiJson(d);
}

static void apiBadge() {
  if (!autorizzato()) return;
  String c = server.arg("codice");
  c.trim();
  int lettore = server.arg("lettore").toInt();
  JsonDocument d;
  bool valido = c.length() > 0 && c.length() < size_t(LUNGHEZZA_CODICE);
  for (size_t i = 0; valido && i < c.length(); i++) {
    char x = c[i];
    valido = isalnum(x) || x == '-' || x == '_' || x == '.';
  }
  if (!valido || !slave) { d["esito"] = "invalid code (letters, digits, - _ . only, 23 characters at most)"; d["concesso"] = false; rispondiJson(d, 400); return; }
  EsitoBadge e = slave->badge(c.c_str(), lettore);
  d["ignorato"] = e.ignorato; d["concesso"] = e.concesso; d["esito"] = e.esito;
  rispondiJson(d);
}

static void apiUscita() {
  if (!autorizzato()) return;
  String a = server.arg("azione");
  JsonDocument d;
  d["ok"] = slave && a.length() == 1 && slave->comandoUscita(server.arg("indice").toInt(), a[0]);
  rispondiJson(d);
}

static void apiSim() {
  if (!autorizzato()) return;
  int i = server.arg("indice").toInt();
  String v = server.arg("valore");
  if (i >= 0 && i < 2) simIngresso[i] = v == "1" ? 1 : (v == "0" ? 0 : -1);
  JsonDocument d; d["ok"] = true; rispondiJson(d);
}

static void apiApri() {
  if (!autorizzato()) return;
  if (slave) slave->apriPorte(0);
  JsonDocument d; d["ok"] = true; rispondiJson(d);
}

static void apiDimentica() {
  if (!autorizzato()) return;
  if (slave) slave->dimenticaAbbinamento();
  JsonDocument d; d["ok"] = true; rispondiJson(d);
}

static void apiRiavvia() {
  if (!autorizzato()) return;
  JsonDocument d; d["ok"] = true; rispondiJson(d);
  delay(300);
  ESP.restart();
}

static String campo(const char* nome, const char* etichetta, const String& valore, const char* extra = "") {
  return String("<label>") + etichetta + "</label><input name=\"" + nome + "\" value=\"" + esc(valore) + "\" " + extra + ">";
}

static void paginaSetup() {
  if (!autorizzato()) return;
  String h = FPSTR(SETUP_TESTA);
  if (!cfg.presente) h += "<div class=\"card\"><b>First setup.</b> Choose the gate ID (the same one you will use in the master), the Wi-Fi and the fixed IP address; then in the master create the gate with that ID and IP and press “Pair”.</div>";
  h += "<form method=\"post\" action=\"/setup\">";
  h += "<div class=\"card\"><h2>Gate</h2>" + campo("id", "Gate ID (as in the master)", String(cfg.id), "type=\"number\" min=\"1\" required") + "</div>";
  h += "<div class=\"card\"><h2>Network</h2>" + campo("ssid", "Wi-Fi network name", cfg.ssid, "required maxlength=\"32\"");
  h += "<label>Wi-Fi password" + String(cfg.presente ? " (empty = keep)" : "") + "</label><input name=\"wpass\" type=\"password\" maxlength=\"63\" autocomplete=\"off\">";
  h += "<div class=\"due\"><div>" + campo("ip", "Fixed IP address", cfg.ip, "required") + "</div><div>" + campo("mask", "Subnet", cfg.mask, "required") + "</div></div>";
  h += campo("gw", "Gateway", cfg.gw, "required") + "</div>";
  h += "<div class=\"card\"><h2>Access to this page</h2><label>Admin password (user “admin”)" + String(cfg.presente ? " (empty = keep)" : ", at least 4 characters") + "</label><input name=\"apass\" type=\"password\" maxlength=\"40\" autocomplete=\"off\"" + (cfg.presente ? "" : " required minlength=\"4\"") + "></div>";
  h += "<div class=\"card\"><h2>Pin (GPIO)</h2><div class=\"due\"><div>" + campo("r1", "Relay 1", String(cfg.relePin[0]), "type=\"number\" min=\"-1\" max=\"48\"") + "</div><div>" + campo("r2", "Relay 2", String(cfg.relePin[1]), "type=\"number\" min=\"-1\" max=\"48\"") + "</div>";
  h += "<div>" + campo("i1", "Input 1", String(cfg.ingressoPin[0]), "type=\"number\" min=\"-1\" max=\"48\"") + "</div><div>" + campo("i2", "Input 2", String(cfg.ingressoPin[1]), "type=\"number\" min=\"-1\" max=\"48\"") + "</div></div>";
  h += "<div class=\"due\"><div>" + campo("lv", "Green LED (access granted)", String(cfg.ledPin[0]), "type=\"number\" min=\"-1\" max=\"48\"") + "</div><div>" + campo("lr", "Red LED (denied)", String(cfg.ledPin[1]), "type=\"number\" min=\"-1\" max=\"48\"") + "</div></div>";
  h += String("<label><input type=\"checkbox\" name=\"h1\" value=\"1\" style=\"min-width:auto\"") + (cfg.releAttivoAlto[0] ? " checked" : "") + "> Relay 1 triggers on a high pin (untick for “active-low” modules)</label>";
  h += String("<label><input type=\"checkbox\" name=\"h2\" value=\"1\" style=\"min-width:auto\"") + (cfg.releAttivoAlto[1] ? " checked" : "") + "> Relay 2 triggers on a high pin</label>";
  h += "<p class=\"mu\">-1 = not connected. LEDs need a resistor (330 Ω) to ground; green lights up when an output opens the door, red for 2 seconds after a denied badge. Avoid pins 19, 20 (USB) and 26-37 (flash and memory). Pins 0, 3, 45 and 46 are used at boot: better not to use them.</p></div>";
  h += "<div class=\"riga\"><button>Save and restart</button>" + String(cfg.presente ? "<a href=\"/\">Cancel</a>" : "") + "</div></form>";
  if (cfg.presente) h += "<p class=\"mu\">To restore factory settings hold the BOOT button for 5 seconds.</p>";
  h += "</main></body></html>";
  server.send(200, "text/html", h);
}

static bool pinValido(int p) { return p == -1 || (p >= 0 && p <= 48 && p != 19 && p != 20 && !(p >= 26 && p <= 37)); }

// Controlli comuni alla pagina di configurazione e a config.txt. Ritorna "" se tutto a posto.
static String validaConfig(const Config& n, bool passwordAdminObbligatoria) {
  if (n.id < 1 || n.id > 2000000000UL) return "Invalid ID.";
  if (n.ssid.length() == 0) return "The Wi-Fi network name is required.";
  IPAddress a, g, m;
  if (!a.fromString(n.ip) || !g.fromString(n.gw) || !m.fromString(n.mask)) return "Invalid IP address, gateway or subnet.";
  if (n.adminPass.length() == 0 && passwordAdminObbligatoria) return "Set an admin password.";
  if (n.adminPass.length() > 0 && n.adminPass.length() < 4) return "The admin password must be at least 4 characters long.";
  for (int i = 0; i < 2; i++) if (!pinValido(n.relePin[i]) || !pinValido(n.ingressoPin[i]) || !pinValido(n.ledPin[i])) return "Unusable pin (avoid 19, 20 and 26-37).";
  int usati[8] = {n.relePin[0], n.relePin[1], n.ingressoPin[0], n.ingressoPin[1], n.ledPin[0], n.ledPin[1], -1, -1};
  for (int i = 0; i < 6; i++) for (int j = i + 1; j < 6; j++) if (usati[i] >= 0 && usati[i] == usati[j]) return "The same pin is used twice.";
  return "";
}

static void salvaSetup() {
  if (!autorizzato()) return;
  Config n = cfg;
  n.id = uint32_t(server.arg("id").toInt());
  n.ssid = server.arg("ssid"); n.ssid.trim();
  if (server.arg("wpass").length() > 0) n.wifiPass = server.arg("wpass");
  n.ip = server.arg("ip"); n.gw = server.arg("gw"); n.mask = server.arg("mask");
  if (server.arg("apass").length() > 0) n.adminPass = server.arg("apass");
  n.relePin[0] = server.arg("r1").toInt(); n.relePin[1] = server.arg("r2").toInt();
  n.ingressoPin[0] = server.arg("i1").toInt(); n.ingressoPin[1] = server.arg("i2").toInt();
  n.ledPin[0] = server.arg("lv").toInt(); n.ledPin[1] = server.arg("lr").toInt();
  n.releAttivoAlto[0] = server.hasArg("h1"); n.releAttivoAlto[1] = server.hasArg("h2");
  String errore = validaConfig(n, true);
  if (errore.length()) {
    server.send(400, "text/html", String("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><link rel=stylesheet href=/stile.css><main><div class=card><b>") + esc(errore) + "</b></div><a href=/setup>Back to the form</a></main>");
    return;
  }
  cfg = n; cfg.presente = true;
  salvaConfig();
  String ip = cfg.ip;
  server.send(200, "text/html", String("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><link rel=stylesheet href=/stile.css><main><div class=card><b>Saved.</b> The device restarts and joins the network “") + esc(cfg.ssid) + "”. In a few seconds open <a href=\"http://" + esc(ip) + "\">http://" + esc(ip) + "</a>.<br>Then in the master: create gate #" + String(cfg.id) + " with IP " + esc(ip) + " and press “Pair”.</div></main>");
  delay(800);
  ESP.restart();
}

static void avviaWeb() {
  server.on("/stile.css", HTTP_GET, []() { server.send_P(200, "text/css", STILE); });
  server.on("/", HTTP_GET, []() {
    if (!cfg.presente) { server.sendHeader("Location", "/setup"); server.send(302); return; }
    if (!autorizzato()) return;
    server.send_P(200, "text/html", PAGINA_PRINCIPALE);
  });
  server.on("/setup", HTTP_GET, paginaSetup);
  server.on("/setup", HTTP_POST, salvaSetup);
  server.on("/api/status", HTTP_GET, apiStato);
  server.on("/api/log", HTTP_GET, apiLog);
  server.on("/api/badge", HTTP_POST, apiBadge);
  server.on("/api/uscita", HTTP_POST, apiUscita);
  server.on("/api/sim", HTTP_POST, apiSim);
  server.on("/api/apri", HTTP_POST, apiApri);
  server.on("/api/dimentica", HTTP_POST, apiDimentica);
  server.on("/api/riavvia", HTTP_POST, apiRiavvia);
  server.onNotFound([]() { server.send(404, "text/plain", "Not found"); });
  server.begin();
}

// ---------------------------------------------------------------- config.txt (alternativa alla pagina di configurazione)

static String minuscolo(String s) { for (size_t i = 0; i < s.length(); i++) s.setCharAt(i, tolower(s[i])); return s; }
static bool siNo(const String& v, bool predefinito) {
  String x = minuscolo(v);
  if (x == "yes" || x == "si" || x == "sì" || x == "1" || x == "true" || x == "high" || x == "alto") return true;
  if (x == "no" || x == "0" || x == "false" || x == "low" || x == "basso") return false;
  return predefinito;
}

// Se nel flash c'e' /config.txt (cartella data/ del progetto, caricata con «Upload Filesystem Image») lo legge,
// salva le impostazioni e lo cancella: la password del Wi-Fi non resta in chiaro in un file. Righe «chiave = valore»,
// «#» per i commenti. Un file non valido si ignora (e resta li, cosi' lo correggi).
static void importaConfigTxt() {
  if (!LittleFS.exists("/config.txt")) return;
  File f = LittleFS.open("/config.txt", "r");
  if (!f) return;
  String testo = f.readString();
  f.close();
  Config n = cfg;
  int pos = 0;
  while (pos < int(testo.length())) {
    int fine = testo.indexOf('\n', pos);
    if (fine < 0) fine = testo.length();
    String riga = testo.substring(pos, fine);
    pos = fine + 1;
    riga.trim();
    int uguale = riga.indexOf('=');
    if (riga.length() == 0 || riga[0] == '#' || uguale < 1) continue;
    String k = minuscolo(riga.substring(0, uguale)), v = riga.substring(uguale + 1);
    k.trim(); v.trim();
    if (k == "id") n.id = uint32_t(v.toInt());
    else if (k == "wifi_name" || k == "wifi_nome" || k == "ssid") n.ssid = v;
    else if (k == "wifi_password") n.wifiPass = v;
    else if (k == "ip") n.ip = v;
    else if (k == "gateway") n.gw = v;
    else if (k == "subnet") n.mask = v;
    else if (k == "admin_password" || k == "password_admin") n.adminPass = v;
    else if (k == "relay1" || k == "rele1") n.relePin[0] = v.toInt();
    else if (k == "relay2" || k == "rele2") n.relePin[1] = v.toInt();
    else if (k == "relay1_active_high" || k == "rele1_attivo_alto") n.releAttivoAlto[0] = siNo(v, true);
    else if (k == "relay2_active_high" || k == "rele2_attivo_alto") n.releAttivoAlto[1] = siNo(v, true);
    else if (k == "input1" || k == "ingresso1") n.ingressoPin[0] = v.toInt();
    else if (k == "input2" || k == "ingresso2") n.ingressoPin[1] = v.toInt();
    else if (k == "led_green" || k == "led_verde") n.ledPin[0] = v.toInt();
    else if (k == "led_red" || k == "led_rosso") n.ledPin[1] = v.toInt();
    else Serial.println("config.txt: unknown key “" + k + "”, ignored");
  }
  String errore = validaConfig(n, false);
  if (errore.length()) { Serial.println("config.txt not valid: " + errore + " (ignored)"); return; }
  cfg = n; cfg.presente = true;
  salvaConfig();
  LittleFS.remove("/config.txt");
  Serial.println("Settings imported from config.txt (the file has been deleted)");
  if (cfg.adminPass.length() == 0) Serial.println("Warning: no admin_password set, the device page is open to the whole network");
}

// ---------------------------------------------------------------- rete

static String nomeRete() {
  String mac = WiFi.macAddress(); mac.replace(":", "");
  return "Limen-" + mac.substring(mac.length() - 4);
}

static void avviaReteConfigurazione() {
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP(nomeRete().c_str(), "limen-setup");
  Serial.println("Setup network: " + nomeRete() + " (password limen-setup) -> http://" + WiFi.softAPIP().toString());
}

static void avviaConnessioneWifi() {
  IPAddress ip, gw, mask;
  ip.fromString(cfg.ip); gw.fromString(cfg.gw); mask.fromString(cfg.mask);
  WiFi.setHostname(("limen-" + String(cfg.id)).c_str());
  WiFi.config(ip, gw, mask, gw);                          // IP fisso (DNS = gateway), mai DHCP
  WiFi.begin(cfg.ssid.c_str(), cfg.wifiPass.c_str());
  WiFi.setSleep(false);                                   // i pacchetti del master arrivano senza ritardi del risparmio energetico
}

static bool collegaWifi() {
  WiFi.mode(WIFI_STA);
  avviaConnessioneWifi();
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 25000) delay(200);
  return WiFi.status() == WL_CONNECTED;
}

static bool serviziRete = false;                          // ora di riserva (SNTP) e nome limen-N.local

static void avviaServiziRete() {
  if (serviziRete) return;
  serviziRete = true;
  configTime(0, 0, "pool.ntp.org", "time.google.com");    // ora di riserva se c'e' internet; il master comunque la corregge
  if (MDNS.begin(("limen-" + String(cfg.id)).c_str())) MDNS.addService("http", "tcp", 80);
}

// Sorveglia il Wi-Fi: se cade (router riavviato, segnale perso) riprova ogni 15 s con lo stesso IP fisso; se resta giu'
// per 10 minuti riavvia la scheda (la configurazione, la chiave e la cache sono salvate e si ricaricano da sole).
static void sorvegliaWifi() {
  static uint32_t giuDal = 0, ultimoTentativo = 0, ultimoStato = 0;
  uint32_t adesso = millis();
  if (WiFi.status() == WL_CONNECTED) {
    if (giuDal) {
      hal.log(0, "Wi-Fi back (" + std::string(WiFi.localIP().toString().c_str()) + ")");
      giuDal = 0;
    }
    avviaServiziRete();
  } else {
    if (!giuDal) {
      giuDal = adesso ? adesso : 1; ultimoTentativo = adesso;
      hal.log(1, "Wi-Fi lost: trying to reconnect");
    } else if (adesso - ultimoTentativo > 15000) {
      ultimoTentativo = adesso;
      hal.log(1, "Wi-Fi still down for " + std::to_string((adesso - giuDal) / 1000) + " s: retrying");
      WiFi.disconnect();
      avviaConnessioneWifi();
    }
    if (giuDal && adesso - giuDal > 600000UL) {
      hal.log(2, "Wi-Fi missing for 10 minutes: restarting");
      delay(200);
      ESP.restart();
    }
  }
  if (adesso - ultimoStato > 30000) {                     // una riga ogni 30 s sul monitor seriale, per capire cosa succede
    ultimoStato = adesso;
    Serial.println(String("[status] ") + (WiFi.status() == WL_CONNECTED ? "Wi-Fi ok " + String(WiFi.RSSI()) + " dBm" : String("Wi-Fi DOWN")) +
                   " | up " + String(uint32_t(adesso / 1000)) + " s");
  }
}

// ---------------------------------------------------------------- setup / loop

static uint32_t bootPremutoDal = 0;

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\nLimen slave (ESP32)");
  pinMode(0, INPUT_PULLUP);                               // tasto BOOT: 5 s = impostazioni di fabbrica
  if (!LittleFS.begin(true)) Serial.println("LittleFS not available");
  caricaConfig();
  importaConfigTxt();

  if (cfg.presente) {
    for (int i = 0; i < 2; i++) {
      if (cfg.relePin[i] >= 0) { pinMode(cfg.relePin[i], OUTPUT); digitalWrite(cfg.relePin[i], cfg.releAttivoAlto[i] ? LOW : HIGH); }
      if (cfg.ingressoPin[i] >= 0) pinMode(cfg.ingressoPin[i], INPUT_PULLUP);
      if (cfg.ledPin[i] >= 0) { pinMode(cfg.ledPin[i], OUTPUT); digitalWrite(cfg.ledPin[i], LOW); }
    }
    if (!collegaWifi()) {
      Serial.println("Wi-Fi unreachable: also opening the setup network");
      avviaReteConfigurazione();
    } else {
      Serial.println("Connected: " + WiFi.localIP().toString());
      avviaServiziRete();
    }
    hal.udp.begin(PORTA_UDP);
    slave = new Slave(hal, cfg.id, 3);
    slave->begin();
  } else {
    avviaReteConfigurazione();
  }
  avviaWeb();
}

void loop() {
  server.handleClient();
  if (cfg.presente) sorvegliaWifi();

  if (slave) {
    uint8_t buf[1536];
    int n;
    while ((n = hal.udp.parsePacket()) > 0) {
      int letti = hal.udp.read(buf, sizeof(buf));
      if (n > int(sizeof(buf))) hal.udp.flush();            // pacchetto piu' grande del previsto: il resto si scarta
      if (letti > 0) slave->onDatagram(buf, size_t(letti), std::string(hal.udp.remoteIP().toString().c_str()));
    }
    slave->loop();
  }

  // Tasto BOOT tenuto premuto 5 s: impostazioni di fabbrica (config, abbinamento, cache).
  if (digitalRead(0) == LOW) {
    if (!bootPremutoDal) bootPremutoDal = millis();
    else if (millis() - bootPremutoDal > 5000) {
      Serial.println("Factory settings");
      prefs.begin("gk", false); prefs.clear(); prefs.end();
      LittleFS.format();
      delay(300);
      ESP.restart();
    }
  } else {
    bootPremutoDal = 0;
  }
  delay(2);
}
