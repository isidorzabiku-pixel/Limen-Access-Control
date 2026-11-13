// Limen — open-source access control
// Copyright (C) 2026 Isidor Zabiku
// SPDX-License-Identifier: AGPL-3.0-only
// Tre schermate: prima pagina con i varchi salvati (stato e «Open» a un tocco), aggiunta di un varco (l'unico
// momento in cui si cercano dispositivi) e dettaglio del varco. Colori del master Limen (base.html).
package org.limen.bletest

import android.app.Application
import android.bluetooth.BluetoothAdapter
import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.AndroidViewModel
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

// Colori del master (backend/templates/base.html)
private val Sfondo = Color(0xFF0B1222)
private val Scheda = Color(0xFF121D33)
private val SchedaAlta = Color(0xFF16233D)
private val Testo = Color(0xFFE2E8F0)
private val Tenue = Color(0xFF8B9BB4)
private val Bordo = Color(0xFF223150)
private val Accento = Color(0xFF38BDF8)
private val SuAccento = Color(0xFF05202F)
private val Verde = Color(0xFF22C55E)
private val Rosso = Color(0xFFFCA5A5)
private val BordoRosso = Color(0x61EF4444)

private val Colori = darkColorScheme(
    primary = Accento, onPrimary = SuAccento, background = Sfondo, onBackground = Testo,
    surface = Scheda, onSurface = Testo, surfaceVariant = SchedaAlta, onSurfaceVariant = Tenue,
    outline = Bordo, error = Rosso,
)

// Il client vive nel ViewModel: ruotare lo schermo non interrompe un'operazione.
class BleViewModel(app: Application) : AndroidViewModel(app) {
    val ble = BleClient(app)
    override fun onCleared() = ble.chiudi()
}

class MainActivity : ComponentActivity() {
    private val vm: BleViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = Colori) {
                Surface(Modifier.fillMaxSize(), color = Sfondo) { App(vm.ble) }
            }
        }
    }

    // La ricerca dei varchi vicini gira solo con l'app in primo piano.
    override fun onStart() {
        super.onStart()
        vm.ble.avviaVicinanza()
    }

    override fun onStop() {
        vm.ble.fermaVicinanza()
        super.onStop()
    }
}

@Composable
private fun App(ble: BleClient) {
    val ctx = LocalContext.current
    val ui by ble.ui.collectAsState()
    var permessi by remember { mutableStateOf(haPermessi(ctx)) }
    val chiediPermessi = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        permessi = haPermessi(ctx)
        ble.avviaVicinanza()
    }
    val accendiBluetooth = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) {
        ble.avviaVicinanza()
    }
    var schermo by rememberSaveable { mutableStateOf("casa") }     // casa, nuovo, varco
    var scelto by rememberSaveable { mutableStateOf("") }           // indirizzo del varco aperto nel dettaglio

    Column(Modifier.fillMaxSize().safeDrawingPadding().padding(horizontal = 18.dp, vertical = 14.dp)) {
        when {
            !permessi -> Avviso("Limen needs Bluetooth to talk to the gates.", "Allow Bluetooth") {
                chiediPermessi.launch(permessiNecessari())
            }

            !ble.bluetoothAcceso && !ui.ricerca -> Avviso("Bluetooth is off.", "Turn on Bluetooth") {
                accendiBluetooth.launch(Intent(BluetoothAdapter.ACTION_REQUEST_ENABLE))
            }

            schermo == "nuovo" -> {
                BackHandler { schermo = "casa" }
                Nuovo(ble, ui, indietro = { schermo = "casa" })
            }

            schermo == "varco" && ui.varchi.any { it.indirizzo == scelto } -> {
                BackHandler { schermo = "casa" }
                Dettaglio(ble, ui, ui.varchi.first { it.indirizzo == scelto }, indietro = { schermo = "casa" })
            }

            else -> Casa(ble, ui, nuovo = { schermo = "nuovo" }, apriVarco = { scelto = it; schermo = "varco" })
        }
    }
}

// ---------- Prima pagina ----------

