// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
//
// Banco di prova: il Flipper legge un tag 125 kHz e manda il codice all'ESP32 sulla USART
// (pin 13 = TX, pin 14 = RX, 115200 8N1). Riga inviata: «PROTOCOLLO:HEX\n», es. «EM4100:0A1B2C3D4E».
// La risposta dell'ESP32 («OK ...», «NO ...», «IGNORED», «ERR ...») compare sullo schermo.
// Interfaccia in inglese (lato dispositivo), commenti in italiano.

#include <furi.h>
#include <furi_hal.h>
#include <gui/gui.h>
#include <input/input.h>
#include <expansion/expansion.h>
#include <notification/notification_messages.h>
#include <lfrfid/lfrfid_worker.h>
#include <lfrfid/protocols/lfrfid_protocols.h>
#include <toolbox/protocols/protocol_dict.h>

#define BAUD           115200
#define PAUSA_MS       1500 // dopo una lettura, prima di rileggere (evita doppi invii dello stesso tag)
#define RIGA_MAX       48

typedef enum {
    EvTasto,
    EvLetto,
} TipoEvento;

typedef struct {
    TipoEvento tipo;
    InputEvent tasto;
    ProtocolId protocollo;
} Evento;

typedef struct {
    FuriMessageQueue* coda;
    FuriStreamBuffer* rx;
    FuriMutex* mutex;
    ViewPort* vp;
    Gui* gui;
    NotificationApp* notifiche;
    FuriHalSerialHandle* seriale;
    ProtocolDict* dict;
    LFRFIDWorker* worker;
    // stato mostrato a schermo (sotto mutex)
    char inviato[RIGA_MAX + 2]; // + «> » davanti alla riga
    char risposta[RIGA_MAX + 2];
    bool errore_uart;
    bool in_attesa; // inviato un codice, si aspetta la risposta: il resto che arriva e' rumore
} App;

// ---------------------------------------------------------------- callback

static void disegna(Canvas* canvas, void* ctx) {
    App* app = ctx;
    furi_mutex_acquire(app->mutex, FuriWaitForever);
    canvas_clear(canvas);
    canvas_set_font(canvas, FontPrimary);
    canvas_draw_str(canvas, 2, 11, "Limen UART reader");
    canvas_set_font(canvas, FontSecondary);
    if(app->errore_uart) {
        canvas_draw_str(canvas, 2, 26, "USART busy: close other");
        canvas_draw_str(canvas, 2, 37, "GPIO apps and retry.");
    } else {
        canvas_draw_str(canvas, 2, 23, "TX pin 13  RX pin 14  115200");
        canvas_draw_str(canvas, 2, 35, app->inviato[0] ? app->inviato : "Hold a 125 kHz tag on the back");
        canvas_draw_str(canvas, 2, 47, app->risposta[0] ? app->risposta : "");
    }
    canvas_draw_str(canvas, 2, 62, "Back: exit");
    furi_mutex_release(app->mutex);
}

static void su_tasto(InputEvent* tasto, void* ctx) {
    App* app = ctx;
    Evento ev = {.tipo = EvTasto, .tasto = *tasto};
    furi_message_queue_put(app->coda, &ev, FuriWaitForever);
}

// Thread del worker RFID: solo un messaggio al ciclo principale.
static void su_lettura(LFRFIDWorkerReadResult esito, ProtocolId protocollo, void* ctx) {
    App* app = ctx;
    if(esito != LFRFIDWorkerReadDone) return;
    Evento ev = {.tipo = EvLetto, .protocollo = protocollo};
    furi_message_queue_put(app->coda, &ev, 0);
}

// Interrupt della USART: un byte alla volta nel buffer.
static void su_rx(FuriHalSerialHandle* h, FuriHalSerialRxEvent ev, void* ctx) {
    App* app = ctx;
    if(ev & FuriHalSerialRxEventData) {
        uint8_t b = furi_hal_serial_async_rx(h);
        furi_stream_buffer_send(app->rx, &b, 1, 0);
    }
}

// ---------------------------------------------------------------- lavoro

static void avvia_lettura(App* app) {
    lfrfid_worker_read_start(app->worker, LFRFIDWorkerReadTypeAuto, su_lettura, app);
}

static void invia_codice(App* app, ProtocolId protocollo) {
    lfrfid_worker_stop(app->worker);

    size_t n = protocol_dict_get_data_size(app->dict, protocollo);
    uint8_t dati[16];
    if(n > sizeof(dati)) n = sizeof(dati);
    protocol_dict_get_data(app->dict, protocollo, dati, n);

    // Il nome del protocollo puo' contenere caratteri che l'ESP32 non vuole (es. spazi): si tiene solo alfanumerico.
    char nome[16] = {0};
    const char* p = protocol_dict_get_name(app->dict, protocollo);
    size_t k = 0;
    for(size_t i = 0; p[i] && k < sizeof(nome) - 1; i++)
        if((p[i] >= '0' && p[i] <= '9') || (p[i] >= 'A' && p[i] <= 'Z') || (p[i] >= 'a' && p[i] <= 'z'))
            nome[k++] = p[i];

    char riga[RIGA_MAX];
    int pos = snprintf(riga, sizeof(riga), "%s:", nome);
    for(size_t i = 0; i < n && pos < (int)sizeof(riga) - 3; i++)
        pos += snprintf(riga + pos, sizeof(riga) - pos, "%02X", dati[i]);
    riga[pos++] = '\n';

    furi_hal_serial_tx(app->seriale, (uint8_t*)riga, pos);
    furi_hal_serial_tx_wait_complete(app->seriale);

    furi_mutex_acquire(app->mutex, FuriWaitForever);
    riga[pos - 1] = '\0';
    snprintf(app->inviato, sizeof(app->inviato), "> %s", riga);
    snprintf(app->risposta, sizeof(app->risposta), "waiting for ESP32...");
    app->in_attesa = true;
    furi_mutex_release(app->mutex);
    notification_message(app->notifiche, &sequence_blink_blue_100);
    view_port_update(app->vp);
}

