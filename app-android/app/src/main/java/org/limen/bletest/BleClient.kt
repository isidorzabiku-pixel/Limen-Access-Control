// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Client BLE per i varchi con firmware esp32s3_blecred (src/ble_cred.*): sfida + HMAC con il segreto del telefono
// (ChiaveTelefono). Due cose:
//  - vicinanza: con l'app in primo piano una ricerca leggera dice quali varchi sono raggiungibili adesso;
//  - operazioni: «apri» e «abbina» fanno tutto da sole (connessione, sfida, comando, risposta, disconnessione).
// Tutto lo stato si tocca nel thread principale.
package org.limen.bletest

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import java.security.SecureRandom
import java.util.UUID

val UUID_SERVIZIO_CRED: UUID = UUID.fromString("6b1c4a10-7a3e-4c1d-9d6f-3a1e5b0c8f21")
val UUID_SFIDA: UUID = UUID.fromString("6b1c4a11-7a3e-4c1d-9d6f-3a1e5b0c8f21")
val UUID_COMANDO: UUID = UUID.fromString("6b1c4a12-7a3e-4c1d-9d6f-3a1e5b0c8f21")
val UUID_ESITO_CRED: UUID = UUID.fromString("6b1c4a13-7a3e-4c1d-9d6f-3a1e5b0c8f21")

private val UUID_CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

private const val VICINO_PER_MS = 12_000L                // un varco e' «vicino» se si e' fatto sentire da poco
private const val BATTITO_MS = 3_000L
private const val ATTESA_NOTIFICA_MS = 1_500L            // poi la risposta si legge con una read
private const val ATTESA_MASTER_MS = 7_500L              // dopo «PAIR SENT» lo slave aspetta il master fino a 6 s
private const val SCADENZA_APERTURA_MS = 12_000L
private const val SCADENZA_ABBINAMENTO_MS = 25_000L
private const val MOSTRA_ESITO_MS = 3_500L
private const val MAX_TENTATIVI_CONNESSIONE = 3          // la connessione BLE a volte fallisce al primo colpo: si riprova da soli
private const val PAUSA_TENTATIVO_MS = 300L
private const val LUNGHEZZA_CREDENZIALE_ESA = 2 * (23 + 32 + 16)   // «PAIR OK» porta titolo + segreto + etichetta
private const val LUNGHEZZA_INVITO = 10
private const val ALFABETO_INVITO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   // come backend/services/ble_abbinamento.py

fun permessiNecessari(): Array<String> =
    if (Build.VERSION.SDK_INT >= 31) arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
    else arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)

fun haPermessi(ctx: Context): Boolean =
    permessiNecessari().all { ctx.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED }

// Un varco sentito dalla ricerca.
data class Vicino(val indirizzo: String, val nome: String, val visto: Long)

enum class Fase { CONNESSIONE, ATTESA, FATTO }

// L'operazione in corso su un varco (una alla volta).
data class Operazione(
    val indirizzo: String,
    val abbinamento: Boolean,
    val fase: Fase,
    val ok: Boolean = false,
    val esito: String = "",          // risposta del varco, o il motivo per cui non c'e' stata
)

data class UiBle(
    val varchi: List<Varco> = emptyList(),
    val vicini: Map<String, Vicino> = emptyMap(),
    val ora: Long = 0,                                   // aggiornata dal battito: fa scadere i «vicini»
    val ricerca: Boolean = false,
    val operazione: Operazione? = null,
    val titolo: String = "",                             // titolo di questo telefono ("" = non abbinato)
    val messaggio: String? = null,
) {
    fun vicino(indirizzo: String): Boolean = (vicini[indirizzo]?.visto ?: 0) > ora - VICINO_PER_MS
    fun occupato(): Boolean = operazione != null && operazione.fase != Fase.FATTO
}

// Normalizza l'invito come il master (maiuscolo, solo lettere e cifre). null = non valido.
fun normalizzaInvito(testo: String): String? {
    val s = testo.uppercase().filter { it.isLetterOrDigit() }
    return s.takeIf { it.length == LUNGHEZZA_INVITO && it.all { c -> c in ALFABETO_INVITO } }
}

@SuppressLint("MissingPermission")                       // i permessi si controllano prima di ogni azione
class BleClient(context: Context) {
    private val ctx = context.applicationContext
    private val adapter: BluetoothAdapter? =
        (ctx.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager?)?.adapter
    private val main = Handler(Looper.getMainLooper())
    private val store = Varchi(ctx)
    private val _ui = MutableStateFlow(
        UiBle(
            varchi = store.tutti(),
            titolo = runCatching { ChiaveTelefono.inizia(ctx); ChiaveTelefono.titolo() }.getOrDefault(""),
            ora = System.currentTimeMillis(),
        )
    )
    val ui: StateFlow<UiBle> = _ui.asStateFlow()