@Composable
private fun Casa(ble: BleClient, ui: UiBle, nuovo: () -> Unit, apriVarco: (String) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(bottom = 14.dp), verticalAlignment = Alignment.CenterVertically) {
        Logo()
        Spacer(Modifier.width(8.dp))
        Text("Limen", fontWeight = FontWeight.Bold, fontSize = 20.sp, color = Testo)
        Spacer(Modifier.weight(1f))
        Tondo("+", nuovo)
    }
    if (ui.varchi.isEmpty()) {
        Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
            Text("No gates yet", fontWeight = FontWeight.SemiBold, fontSize = 18.sp, color = Testo)
            Spacer(Modifier.height(6.dp))
            Text("Add the first one with the invite you were given.", color = Tenue, fontSize = 14.sp)
            Spacer(Modifier.height(18.dp))
            Button(onClick = nuovo, shape = RoundedCornerShape(12.dp)) { Text("Add a gate", fontWeight = FontWeight.Bold) }
        }
        return
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        for (v in ui.varchi) {
            Riquadro {
                Row(
                    Modifier.fillMaxWidth().clickable { apriVarco(v.indirizzo) },
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(v.nome, Modifier.weight(1f), fontWeight = FontWeight.SemiBold, fontSize = 17.sp, color = Testo, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    StatoVarco(ui.vicino(v.indirizzo))
                    Spacer(Modifier.width(8.dp))
                    Text("›", color = Tenue, fontSize = 22.sp)
                }
                Spacer(Modifier.height(12.dp))
                BottoneApri(ble, ui, v.indirizzo)
            }
        }
        ui.messaggio?.let { Text(it, color = Tenue, fontSize = 13.sp) }
    }
}

// «Open»: un tocco fa tutto. Mostra l'avanzamento e poi, per qualche secondo, com'e' andata.
@Composable
private fun BottoneApri(ble: BleClient, ui: UiBle, indirizzo: String) {
    val op = ui.operazione?.takeIf { it.indirizzo == indirizzo && !it.abbinamento }
    val forma = RoundedCornerShape(12.dp)
    val alto = Modifier.fillMaxWidth().height(52.dp)
    when {
        op != null && op.fase != Fase.FATTO -> OutlinedButton(onClick = {}, enabled = false, modifier = alto, shape = forma, border = BorderStroke(1.dp, Bordo)) {
            CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = Accento)
            Spacer(Modifier.width(10.dp))
            Text("Opening…", color = Tenue, fontWeight = FontWeight.SemiBold)
        }

        op != null && op.ok -> OutlinedButton(
            onClick = {}, modifier = alto, shape = forma, border = BorderStroke(1.dp, Verde.copy(alpha = 0.4f)),
            colors = ButtonDefaults.outlinedButtonColors(containerColor = Verde.copy(alpha = 0.16f), contentColor = Verde),
        ) { Text("✓ Open", fontWeight = FontWeight.Bold, fontSize = 16.sp) }

        op != null -> OutlinedButton(
            onClick = { ble.apri(indirizzo) }, modifier = alto, shape = forma, border = BorderStroke(1.dp, BordoRosso),
            colors = ButtonDefaults.outlinedButtonColors(contentColor = Rosso),
        ) { Text(testoEsito(op), fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis) }

        ui.vicino(indirizzo) -> Button(onClick = { ble.apri(indirizzo) }, enabled = !ui.occupato(), modifier = alto, shape = forma) {
            Text("Open", fontWeight = FontWeight.Bold, fontSize = 16.sp)
        }

        // Non sentito di recente: si puo' provare lo stesso, ma il pulsante e' spento.
        else -> OutlinedButton(
            onClick = { ble.apri(indirizzo) }, enabled = !ui.occupato(), modifier = alto, shape = forma, border = BorderStroke(1.dp, Bordo),
            colors = ButtonDefaults.outlinedButtonColors(containerColor = SchedaAlta, contentColor = Tenue),
        ) { Text("Open", fontWeight = FontWeight.Bold, fontSize = 16.sp) }
    }
}

// ---------- Nuovo varco ----------