// Raccoglie la risposta dell'ESP32 fino a «\n». Conta solo una risposta riconosciuta mentre se ne aspetta una:
// i disturbi sulla linea (fili staccati, ESP32 che si avvia) non devono suonare come un badge negato.
static bool risposta_nota(const char* r) {
    return strncmp(r, "OK", 2) == 0 || strncmp(r, "NO", 2) == 0 || strncmp(r, "IGNORED", 7) == 0 ||
           strncmp(r, "ERR", 3) == 0;
}

static void leggi_risposta(App* app, char* buf, size_t* len) {
    uint8_t b;
    while(furi_stream_buffer_receive(app->rx, &b, 1, 0) == 1) {
        if(b == '\r') continue;
        if(b != '\n') {
            if(b < 0x20 || b > 0x7E) {
                *len = 0; // carattere non testuale: riga da buttare
                continue;
            }
            if(*len < RIGA_MAX - 1) buf[(*len)++] = (char)b;
            continue;
        }
        buf[*len] = '\0';
        *len = 0;
        if(!app->in_attesa || !risposta_nota(buf)) continue;
        furi_mutex_acquire(app->mutex, FuriWaitForever);
        snprintf(app->risposta, sizeof(app->risposta), "< %s", buf);
        app->in_attesa = false;
        furi_mutex_release(app->mutex);
        if(strncmp(buf, "OK", 2) == 0)
            notification_message(app->notifiche, &sequence_success);
        else
            notification_message(app->notifiche, &sequence_error);
        view_port_update(app->vp);
    }
}

// ---------------------------------------------------------------- ingresso

int32_t limen_uart_reader_app(void* p) {
    UNUSED(p);
    App* app = malloc(sizeof(App));
    memset(app, 0, sizeof(App));

    app->coda = furi_message_queue_alloc(8, sizeof(Evento));
    app->rx = furi_stream_buffer_alloc(256, 1);
    app->mutex = furi_mutex_alloc(FuriMutexTypeNormal);
    app->notifiche = furi_record_open(RECORD_NOTIFICATION);

    app->vp = view_port_alloc();
    view_port_draw_callback_set(app->vp, disegna, app);
    view_port_input_callback_set(app->vp, su_tasto, app);
    app->gui = furi_record_open(RECORD_GUI);
    gui_add_view_port(app->gui, app->vp, GuiLayerFullscreen);

    // Il servizio «moduli di espansione» ascolta sulla USART: va spento finche' l'app e' aperta.
    Expansion* espansione = furi_record_open(RECORD_EXPANSION);
    expansion_disable(espansione);

    app->seriale = furi_hal_serial_control_acquire(FuriHalSerialIdUsart);
    if(app->seriale) {
        furi_hal_serial_init(app->seriale, BAUD);
        furi_hal_serial_async_rx_start(app->seriale, su_rx, app, false);

        app->dict = protocol_dict_alloc(lfrfid_protocols, LFRFIDProtocolMax);
        app->worker = lfrfid_worker_alloc(app->dict);
        lfrfid_worker_start_thread(app->worker);
        avvia_lettura(app);
    } else {
        app->errore_uart = true;
    }
    view_port_update(app->vp);

    char buf[RIGA_MAX];
    size_t len = 0;
    uint32_t riprendi_alle = 0; // 0 = lettura in corso
    Evento ev;
    bool esci = false;
    while(!esci) {
        if(furi_message_queue_get(app->coda, &ev, 50) == FuriStatusOk) {
            if(ev.tipo == EvTasto) {
                if(ev.tasto.key == InputKeyBack && ev.tasto.type == InputTypeShort) esci = true;
            } else if(ev.tipo == EvLetto && app->seriale && !riprendi_alle) {
                invia_codice(app, ev.protocollo);
                riprendi_alle = furi_get_tick() + furi_ms_to_ticks(PAUSA_MS);
            }
        }
        if(app->seriale) {
            leggi_risposta(app, buf, &len);
            if(riprendi_alle && furi_get_tick() >= riprendi_alle) {
                riprendi_alle = 0;
                avvia_lettura(app);
            }
        }
    }

    if(app->seriale) {
        lfrfid_worker_stop(app->worker);
        lfrfid_worker_stop_thread(app->worker);
        lfrfid_worker_free(app->worker);
        protocol_dict_free(app->dict);
        furi_hal_serial_async_rx_stop(app->seriale);
        furi_hal_serial_deinit(app->seriale);
        furi_hal_serial_control_release(app->seriale);
    }
    expansion_enable(espansione);
    furi_record_close(RECORD_EXPANSION);

    gui_remove_view_port(app->gui, app->vp);
    view_port_free(app->vp);
    furi_record_close(RECORD_GUI);
    furi_record_close(RECORD_NOTIFICATION);
    furi_mutex_free(app->mutex);
    furi_stream_buffer_free(app->rx);
    furi_message_queue_free(app->coda);
    free(app);
    return 0;
}
