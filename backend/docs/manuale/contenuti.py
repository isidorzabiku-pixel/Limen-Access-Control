# Limen — open-source access control
# Copyright (C) 2026 Isidor Zabiku
# SPDX-License-Identifier: AGPL-3.0-only
"""Testo dei manuali (italiano e inglese). Le etichette tra ui(...) sono quelle esatte dell'interfaccia."""


def sez_en(ui, im):
    return [
        ("Getting started", f"""
<p>Limen controls who can go through which door, and when. You use it from a web browser: open the address your administrator gave you (it looks like <b>https://server-address:8443</b>) and sign in.</p>
<ul>
<li><b>First sign-in:</b> the administrator account is <b>admin / admin</b>. A warning stays on screen until you change that password.</li>
<li><b>Change your password:</b> open {ui("System properties")} (operators: {ui("My account")}) and use {ui("Change your password")}.</li>
<li><b>Language:</b> the {ui("Language")} button at the top right of the same page. Each person chooses their own; the sign-in page has a selector too.</li>
<li>{ui("Sign out")} is at the bottom left.</li>
</ul>
<h3>Two kinds of users</h3>
<table><tr><th>Operator</th><td>People, visitors, groups, gates, time windows, access rules, monitoring, alerts, system info. Can change only their own password.</td></tr>
<tr><th>Administrator</th><td>Everything an operator does, plus {ui("System properties")} (users, backup, restart, data retention, API accounts), creating gates and pairing devices.</td></tr></table>"""),
        ("The screen at a glance", f"""
{im("dashboard", "The Dashboard: gates online, today's passages, denied accesses, active alerts, live passages and quick opening.")}
<ul>
<li><b>Left menu.</b> <i>Monitoring</i>: Dashboard, Monitoring, Alerts. <i>Access configuration</i>: People, Visitors, Groups, Gates, Time windows, Access rules. <i>System</i>: System info and System properties.</li>
<li><b>The ⓘ icon</b> next to a title or a field gives a one-sentence explanation: hover or tap it.</li>
<li><b>{ui("Send data to devices")}</b> (top right) sends your changes to the gates. See step 5 of section 4.</li>
<li><b>{ui("To do")}</b> (left menu) lists what needs attention: data to send and, for administrators, new devices to pair.</li>
</ul>"""),
        ("Everyday use", f"""
{im("monitoraggio", "Monitoring: live list of passages, with filters and CSV export.")}
<h3>Monitoring</h3>
<p>Every passage at every gate, live. Use {ui("Pause")} to freeze the list. Filter by outcome (All, Granted, Denied, Alarms), by exact outcome, by dates, by gate, or by person or credential. Choosing dates shows the history. {ui("Export CSV")} downloads what you see. The <b>›</b> button on a row opens the details, including the exact reason for a denied access.</p>
<h3>Alerts</h3>
<p>Devices that are offline and door alarms (for example a door left open). An alarm stays until someone presses {ui("Take over")}.</p>
<h3>Opening a door by hand</h3>
<p>On the Dashboard press {ui("Open")} next to the gate, or open the gate in {ui("Gates")} and press {ui("Open now")}. It works only if the gate is online, and opens it once.</p>"""),
        ("Setting up access", f"""
<p>Access works with <b>rules</b>. A rule says <i>which gates</i> and <i>when</i> (a time window). People receive rules directly or through groups. Five steps, in this order:</p>
<ol>
<li><b>Gates</b> (administrator). {ui("Gates")} → {ui("New gate")}: name, gate no. and the IP address of its device → {ui("Create gate")}. When the device connects it appears in {ui("To do")}: press {ui("Pair")}. The gate turns <i>online</i>.</li>
<li><b>Time windows.</b> {ui("Time windows")} → {ui("New time window")}: give it a name, then choose days and hours. {ui("Holidays →")} lists the days that windows set to exclude holidays skip.</li>
<li><b>People.</b> {ui("People")} → {ui("New person")} → {ui("Add")}, or {ui("Import from CSV")}. Press {ui("Properties ›")} on a person to see three tabs: {ui("Access")} (groups and direct rules), {ui("Access credentials")} (badges, PIN) and {ui("Presence")}. For guests use {ui("Visitors")}: they have an end date and lose access after it.</li>
<li><b>Groups and rules.</b> {ui("Groups")} → {ui("New group")} to gather people. {ui("Access rules")} → {ui("New rule")}: choose one or more gates and a time window (or {ui("Always (24 hours a day)")}). Then add the rule to a group or to a person.</li>
<li><b>Send data.</b> Press {ui("Send data to devices")}. Changes reach the doors only after this.</li>
</ol>
{im("persona", "A person's properties: groups, direct rules and effective permissions.")}
<div class="note"><b>Unknown badge?</b> A badge read at a gate but not assigned appears in People, under {ui("Credentials read but not recognised")}: press {ui("Assign to…")} and choose the person, or {ui("Ignore")}.</div>
<div class="note"><b>Check before you trust it.</b> {ui("See effective permissions")} in a person's properties shows what that person can really do. A rule with no gate or no time window is marked <i>incomplete</i> and grants nothing.</div>
{im("policy", "Access rules: gates, time window and status.")}
<div class="note warn"><b>Do not forget step 5.</b> After any change to people, rules, time windows or gates, press {ui("Send data to devices")}. Gates also receive data automatically every 6 hours, but do not wait for it.</div>"""),
        ("Controlling a gate", f"""
{im("varco", "A gate's panel: open now, operation, status and latest passages.")}
<p>Click a gate in {ui("Gates")} to open its panel, with three tabs.</p>
<ul>
<li><b>{ui("Control")}</b>: {ui("Open now")}; <b>Operation</b> with {ui("Configure")} (mode, readers, outputs, inputs); <b>Status</b>, where {ui("Deactivate")} makes the gate deny everyone until you reactivate it; latest passages.</li>
<li><b>{ui("Settings")}</b>: description, <i>Counts presence</i> (needed to know who is inside) and <i>anti-passback</i> (someone already inside cannot enter again until they have left).</li>
<li><b>{ui("Device")}</b>: connection status of the gate's device.</li>
</ul>
<h3>Gate modes</h3>
<table>
<tr><th>Normal</th><td>Badges open the door according to the rules.</td></tr>
<tr><th>Scheduled unlock</th><td>The door is free (no badge needed) during a chosen time window.</td></tr>
<tr><th>Maintenance</th><td>Badges do not open the door.</td></tr>
<tr><th>Emergency</th><td>The door is free for everyone until you go back to normal.</td></tr></table>
<p>The mode is chosen in {ui("Configure")} and takes effect immediately. To unlock <b>all</b> gates at once, use the {ui("Emergency")} box in {ui("Gates")} → {ui("Unlock all gates")}.</p>
<div class="note warn">Limen does not replace the legally required emergency-exit release or an intrusion alarm panel.</div>"""),
        ("Administration", f"""
{im("sistema", "System properties (administrators): backup, restart, data retention, API and users.")}
<ul>
<li><b>Users.</b> Create users, make them operator or administrator, reset a password, delete. There must always be at least one administrator.</li>
<li><b>Backup.</b> {ui("Download backup")} saves a copy of the database. Keep it somewhere safe.</li>
<li><b>Data retention.</b> How many days passages and log are kept (0 = forever). {ui("Save and apply now")} applies it immediately. Alerts still to be handled are never deleted.</li>
<li><b>Log cleanup.</b> {ui("Clean")} removes old information lines from the system log; passages and warnings are not touched.</li>
<li><b>App restart.</b> Works only when the app runs as a service.</li>
<li><b>API.</b> Accounts for other programs, and the link to the API documentation (PDF, English).</li>
<li><b>Language.</b> The default language for people who have not chosen one.</li>
<li><b>System info</b> (everyone): a health check of the server and of every gate.</li>
</ul>"""),
        ("When something is wrong", f"""
<table>
<tr><th>A gate is offline, or "Waiting for the device"</th><td>Check power and network cable, and the IP address in the {ui("Gates")} table. A new device needs {ui("Pair")} (administrator).</td></tr>
<tr><th>A change does not work at the door</th><td>Press {ui("Send data to devices")}.</td></tr>
<tr><th>Someone is denied</th><td>Open Monitoring, press <b>›</b> on the row: the reason is explained. See the table below.</td></tr>
<tr><th>An alarm will not go away</th><td>Press {ui("Take over")} in Alerts. If the door is still open, close it first.</td></tr>
<tr><th>Password forgotten</th><td>Another administrator resets it in {ui("System properties")} → Users.</td></tr></table>
<h3>Reasons for a denied access</h3>
<table>
<tr><th>Outside the time window</th><td>The rule is valid at other hours: check the time window.</td></tr>
<tr><th>No rule assigned</th><td>The person has no group and no direct rule.</td></tr>
<tr><th>Not authorised on this gate</th><td>The person has rules, but not for this gate.</td></tr>
<tr><th>Rule disabled</th><td>Enable the rule in {ui("Access rules")}.</td></tr>
<tr><th>Gate data not up to date</th><td>Press {ui("Send data to devices")}.</td></tr>
<tr><th>Credential deactivated / expired or not yet valid</th><td>Check the credential in the person's properties.</td></tr>
<tr><th>Credential not recognised</th><td>The badge is not assigned to anyone: assign it in People.</td></tr>
<tr><th>Gate disabled / in maintenance</th><td>Check the gate's status and mode.</td></tr>
<tr><th>Blocked by anti-passback</th><td>The person is recorded as already inside.</td></tr></table>"""),
    ]