@Composable
private fun Nuovo(ble: BleClient, ui: UiBle, indietro: () -> Unit) {
    val salvati = ui.varchi.map { it.indirizzo }.toSet()
    val vicini = ui.vicini.values.filter { ui.vicino(it.indirizzo) && it.indirizzo !in salvati }.sortedBy { it.nome }
    var scelto by rememberSaveable { mutableStateOf("") }
    var invito by rememberSaveable { mutableStateOf("") }
    var nome by rememberSaveable { mutableStateOf("") }
    val dispositivo = vicini.firstOrNull { it.indirizzo == scelto }
    val abbinato = ui.titolo.isNotEmpty()
    val op = ui.operazione?.takeIf { it.abbinamento }

    // Abbinamento riuscito: il varco e' salvato, si torna alla prima pagina.
    LaunchedEffect(op?.fase, op?.ok) {
        if (op != null && op.fase == Fase.FATTO && op.ok) indietro()
    }

    Testata("New gate", indietro)
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Etichetta("Gates nearby")
        if (vicini.isEmpty()) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp, color = Accento)
                Spacer(Modifier.width(10.dp))
                Text("Looking for gates… stand close to one.", color = Tenue, fontSize = 14.sp)
            }
        }
        for (d in vicini) {
            val questo = d.indirizzo == scelto
            Row(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(Scheda)
                    .border(1.dp, if (questo) Accento else Bordo, RoundedCornerShape(12.dp))
                    .clickable { scelto = d.indirizzo; if (nome.isBlank()) nome = d.nome }
                    .padding(horizontal = 14.dp, vertical = 14.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Punto(true)
                Spacer(Modifier.width(10.dp))
                Text(d.nome, Modifier.weight(1f), fontWeight = FontWeight.SemiBold, color = Testo)
                if (questo) Text("selected", color = Tenue, fontSize = 13.sp)
            }
        }

        if (!abbinato) {
            Etichetta("Invite code")
            OutlinedTextField(
                value = invito, onValueChange = { invito = it.uppercase().take(14) }, singleLine = true,
                modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp),
                textStyle = MaterialTheme.typography.titleMedium.copy(fontFamily = FontFamily.Monospace),
                keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters),
            )
            Text("You get it from whoever runs the site. It lasts 10 minutes, in front of this gate.", color = Tenue, fontSize = 13.sp)
        } else {
            Text("This phone is already paired: no invite needed for another gate of the same site.", color = Tenue, fontSize = 13.sp)
        }

        Etichetta("Name")
        OutlinedTextField(
            value = nome, onValueChange = { nome = it.take(40) }, singleLine = true,
            modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(12.dp),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
        )

        if (op != null && op.fase == Fase.FATTO && !op.ok) Text(testoEsito(op), color = Rosso, fontSize = 14.sp)
        ui.messaggio?.let { Text(it, color = Rosso, fontSize = 14.sp) }

        Spacer(Modifier.height(6.dp))
        val pronto = dispositivo != null && nome.isNotBlank() && !ui.occupato() && (abbinato || normalizzaInvito(invito) != null)
        Button(
            onClick = {
                val d = dispositivo ?: return@Button
                ble.togliMessaggio()
                if (abbinato) { ble.aggiungi(d.indirizzo, d.nome, nome.trim()); indietro() }
                else ble.abbina(d.indirizzo, d.nome, nome.trim(), invito)
            },
            enabled = pronto, modifier = Modifier.fillMaxWidth().height(52.dp), shape = RoundedCornerShape(12.dp),
        ) {
            if (ui.occupato()) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = SuAccento)
                Spacer(Modifier.width(10.dp))
                Text("Pairing…", fontWeight = FontWeight.Bold)
            } else {
                Text(if (abbinato) "Add" else "Pair", fontWeight = FontWeight.Bold, fontSize = 16.sp)
            }
        }
    }
}

// ---------- Dettaglio del varco ----------