    private var gatt: BluetoothGatt? = null
    private var comando: BluetoothGattCharacteristic? = null
    private var esito: BluetoothGattCharacteristic? = null
    private var sfida: BluetoothGattCharacteristic? = null
    private var notifiche = false
    private var invitoInCorso: String? = null            // null = apertura; altrimenti abbinamento con questo invito
    private var nuovoVarco: Varco? = null                // abbinamento: il varco da salvare se va a buon fine
    private var attesaMaster = false
    private var riletto = false
    private var inAttesaRisposta = false
    private var abbinamento: Triple<ByteArray, ByteArray, ByteArray>? = null   // chiave dell'invito, sfida, nonce del telefono
    private var tentativi = 0                            // connessioni provate per l'operazione in corso
    private var ricercaInPausa = false                   // la ricerca si ferma durante un'operazione (telefoni vecchi: cercare e connettersi insieme fallisce)
    private val avviiRicerca = ArrayDeque<Long>()        // Android limita gli avvii della ricerca: 5 ogni 30 s

    val bluetoothAcceso: Boolean get() = adapter?.isEnabled == true

    // --- Vicinanza: ricerca leggera mentre l'app e' in primo piano ---

    private val scanCb = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, r: ScanResult) {
            val nome = r.scanRecord?.deviceName ?: runCatching { r.device.name }.getOrNull() ?: "Limen"
            val indirizzo = r.device.address
            val adesso = System.currentTimeMillis()
            main.post {
                val prima = _ui.value.vicini[indirizzo]
                // Uno stesso varco si annuncia molte volte al secondo: lo stato si aggiorna ogni tanto.
                if (prima != null && adesso - prima.visto < 2_000L && prima.nome == nome) return@post
                _ui.update { it.copy(vicini = it.vicini + (indirizzo to Vicino(indirizzo, nome, adesso)), ora = adesso) }
            }
        }

        override fun onScanFailed(errorCode: Int) {
            main.post { _ui.update { it.copy(ricerca = false) } }
        }
    }

    private val battito = object : Runnable {
        override fun run() {
            _ui.update { it.copy(ora = System.currentTimeMillis()) }
            main.postDelayed(this, BATTITO_MS)
        }
    }

    fun avviaVicinanza() {
        if (_ui.value.ricerca || !haPermessi(ctx)) return
        val scanner = adapter?.bluetoothLeScanner
        if (scanner == null || !bluetoothAcceso) return
        val filtri = listOf(ScanFilter.Builder().setServiceUuid(ParcelUuid(UUID_SERVIZIO_CRED)).build())
        val impostazioni = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_BALANCED).build()
        try {
            scanner.startScan(filtri, impostazioni, scanCb)
        } catch (_: Exception) {
            return
        }
        avviiRicerca.addLast(System.currentTimeMillis())
        while (avviiRicerca.size > 5) avviiRicerca.removeFirst()
        _ui.update { it.copy(ricerca = true, ora = System.currentTimeMillis()) }
        main.removeCallbacks(battito)
        main.postDelayed(battito, BATTITO_MS)
    }

    // Durante un'operazione la ricerca si ferma, se poi si puo' riavviare senza superare il limite di Android.
    private fun sospendiRicerca() {
        val adesso = System.currentTimeMillis()
        if (!_ui.value.ricerca || avviiRicerca.count { adesso - it < 30_000L } >= 4) return
        try { adapter?.bluetoothLeScanner?.stopScan(scanCb) } catch (_: Exception) {}
        ricercaInPausa = true
    }

    private fun riprendiRicerca() {
        if (!ricercaInPausa) return
        ricercaInPausa = false
        if (!_ui.value.ricerca) return                   // nel frattempo l'app e' andata in secondo piano
        _ui.update { it.copy(ricerca = false) }
        avviaVicinanza()
    }

    fun fermaVicinanza() {
        main.removeCallbacks(battito)
        ricercaInPausa = false
        if (!_ui.value.ricerca) return
        try { adapter?.bluetoothLeScanner?.stopScan(scanCb) } catch (_: Exception) {}
        _ui.update { it.copy(ricerca = false) }
    }

    // --- Varchi salvati ---

    // Telefono gia' abbinato all'impianto: per un altro varco basta salvarlo.
    fun aggiungi(indirizzo: String, dispositivo: String, nome: String) {
        _ui.update { it.copy(varchi = store.aggiungi(Varco(indirizzo, nome, dispositivo, System.currentTimeMillis()))) }
    }

    fun rinomina(indirizzo: String, nome: String) {
        if (nome.isBlank()) return
        _ui.update { it.copy(varchi = store.rinomina(indirizzo, nome.trim())) }
    }

    // Tolto l'ultimo varco, si toglie anche la credenziale: il telefono torna come nuovo.
    fun rimuovi(indirizzo: String) {
        val restano = store.rimuovi(indirizzo)
        if (restano.isEmpty()) ChiaveTelefono.dimentica()
        _ui.update { it.copy(varchi = restano, titolo = ChiaveTelefono.titolo()) }
    }

    fun togliMessaggio() {
        _ui.update { it.copy(messaggio = null) }
    }

    // --- Operazioni ---

    fun apri(indirizzo: String) {
        if (ChiaveTelefono.titolo().isEmpty()) { messaggio("This phone is not paired yet"); return }
        avvia(indirizzo, invito = null, varco = null)
    }

    // Abbinamento con l'invito: se va a buon fine il varco viene salvato con questo nome.
    fun abbina(indirizzo: String, dispositivo: String, nome: String, testoInvito: String) {
        val invito = normalizzaInvito(testoInvito) ?: run { messaggio("The invite is 10 letters or digits"); return }
        avvia(indirizzo, invito, Varco(indirizzo, nome, dispositivo, System.currentTimeMillis()))
    }

    private fun avvia(indirizzo: String, invito: String?, varco: Varco?) {
        if (_ui.value.occupato()) return
        if (!haPermessi(ctx)) { messaggio("Bluetooth permission needed"); return }
        if (!bluetoothAcceso) { messaggio("Bluetooth is off"); return }
        main.removeCallbacks(pulisci)
        chiudiGatt()
        invitoInCorso = invito
        nuovoVarco = varco
        _ui.update { it.copy(operazione = Operazione(indirizzo, invito != null, Fase.CONNESSIONE), messaggio = null) }
        tentativi = 0
        sospendiRicerca()
        main.postDelayed(scadenza, if (invito != null) SCADENZA_ABBINAMENTO_MS else SCADENZA_APERTURA_MS)
        connetti()
    }

    private fun connetti() {
        val indirizzo = _ui.value.operazione?.indirizzo ?: return
        tentativi++
        val device: BluetoothDevice? = try { adapter?.getRemoteDevice(indirizzo) } catch (_: Exception) { null }
        gatt = try { device?.connectGatt(ctx, false, gattCb, BluetoothDevice.TRANSPORT_LE) } catch (_: Exception) { null }
        if (gatt == null) termina(false, "Cannot connect")
    }

    // Connessione caduta prima di cominciare: capita (radio occupata, telefono lento). Si riprova senza disturbare.
    private val riprova = Runnable {
        val op = _ui.value.operazione
        if (op != null && op.fase == Fase.CONNESSIONE && gatt == null) connetti()
    }

    private val scadenza = Runnable { termina(false, "No reply") }

    private val pulisci = Runnable {
        _ui.update { if (it.operazione?.fase == Fase.FATTO) it.copy(operazione = null) else it }
    }

    // Fine dell'operazione, comunque sia andata: si chiude la connessione e si mostra l'esito per qualche secondo.
    private fun termina(ok: Boolean, risposta: String) {
        val op = _ui.value.operazione ?: return
        if (op.fase == Fase.FATTO) return
        main.removeCallbacks(scadenza)
        main.removeCallbacks(riprova)
        val varco = nuovoVarco
        chiudiGatt()
        riprendiRicerca()
        val adesso = System.currentTimeMillis()
        val varchi = when {
            op.abbinamento && ok && varco != null -> store.aggiungi(varco)
            op.abbinamento -> _ui.value.varchi
            else -> store.esito(op.indirizzo, risposta, ok, adesso)
        }
        // Se il varco ha risposto, e' vicino di sicuro: il pallino non aspetta la ricerca, che riparte adesso.
        val sentito = op.fase == Fase.ATTESA
        _ui.update {
            val prima = it.vicini[op.indirizzo]
            it.copy(
                operazione = op.copy(fase = Fase.FATTO, ok = ok, esito = risposta),
                varchi = varchi,
                titolo = ChiaveTelefono.titolo(),
                vicini = if (sentito && prima != null) it.vicini + (op.indirizzo to prima.copy(visto = adesso)) else it.vicini,
                ora = adesso,
            )
        }
        main.postDelayed(pulisci, MOSTRA_ESITO_MS)
    }

    fun chiudi() {
        fermaVicinanza()
        main.removeCallbacks(scadenza)
        main.removeCallbacks(riprova)
        main.removeCallbacks(pulisci)
        chiudiGatt()
    }

    private fun chiudiGatt() {
        main.removeCallbacks(leggiEsito)
        val g = gatt
        gatt = null; comando = null; esito = null; sfida = null; notifiche = false
        invitoInCorso = null; nuovoVarco = null; attesaMaster = false; inAttesaRisposta = false; abbinamento = null
        try { g?.disconnect(); g?.close() } catch (_: Exception) {}
    }

    private fun messaggio(m: String) {
        _ui.update { it.copy(messaggio = m) }
    }

    // Connessione pronta: si legge la sfida (nonce monouso); il comando parte quando arriva.
    private fun chiediSfida() {
        val g = gatt ?: return
        val s = sfida ?: return
        attesaMaster = false
        riletto = false
        abbinamento = null
        _ui.update { st -> st.copy(operazione = st.operazione?.copy(fase = Fase.ATTESA)) }
        val ok = try { g.readCharacteristic(s) } catch (_: Exception) { false }
        if (!ok) termina(false, "Cannot read the challenge")
    }

    private fun completaComando(nonce: ByteArray) {
        val invito = invitoInCorso
        val dati = try {
            if (invito == null) {
                val titolo = ChiaveTelefono.titolo()
                if (titolo.isEmpty()) { termina(false, "This phone is not paired yet"); return }
                byteArrayOf('O'.code.toByte()) + titolo.toByteArray(Charsets.US_ASCII) + ChiaveTelefono.macApertura(nonce)
            } else {
                // L'invito non viaggia: si manda una prova calcolata con la chiave derivata dall'invito.
                val k = ChiaveTelefono.chiaveInvito(invito)
                val nonceTel = ByteArray(16).also { SecureRandom().nextBytes(it) }
                abbinamento = Triple(k, nonce, nonceTel)
                byteArrayOf('P'.code.toByte()) + nonceTel + ChiaveTelefono.provaAbbinamento(k, nonce, nonceTel)
            }
        } catch (e: Exception) {
            termina(false, "Key error: ${e.message}")
            return
        }
        val g = gatt ?: return
        val c = comando ?: return
        if (scriviCaratteristica(g, c, dati)) inAttesaRisposta = true
        else termina(false, "Write failed")
    }

    @Suppress("DEPRECATION")
    private fun scriviCaratteristica(g: BluetoothGatt, c: BluetoothGattCharacteristic, dati: ByteArray): Boolean = try {
        if (Build.VERSION.SDK_INT >= 33) {
            g.writeCharacteristic(c, dati, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) == BluetoothStatusCodes.SUCCESS
        } else {
            c.writeType = BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
            c.value = dati
            g.writeCharacteristic(c)
        }
    } catch (_: Exception) { false }

    // Se la notifica non arriva in tempo, la risposta si legge dalla caratteristica.
    private val leggiEsito = Runnable {
        val g = gatt
        val e = esito
        val ok = g != null && e != null && try { g.readCharacteristic(e) } catch (_: Exception) { false }
        if (!ok) termina(false, "No reply")
    }

    private fun mostraRisposta(g: BluetoothGatt, uuid: UUID, valore: ByteArray?) {
        main.post {
            if (g != gatt || uuid != esito?.uuid || valore == null || !inAttesaRisposta) return@post
            main.removeCallbacks(leggiEsito)
            val r = String(valore, Charsets.UTF_8).trim()
            if (r == "READY") {                          // valore iniziale, letto prima che arrivasse la risposta vera
                main.postDelayed(leggiEsito, 500L)
                return@post
            }
            // «PAIR SENT»: lo slave ha inoltrato la richiesta; la risposta del master arriva dopo.
            if (r == "PAIR SENT") {
                if (!attesaMaster) { attesaMaster = true; main.postDelayed(leggiEsito, ATTESA_MASTER_MS) }
                return@post
            }
            // «PAIR OK <esadecimale>»: titolo e segreto cifrati con la chiave dell'invito.
            if (r.startsWith("PAIR OK")) {
                val esa = r.removePrefix("PAIR OK").trim()
                if (esa.length < LUNGHEZZA_CREDENZIALE_ESA && !riletto) {
                    riletto = true                       // notifica troncata dall'MTU: si rilegge intera
                    main.post(leggiEsito)
                    return@post
                }
                val a = abbinamento
                val titolo = if (a == null) null else runCatching {
                    ChiaveTelefono.riceviCredenziale(a.first, a.second, a.third, esa.chunked(2).map { it.toInt(16).toByte() }.toByteArray())
                }.getOrNull()
                if (titolo == null) termina(false, "The reply did not come from the real gate")
                else termina(true, "PAIR OK")
                return@post
            }
            termina(r.startsWith("OK"), r)
        }
    }

    @Suppress("DEPRECATION")
    private fun scriviDescrittore(g: BluetoothGatt, d: BluetoothGattDescriptor, valore: ByteArray): Boolean = try {
        if (Build.VERSION.SDK_INT >= 33) {
            g.writeDescriptor(d, valore) == BluetoothStatusCodes.SUCCESS
        } else {
            d.value = valore
            g.writeDescriptor(d)
        }
    } catch (_: Exception) { false }

    // Le callback arrivano su un thread di sistema: tutto passa al thread principale.
    private val gattCb = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            main.post {
                if (g != gatt) { runCatching { g.close() }; return@post }   // connessione vecchia
                if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                    // Scambi piu' fitti: l'apertura dura meno.
                    runCatching { g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_HIGH) }
                    // L'MTU piu' grande serve solo all'abbinamento (la risposta «PAIR OK» e' di 150 byte):
                    // per aprire si risparmia uno scambio.
                    if (invitoInCorso == null || !g.requestMtu(247)) g.discoverServices()
                } else if (newState == BluetoothProfile.STATE_DISCONNECTED || status != BluetoothGatt.GATT_SUCCESS) {
                    val op = _ui.value.operazione
                    if (op != null && op.fase == Fase.CONNESSIONE && tentativi < MAX_TENTATIVI_CONNESSIONE) {
                        // Non era ancora cominciato niente: si chiude e si riprova (l'invito e il varco restano).
                        gatt = null; comando = null; esito = null; sfida = null; notifiche = false
                        runCatching { g.disconnect(); g.close() }
                        main.postDelayed(riprova, PAUSA_TENTATIVO_MS)
                    } else {
                        termina(false, if (op?.fase == Fase.CONNESSIONE) "Gate not reachable" else "Connection lost")
                    }
                }
            }
        }

        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) {
            main.post { if (g == gatt) g.discoverServices() }
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            main.post {
                if (g != gatt) return@post
                val servizio = g.getService(UUID_SERVIZIO_CRED)
                sfida = servizio?.getCharacteristic(UUID_SFIDA)
                comando = servizio?.getCharacteristic(UUID_COMANDO)
                esito = servizio?.getCharacteristic(UUID_ESITO_CRED)
                val e = esito
                if (status != BluetoothGatt.GATT_SUCCESS || sfida == null || comando == null || e == null) {
                    termina(false, "Not a Limen gate"); return@post
                }
                val cccd = e.getDescriptor(UUID_CCCD)
                val avviata = g.setCharacteristicNotification(e, true) && cccd != null &&
                    scriviDescrittore(g, cccd, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
                if (!avviata) chiediSfida()              // senza notifiche le risposte si leggono
            }
        }

        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) {
            main.post {
                if (g != gatt || d.uuid != UUID_CCCD) return@post
                notifiche = status == BluetoothGatt.GATT_SUCCESS
                chiediSfida()
            }
        }

        override fun onCharacteristicWrite(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) {
            main.post {
                if (g != gatt || c.uuid != comando?.uuid || !inAttesaRisposta) return@post
                if (status != BluetoothGatt.GATT_SUCCESS) { termina(false, "Write failed"); return@post }
                main.postDelayed(leggiEsito, if (notifiche) ATTESA_NOTIFICA_MS else 500L)
            }
        }

        // Android 13+
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray) {
            mostraRisposta(g, c.uuid, value)
        }

        // Android 12 e precedenti
        @Suppress("OVERRIDE_DEPRECATION", "DEPRECATION")
        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic) {
            mostraRisposta(g, c.uuid, c.value?.copyOf())
        }

        override fun onCharacteristicRead(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray, status: Int) {
            letta(g, c.uuid, value, status)
        }

        @Suppress("OVERRIDE_DEPRECATION", "DEPRECATION")
        override fun onCharacteristicRead(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) {
            letta(g, c.uuid, c.value?.copyOf(), status)
        }

        private fun letta(g: BluetoothGatt, uuid: UUID, valore: ByteArray?, status: Int) {
            if (status != BluetoothGatt.GATT_SUCCESS) {
                main.post { if (g == gatt) termina(false, "Read failed") }
                return
            }
            if (uuid == UUID_SFIDA) {
                main.post {
                    if (g != gatt) return@post
                    if (valore == null || valore.size != 16) termina(false, "Bad challenge")
                    else completaComando(valore)
                }
            } else {
                mostraRisposta(g, uuid, valore)
            }
        }
    }
}