def sez_it(ui, im):
    return [
        ("Per iniziare", f"""
<p>Limen controlla chi può passare da quale porta, e quando. Si usa da un browser: apri l'indirizzo che ti ha dato l'amministratore (del tipo <b>https://indirizzo-del-server:8443</b>) e accedi.</p>
<ul>
<li><b>Primo accesso:</b> l'account amministratore è <b>admin / admin</b>. Un avviso resta sullo schermo finché non cambi quella password.</li>
<li><b>Cambiare la password:</b> apri {ui("Proprietà di sistema")} (gli operatori: {ui("Il mio account")}) e usa {ui("Cambia la tua password")}.</li>
<li><b>Lingua:</b> il pulsante {ui("Lingua")} in alto a destra nella stessa pagina. Ognuno sceglie la propria; anche la pagina di accesso ha un selettore.</li>
<li>{ui("Esci")} è in basso a sinistra.</li>
</ul>
<h3>Due tipi di utente</h3>
<table><tr><th>Operatore</th><td>Persone, visitatori, gruppi, varchi, fasce orarie, regole di accesso, monitoraggio, alert, info sistema. Può cambiare solo la propria password.</td></tr>
<tr><th>Amministratore</th><td>Tutto ciò che fa un operatore, più {ui("Proprietà di sistema")} (utenti, backup, riavvio, conservazione dei dati, account API), la creazione dei varchi e l'abbinamento dei dispositivi.</td></tr></table>"""),
        ("La schermata in breve", f"""
{im("dashboard", "La Dashboard: varchi online, passaggi di oggi, accessi negati, alert attivi, passaggi in diretta e apertura rapida.")}
<ul>
<li><b>Menu a sinistra.</b> <i>Monitoraggio</i>: Dashboard, Monitoraggio, Alert. <i>Configurazione accessi</i>: Persone, Visitatori, Gruppi, Varchi, Fasce orarie, Regole di accesso. <i>Sistema</i>: Info sistema e Proprietà di sistema.</li>
<li><b>L'icona ⓘ</b> accanto a un titolo o a un campo dà una spiegazione di una frase: passaci sopra o toccala.</li>
<li><b>{ui("Scarica dati ai dispositivi")}</b> (in alto a destra) manda le tue modifiche ai varchi. Vedi il punto 5 della sezione 4.</li>
<li><b>{ui("Da fare")}</b> (menu a sinistra) elenca ciò che richiede attenzione: dati da inviare e, per gli amministratori, nuovi dispositivi da abbinare.</li>
</ul>"""),
        ("Uso quotidiano", f"""
{im("monitoraggio", "Monitoraggio: elenco dei passaggi in diretta, con filtri ed esportazione CSV.")}
<h3>Monitoraggio</h3>
<p>Ogni passaggio a ogni varco, in diretta. {ui("Pausa")} ferma l'elenco. Filtra per esito (Tutti, Concessi, Negati, Allarmi), per esito preciso, per date, per varco, o per persona o credenziale. Scegliendo le date vedi lo storico. {ui("Esporta CSV")} scarica ciò che vedi. Il pulsante <b>›</b> su una riga apre il dettaglio, con il motivo preciso di un accesso negato.</p>
<h3>Alert</h3>
<p>Dispositivi offline e allarmi della porta (per esempio una porta lasciata aperta). Un allarme resta finché qualcuno non preme {ui("Prendi in carico")}.</p>
<h3>Aprire una porta a mano</h3>
<p>Nella Dashboard premi {ui("Apri")} accanto al varco, oppure apri il varco in {ui("Varchi")} e premi {ui("Apri ora")}. Funziona solo se il varco è online, e apre una volta sola.</p>"""),
        ("Configurare gli accessi", f"""
<p>Gli accessi funzionano con le <b>regole</b>. Una regola dice <i>quali varchi</i> e <i>quando</i> (una fascia oraria). Le persone ricevono le regole direttamente o tramite i gruppi. Cinque passi, in quest'ordine:</p>
<ol>
<li><b>Varchi</b> (amministratore). {ui("Varchi")} → {ui("Nuovo varco")}: nome, n. varco e indirizzo IP del suo dispositivo → {ui("Crea varco")}. Quando il dispositivo si collega compare in {ui("Da fare")}: premi {ui("Abbina")}. Il varco diventa <i>online</i>.</li>
<li><b>Fasce orarie.</b> {ui("Fasce orarie")} → {ui("Nuova fascia")}: dalle un nome, poi scegli giorni e orari. {ui("Festività →")} elenca i giorni che le fasce impostate per escludere i festivi saltano.</li>
<li><b>Persone.</b> {ui("Persone")} → {ui("Nuova persona")} → {ui("Aggiungi")}, oppure {ui("Importa da CSV")}. Premi {ui("Proprietà ›")} su una persona per vedere tre schede: {ui("Accessi")} (gruppi e regole dirette), {ui("Titoli di accesso")} (badge, PIN) e {ui("Presenza")}. Per gli ospiti usa {ui("Visitatori")}: hanno una data di fine e dopo perdono l'accesso.</li>
<li><b>Gruppi e regole.</b> {ui("Gruppi")} → {ui("Nuovo gruppo")} per raccogliere le persone. {ui("Regole di accesso")} → {ui("Nuova regola")}: scegli uno o più varchi e una fascia oraria (oppure {ui("Sempre (24 ore su 24)")}). Poi aggiungi la regola a un gruppo o a una persona.</li>
<li><b>Invia i dati.</b> Premi {ui("Scarica dati ai dispositivi")}. Le modifiche arrivano alle porte solo dopo questo passaggio.</li>
</ol>
{im("persona", "Le proprietà di una persona: gruppi, regole dirette e permessi effettivi.")}
<div class="note"><b>Badge sconosciuto?</b> Un badge letto a un varco ma non assegnato compare in Persone, sotto {ui("Credenziali lette e non riconosciute")}: premi {ui("Assegna a…")} e scegli la persona, oppure {ui("Ignora")}.</div>
<div class="note"><b>Controlla prima di fidarti.</b> {ui("Vedi permessi effettivi")} nelle proprietà di una persona mostra cosa può fare davvero. Una regola senza varco o senza fascia oraria è segnata come <i>incompleta</i> e non concede nulla.</div>
{im("policy", "Regole di accesso: varchi, fascia oraria e stato.")}
<div class="note warn"><b>Non dimenticare il passo 5.</b> Dopo ogni modifica a persone, regole, fasce orarie o varchi, premi {ui("Scarica dati ai dispositivi")}. I varchi ricevono i dati anche in automatico ogni 6 ore, ma non aspettare.</div>"""),
        ("Gestire un varco", f"""
{im("varco", "Il pannello di un varco: apri ora, funzionamento, stato e ultimi passaggi.")}
<p>Clicca un varco in {ui("Varchi")} per aprire il suo pannello, con tre schede.</p>
<ul>
<li><b>{ui("Controllo")}</b>: {ui("Apri ora")}; <b>Funzionamento</b> con {ui("Configura")} (modalità, lettori, uscite, ingressi); <b>Stato</b>, dove {ui("Disattiva")} fa negare tutti al varco finché non lo riattivi; ultimi passaggi.</li>
<li><b>{ui("Impostazioni")}</b>: descrizione, <i>Conta le presenze</i> (serve per sapere chi è dentro) e <i>anti-passback</i> (chi è già dentro non può rientrare finché non è uscito).</li>
<li><b>{ui("Dispositivo")}</b>: stato della connessione del dispositivo del varco.</li>
</ul>
<h3>Modalità del varco</h3>
<table>
<tr><th>Normale</th><td>I badge aprono la porta secondo le regole.</td></tr>
<tr><th>Sblocco programmato</th><td>La porta è libera (nessun badge richiesto) in una fascia oraria scelta.</td></tr>
<tr><th>Manutenzione</th><td>I badge non aprono la porta.</td></tr>
<tr><th>Emergenza</th><td>La porta è libera per tutti finché non torni alla normalità.</td></tr></table>
<p>La modalità si sceglie in {ui("Configura")} e vale subito. Per sbloccare <b>tutti</b> i varchi insieme usa il riquadro {ui("Emergenza")} in {ui("Varchi")} → {ui("Sblocca tutti i varchi")}.</p>
<div class="note warn">Limen non sostituisce lo sblocco di emergenza richiesto dalle norme né una centrale antintrusione.</div>"""),
        ("Amministrazione", f"""
{im("sistema", "Proprietà di sistema (amministratori): backup, riavvio, conservazione dei dati, API e utenti.")}
<ul>
<li><b>Utenti.</b> Crea utenti, rendili operatori o amministratori, reimposta una password, eliminali. Deve esserci sempre almeno un amministratore.</li>
<li><b>Backup.</b> {ui("Scarica backup")} salva una copia del database. Tienila al sicuro.</li>
<li><b>Conservazione dei dati.</b> Per quanti giorni si conservano passaggi e log (0 = per sempre). {ui("Salva e applica ora")} la applica subito. Gli alert ancora da gestire non vengono mai eliminati.</li>
<li><b>Pulizia log.</b> {ui("Pulisci")} elimina le vecchie righe informative del log di sistema; passaggi e avvisi restano.</li>
<li><b>Riavvio app.</b> Funziona solo se l'app gira come servizio.</li>
<li><b>API.</b> Account per altri programmi, e il link alla documentazione dell'API (PDF, in inglese).</li>
<li><b>Lingua.</b> La lingua predefinita per chi non ne ha scelta una.</li>
<li><b>Info sistema</b> (per tutti): controllo dello stato del server e di ogni varco.</li>
</ul>"""),
        ("Quando qualcosa non va", f"""
<table>
<tr><th>Un varco è offline, o «In attesa del dispositivo»</th><td>Controlla alimentazione e cavo di rete, e l'indirizzo IP nella tabella {ui("Varchi")}. Un dispositivo nuovo richiede {ui("Abbina")} (amministratore).</td></tr>
<tr><th>Una modifica non funziona alla porta</th><td>Premi {ui("Scarica dati ai dispositivi")}.</td></tr>
<tr><th>Qualcuno viene negato</th><td>Apri il Monitoraggio, premi <b>›</b> sulla riga: il motivo è spiegato. Vedi la tabella qui sotto.</td></tr>
<tr><th>Un allarme non se ne va</th><td>Premi {ui("Prendi in carico")} negli Alert. Se la porta è ancora aperta, chiudila prima.</td></tr>
<tr><th>Password dimenticata</th><td>Un altro amministratore la reimposta in {ui("Proprietà di sistema")} → Utenti.</td></tr></table>
<h3>Motivi di un accesso negato</h3>
<table>
<tr><th>Fuori fascia oraria</th><td>La regola vale in altri orari: controlla la fascia oraria.</td></tr>
<tr><th>Nessuna regola assegnata</th><td>La persona non ha né gruppi né regole dirette.</td></tr>
<tr><th>Non autorizzato su questo varco</th><td>La persona ha delle regole, ma non per questo varco.</td></tr>
<tr><th>Regola disattivata</th><td>Attiva la regola in {ui("Regole di accesso")}.</td></tr>
<tr><th>Dati del varco non aggiornati</th><td>Premi {ui("Scarica dati ai dispositivi")}.</td></tr>
<tr><th>Credenziale disattivata / scaduta o non ancora valida</th><td>Controlla la credenziale nelle proprietà della persona.</td></tr>
<tr><th>Credenziale non riconosciuta</th><td>Il badge non è assegnato a nessuno: assegnalo in Persone.</td></tr>
<tr><th>Varco disattivato / in manutenzione</th><td>Controlla lo stato e la modalità del varco.</td></tr>
<tr><th>Bloccato da anti-passback</th><td>La persona risulta già dentro.</td></tr></table>"""),
    ]


IT = {"titolo": "Manuale d'uso", "h1": "Manuale<br><span>d'uso</span>",
      "sub": "Per operatori e amministratori. Come usare l'interfaccia web di Limen, passo per passo.", "sezioni": sez_it}
EN = {"titolo": "User manual", "h1": "User<br><span>manual</span>",
      "sub": "For operators and administrators. How to use Limen's web interface, step by step.", "sezioni": sez_en}