@Composable
private fun Dettaglio(ble: BleClient, ui: UiBle, v: Varco, indietro: () -> Unit) {
    var rinomina by remember { mutableStateOf(false) }
    var rimuovi by remember { mutableStateOf(false) }
    var riabbina by remember { mutableStateOf(false) }
    var testo by remember { mutableStateOf("") }
    val op = ui.operazione?.takeIf { it.indirizzo == v.indirizzo && it.abbinamento }

    Testata(v.nome, indietro)
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Riquadro {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                StatoVarco(ui.vicino(v.indirizzo))
                Spacer(Modifier.weight(1f))
                if (v.ultimaApertura > 0) Text("last opening ${quando(v.ultimaApertura)}", color = Tenue, fontSize = 13.sp)
            }
            Spacer(Modifier.height(12.dp))
            BottoneApri(ble, ui, v.indirizzo)
        }
        Riquadro {
            Voce("Name", "edit ›") { testo = v.nome; rinomina = true }
            Linea()
            Voce("Device", v.dispositivo.ifEmpty { v.indirizzo })
            Linea()
            Voce("This phone", if (ui.titolo.isEmpty()) "not paired" else ui.titolo.take(8) + "…")
            Linea()
            Voce("Added on", if (v.aggiuntoIl > 0) SimpleDateFormat("dd/MM/yyyy", Locale.getDefault()).format(Date(v.aggiuntoIl)) else "—")
            Linea()
            Voce("Last result", if (v.ultimoEsito.isEmpty()) "—" else testoRisposta(v.ultimoEsito))
        }
        when {
            op != null && op.fase != Fase.FATTO -> Text("Pairing…", color = Tenue, fontSize = 14.sp)
            op != null && op.ok -> Text("Phone paired again.", color = Verde, fontSize = 14.sp)
            op != null -> Text(testoEsito(op), color = Rosso, fontSize = 14.sp)
        }
        ui.messaggio?.let { Text(it, color = Rosso, fontSize = 14.sp) }
        TextButton(onClick = { testo = ""; ble.togliMessaggio(); riabbina = true }, enabled = !ui.occupato()) {
            Text("Pair this phone again", color = Accento)
        }
        OutlinedButton(
            onClick = { rimuovi = true }, modifier = Modifier.fillMaxWidth().height(50.dp), shape = RoundedCornerShape(12.dp),
            border = BorderStroke(1.dp, BordoRosso), colors = ButtonDefaults.outlinedButtonColors(contentColor = Rosso),
        ) { Text("Remove this gate", fontWeight = FontWeight.SemiBold) }
    }

    if (rinomina) {
        AlertDialog(
            onDismissRequest = { rinomina = false },
            title = { Text("Name") },
            text = { OutlinedTextField(value = testo, onValueChange = { testo = it.take(40) }, singleLine = true) },
            confirmButton = { TextButton(onClick = { ble.rinomina(v.indirizzo, testo); rinomina = false }) { Text("Save") } },
            dismissButton = { TextButton(onClick = { rinomina = false }) { Text("Cancel") } },
        )
    }
    if (riabbina) {
        AlertDialog(
            onDismissRequest = { riabbina = false },
            title = { Text("Pair again") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Type a new invite, in front of this gate.", color = Tenue, fontSize = 14.sp)
                    OutlinedTextField(
                        value = testo, onValueChange = { testo = it.uppercase().take(14) }, singleLine = true,
                        textStyle = MaterialTheme.typography.titleMedium.copy(fontFamily = FontFamily.Monospace),
                        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters),
                    )
                }
            },
            confirmButton = {
                TextButton(
                    onClick = { ble.abbina(v.indirizzo, v.dispositivo, v.nome, testo); riabbina = false },
                    enabled = normalizzaInvito(testo) != null,
                ) { Text("Pair") }
            },
            dismissButton = { TextButton(onClick = { riabbina = false }) { Text("Cancel") } },
        )
    }
    if (rimuovi) {
        val ultimo = ui.varchi.size == 1
        AlertDialog(
            onDismissRequest = { rimuovi = false },
            title = { Text("Remove this gate?") },
            text = {
                Text(
                    if (ultimo) "It is the last one: this phone's credential is removed too, and you will need a new invite."
                    else "You can add it again later without an invite."
                )
            },
            confirmButton = { TextButton(onClick = { rimuovi = false; ble.rimuovi(v.indirizzo); indietro() }) { Text("Remove", color = Rosso) } },
            dismissButton = { TextButton(onClick = { rimuovi = false }) { Text("Cancel") } },
        )
    }
}

// ---------- Pezzi comuni ----------

@Composable
private fun Avviso(testo: String, azione: String, alTocco: () -> Unit) {
    Column(Modifier.fillMaxSize(), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
        Logo()
        Spacer(Modifier.height(14.dp))
        Text(testo, color = Testo, fontSize = 16.sp)
        Spacer(Modifier.height(18.dp))
        Button(onClick = alTocco, shape = RoundedCornerShape(12.dp)) { Text(azione, fontWeight = FontWeight.Bold) }
    }
}

@Composable
private fun Testata(titolo: String, indietro: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(bottom = 14.dp), verticalAlignment = Alignment.CenterVertically) {
        Tondo("‹", indietro)
        Spacer(Modifier.width(10.dp))
        Text(titolo, fontWeight = FontWeight.Bold, fontSize = 20.sp, color = Testo, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun Tondo(segno: String, alTocco: () -> Unit) {
    Box(
        Modifier.size(40.dp).clip(CircleShape).background(Scheda).border(1.dp, Bordo, CircleShape).clickable(onClick = alTocco),
        contentAlignment = Alignment.Center,
    ) { Text(segno, color = Testo, fontSize = 22.sp) }
}

@Composable
private fun Riquadro(contenuto: @Composable () -> Unit) {
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Scheda)
            .border(1.dp, Bordo, RoundedCornerShape(16.dp)).padding(14.dp),
    ) { contenuto() }
}

@Composable
private fun Etichetta(testo: String) {
    Text(testo.uppercase(), color = Tenue, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 1.sp, modifier = Modifier.padding(top = 6.dp))
}

@Composable
private fun Punto(acceso: Boolean) {
    Box(Modifier.size(10.dp).clip(CircleShape).background(if (acceso) Verde else Tenue))
}

@Composable
private fun StatoVarco(vicino: Boolean) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Punto(vicino)
        Spacer(Modifier.width(6.dp))
        Text(if (vicino) "Nearby" else "Out of range", color = Tenue, fontSize = 13.sp)
    }
}

@Composable
private fun Voce(nome: String, valore: String, alTocco: (() -> Unit)? = null) {
    Row(
        (if (alTocco != null) Modifier.clickable(onClick = alTocco) else Modifier).fillMaxWidth().padding(vertical = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(nome, Modifier.weight(1f), color = Testo, fontSize = 15.sp)
        Text(valore, color = Tenue, fontSize = 13.sp, fontFamily = FontFamily.Monospace, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun Linea() {
    Box(Modifier.fillMaxWidth().height(1.dp).background(Bordo))
}

// Il logo di Limen: una L con un punto (come nel master).
@Composable
private fun Logo() {
    Canvas(Modifier.size(28.dp)) {
        val u = size.width / 64f
        val l = Path().apply { moveTo(22 * u, 12 * u); lineTo(22 * u, 50 * u); lineTo(48 * u, 50 * u) }
        drawPath(l, Testo, style = Stroke(width = 5 * u, cap = StrokeCap.Round, join = StrokeJoin.Round))
        drawCircle(Accento, radius = 3.8f * u, center = Offset(40 * u, 30 * u))
    }
}

private fun quando(ms: Long): String {
    val oggi = SimpleDateFormat("yyyyMMdd", Locale.US)
    val formato = if (oggi.format(Date(ms)) == oggi.format(Date())) "HH:mm" else "dd/MM HH:mm"
    return SimpleDateFormat(formato, Locale.getDefault()).format(Date(ms))
}

// Risposta del varco in parole.
private fun testoRisposta(r: String): String = when {
    r.startsWith("OK") -> "Open"
    r == "PAIR OK" -> "Paired"
    r == "NO" -> "Not enabled here"
    r == "NO negato_fuori_fascia" -> "Outside your hours"
    r == "NO negato_anti_passback" -> "Anti-passback"
    r == "NO negato_credenziale_scaduta" -> "Credential expired"
    r == "NO negato_varco_disattivato" || r == "NO negato_varco_manutenzione" -> "Gate out of service"
    r.startsWith("NO") -> "Access denied"
    r == "IGNORED" -> "Wait a moment"
    r == "PAIR NO" -> "Invite refused"
    r.startsWith("ERR") -> "Try again"
    else -> r
}

private fun testoEsito(op: Operazione): String = when {
    op.ok && op.abbinamento -> "Paired"
    op.abbinamento && op.esito == "PAIR NO" -> "Invite refused: wrong gate, or invite wrong, used or expired"
    else -> testoRisposta(op.esito)
}
