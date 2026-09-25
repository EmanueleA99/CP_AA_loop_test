# Banchetto Test CarPlay / Android Auto — Raspberry Pi (GPIO)

Script per l'automazione del banco di test hardware-in-the-loop che verifica il risveglio e
l'avvio di Apple CarPlay / Android Auto su un infotainment (ICC), simulando la pressione del
pulsante KL15 tramite una scheda relè collegata ai pin GPIO di un Raspberry Pi e monitorando lo
schermo via ADB.

Il banco copre due scenari:

- **Deep Sleep** (`main_banchetto_deep_sleep_cp.py`, `main_banchetto_deep_sleep_aa.py`): il
  device parte da spento/deep sleep, richiede due impulsi relè in sequenza (accensione + KL15) e
  verifica sia il passaggio dell'icona di stato CarPlay/AA da grigia a verde, sia che la sessione
  sia effettivamente in foreground a schermo intero.
- **Soft Boot** (`main_banchetto_soft_cp.py` per CarPlay, `main_banchetto_soft_sleep_aa.py` per
  Android Auto): il device è già acceso, si simula solo il pulsante KL15 (un impulso singolo) e
  si verifica lo stesso passaggio grigio→verde + foreground.

Questo README copre il branch **`main`**, che pilota il relè direttamente sui pin **GPIO** del
Raspberry Pi tramite `gpiozero`. Il branch `ubuntu` fa la stessa cosa ma su una scheda relè
USB-HID generica pilotata via CLI `usbrelay` (utile per girare su un PC/Ubuntu qualsiasi senza
GPIO) — la logica di test è la stessa, cambia solo `pulse_relays()` in `banchetto_controller.py`.

---

## 1. Architettura del codice

Il progetto segue una struttura MVC leggera, con stato e configurazione condivisi tramite un
modulo globale:

| File | Ruolo |
|---|---|
| `banchetto_model.py` | Stato di sessione: `CONFIG` (parametri di test), timer (`session_start_perf`, `gray_detect_start_perf`, `second_relay_perf`...), timeline degli eventi (`mark_event`), `cooldown_restart()` (attende tra un ciclo e il successivo **e** verifica attivamente che il banco sia spento prima di ripartire) |
| `banchetto_view.py` | Tutto l'I/O di log e reportistica: log testuale di sessione, CSV di risultato (`append_csv`, `append_output_csv`, `append_deep_sleep_csv`) |
| `banchetto_utils.py` | Azioni fisiche: cattura schermo via ADB (`capture_png`, `capture_frame_bgr`), analisi colore con OpenCV, tap/swipe/motionevent via `adb shell input` |
| `banchetto_controller.py` | Logica del test: pilotaggio relè via GPIO (`pulse_relays`), connessione ADB (probe TCP + `adb connect`), attesa grigio→verde, validazione schermata CarPlay/AA, i due loop principali (`run_deep_sleep_loop`, `run_soft_loop`) |
| `esoTraceLogger.py` | Avvia/ferma le acquisizioni esoTrace (jTraceCapture) per le partizioni SYS, IVI e ConMod, una per ciclo di test |
| `main_banchetto_*.py` | Entry point: definiscono il `CONFIG` specifico del test (soglie, coordinate, path, pin relè) e lanciano il loop corrispondente |

Ogni `main_*.py` è indipendente e lanciabile singolarmente: `python main_banchetto_deep_sleep_cp.py`.

Tutti e quattro gli script condividono le stesse `wait_for_device()` / `end_of_test_relay_sequence()`
nel controller, che è anche dove è agganciato esoTrace (vedi [§2.4](#24-prerequisiti-esotrace-logging-sys--ivi--conmod))
— quindi la cattura delle trace è automatica per ogni ciclo di ogni script, senza wiring per-script.

---

## 2. Requisiti di sistema (installazione da zero su un nuovo Raspberry Pi)

### 2.1 Python e dipendenze

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

cd /path/al/progetto
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gpiozero lgpio
```

`requirements.txt` installa `opencv-python-headless`, `numpy` e `Pillow`. `gpiozero` + `lgpio`
pilotano i pin GPIO per il relè (non sono nel `requirements.txt` perché non servono sul branch
`ubuntu`, che usa `usbrelay`).

Ricordati di attivare il venv (`source venv/bin/activate`) in ogni nuova sessione di terminale
prima di lanciare uno script.

### 2.2 ADB (Android Debug Bridge)

```bash
sudo apt install -y android-tools-adb
adb version
which adb
```

Se il comando non è nel `PATH`, aggiorna il campo `ADB=` in ciascun `main_*.py` con il percorso
assoluto dell'eseguibile.

### 2.3 Scheda relè GPIO

Il relè è collegato direttamente ai pin GPIO del Raspberry Pi (board tipo Keyestudio a moduli
relè) e pilotato a livello TTL tramite `gpiozero.LED`, **non** tramite `usbrelay` (quello serve
solo per relay board USB-HID, usate sul branch `ubuntu`).

**Se cambi board relè o pin di collegamento**, verifica prima quali pin GPIO attivano
effettivamente il relè con lo script di supporto già presente nel repo:

```bash
python raspberry_relay.py   # cicla una serie di pin candidati, 1s ciascuno, osserva quale relè scatta
```

Una volta identificati i due pin corretti, aggiornali in `CONFIG` nei `main_banchetto_*.py`:

```python
RELAY_CHANNEL_1=<pin_GPIO_BCM>,
RELAY_CHANNEL_2=<pin_GPIO_BCM>,
RELAY_ACTIVE_LOW=False,   # True se il relè si chiude portando il pin a LOW: va verificato empiricamente
```

Se i permessi sul gruppo `gpio` mancano, aggiungi l'utente al gruppo e riavvia la sessione:

```bash
sudo usermod -aG gpio $USER
```

### 2.4 Prerequisiti esoTrace (logging SYS / IVI / ConMod)

Le acquisizioni esoTrace girano come `jtracecapture.jar` dentro finestre `lxterminal` dedicate,
una per partizione. Serve:

```bash
sudo apt install -y default-jdk lxterminal wmctrl
```

- **`default-jdk`**: fornisce `java`, usato per eseguire `jtracecapture.jar`.
- **`lxterminal`**: apre una finestra visibile per partizione (SYS/IVI/ConMod). **Richiede una
  sessione grafica/X** sulla macchina che esegue il banco — non funziona su una sessione SSH
  headless senza desktop environment.
- **`wmctrl`**: chiude le finestre SYS/IVI/ConMod a fine ciclo. Non strettamente necessario — se
  assente, i processi `java` vengono comunque terminati (via `pkill`), restano solo le finestre
  vuote aperte.
- **`jtracecapture.jar`** va posizionato nella cartella `jtrace/` alla radice del progetto (già
  incluso in questo checkout). Non è gestito da `requirements.txt` — è uno strumento a parte.

Se `java`, il jar o `lxterminal` mancano, `esoTraceLogger.py` logga un avviso chiaro e **salta la
trace per quel ciclo senza bloccare il test** — un prerequisito esoTrace mancante non blocca mai
il banco.

### 2.5 Verifica finale

Con il banco collegato (relè su GPIO, infotainment raggiungibile in rete):

```bash
python raspberry_relay.py        # deve azionare il relè sui pin attesi
adb connect 172.16.250.248:5555  # deve confermare la connessione
adb devices                      # il device deve apparire come "172.16.250.248:5555   device"
java -version                    # deve stampare una versione JDK/JRE
ls jtrace/jtracecapture.jar      # deve esistere
```

Se tutto risponde correttamente, il banco è pronto per eseguire i test.

---

## 3. Come lanciare i test

```bash
source venv/bin/activate

python main_banchetto_deep_sleep_cp.py     # test Deep Sleep + CarPlay
python main_banchetto_deep_sleep_aa.py     # test Deep Sleep + Android Auto
python main_banchetto_soft_cp.py           # test Soft Boot + CarPlay
python main_banchetto_soft_sleep_aa.py     # test Soft Boot + Android Auto
```

Ogni script esegue un **loop infinito**: al termine di ogni ciclo (PASSED/FAILED/PARTIALLY
FAILED) attende `RESTART_DELAY_SECONDS` **e verifica che il banco sia effettivamente spento**
prima di ricominciare (vedi [§5](#5-note-di-progettazione)), poi ricomincia automaticamente. Si
interrompe con `Ctrl+C`.

I due script Deep Sleep isolano il proprio traffico ADB su una porta server dedicata
(`ANDROID_ADB_SERVER_PORT=5038`), così un `adb` lanciato a mano nel terminale (porta di default
5037) non interferisce con il banco e non ne uccide il transport con un `kill-server` accidentale.
Per ispezionare il server dedicato dall'esterno: `ANDROID_ADB_SERVER_PORT=5038 adb devices -l`.

### Struttura dell'output

Ogni lancio dello script crea/aggiorna, dentro `output/<Nome_Test>/`:

- una cartella `cattura schermate_<timestamp>/` per ogni ciclo di test, contenente gli
  screenshot salvati durante quel ciclo (fase grigia, fase verde, controllo finale CarPlay,
  eventuale frame di fallimento, trace esoTrace se disponibili) e il log testuale
  `tempo_connessione.txt` di quel ciclo;
- uno o più CSV con **timestamp di lancio dello script** nel nome (es.
  `results_deepsleep_30_07_26_1547.csv`), che accumulano una riga per ogni ciclo eseguito in
  quella sessione. Un nuovo lancio dello script crea sempre un CSV nuovo, non sovrascrive né
  accoda a uno vecchio.

**CSV del test Deep Sleep** (`results_deepsleep_*.csv`), quattro colonne:

| Colonna | Significato |
|---|---|
| Timestamp evento | Data/ora della riga |
| Stato test | `PASSED` (verde + CarPlay in foreground) / `PARTIALLY PASSED` (verde ok, CarPlay non in foreground) / `FAILED` (icona mai diventata verde) |
| Last Mode | `PASSED`/`FAILED` in base a CarPlay in foreground o background, `N/A` se il test non ha mai raggiunto il verde |
| Connection time | Secondi trascorsi dal click relay al passaggio al verde, o `N/A` |

**CSV dei test Soft Boot** (`results_carplay_*.csv` / `results_androidauto_*.csv`): tre colonne
`timestamp`, `status` (`PASSED`/`PARTIALLY FAILED`/`FAILED`), `reason` (descrizione testuale).

---

## 4. Guida alla configurazione

Tutti i parametri di test si trovano nel blocco `CONFIG = SimpleNamespace(...)` in cima a
ciascun `main_banchetto_*.py`. Le sezioni seguenti spiegano come recuperare i valori corretti
quando cambi banco, scheda relè o infotainment.

### 4.1 Cambiare il display da catturare (`SCREEN_DISPLAY_ID`)

Gli infotainment automotive spesso espongono **più display fisici** (cluster, HMI centrale,
pannelli secondari). Se non specifichi quale catturare, `screencap` stampa un avviso
(`[Warning] Multiple displays were found...`) **direttamente nei byte dell'immagine**,
corrompendola — lo screenshot risulterà vuoto o non riconosciuto come PNG valido.

**Attenzione**: l'ID che serve a `screencap -d` è l'**ID fisico** del display (un numero lungo,
tipo `4633128631561747456`), **non** l'ID logico Android (0, 1, 2...) che si vede in
`dumpsys display`. Sono due numerazioni diverse e usare quella sbagliata produce cattura vuota
o silenziosamente errata, senza un messaggio d'errore chiaro.

**Procedura per trovare l'ID fisico corretto**, con il device connesso via ADB:

1. Elenca i display disponibili con relativa risoluzione:

   ```bash
   adb -s <ip>:<porta> shell dumpsys display | grep -E "mDisplayId=|width=|height="
   ```

   Nota le risoluzioni di ciascun display (es. `1920 x 816`) e il campo `uniqueId="local:XXXX"`
   di ciascuno: il numero dopo `local:` è quasi sempre l'ID fisico che ti serve.

2. Identifica quale display corrisponde al pannello che il test deve monitorare (di solito
   quello la cui risoluzione combacia con le coordinate ROI/tap già presenti nel `CONFIG`, o
   quello con la risoluzione più simile allo schermo HMI principale).

3. Testa la cattura con quell'ID:

   ```bash
   adb -s <ip>:<porta> exec-out screencap -d <ID_fisico> -p > /tmp/test.png
   file /tmp/test.png
   ```

   Deve rispondere `PNG image data, <larghezza> x <altezza>, ...`. Se dice `data` o `empty`,
   l'ID non è quello giusto — riprova con un altro valore `uniqueId` dall'elenco del punto 1.
   In alternativa, `dumpsys SurfaceFlinger --display-id` elenca gli stessi ID fisici in un
   formato diverso, utile come controllo incrociato.

4. Apri `/tmp/test.png` e verifica **visivamente** che sia davvero il pannello con l'icona
   CarPlay/Android Auto da monitorare (due display diversi possono avere la stessa risoluzione
   per coincidenza).

5. Una volta confermato, aggiorna in **tutti** i `main_banchetto_*.py`:

   ```python
   SCREEN_DISPLAY_ID=<ID_fisico_confermato>,
   ```

L'ID fisico di un pannello è stabile nel tempo (deriva dall'hardware del display, non cambia
al riavvio), quindi va aggiornato solo se cambi banco/infotainment fisico.

### 4.2 Cambiare la scheda relè / i pin GPIO

I due canali relè sono identificati dal **numero di pin GPIO** (numerazione BCM) a cui sono
collegati, usato da `gpiozero.LED` per pilotare il pin giusto.

**Procedura per trovare i pin corretti su una nuova board:**

1. Collega la board relè ai pin GPIO del Raspberry Pi.

2. Usa lo script di supporto `raspberry_relay.py` (alla radice del progetto) per ciclare una
   lista di pin candidati e osservare quale relè scatta:

   ```bash
   python raspberry_relay.py
   ```

   Modifica la lista `candidate_pins` nello script per includere i pin che vuoi testare. Lo
   script attiva ogni pin per 1 secondo: annota quale numero corrisponde a quale canale relè.

3. Verifica anche la **polarità**: se il relè si chiude quando il pin va a HIGH, `RELAY_ACTIVE_LOW`
   deve restare `False`; se invece si chiude quando il pin va a LOW, impostalo a `True`.

4. Se i permessi sul gruppo `gpio` mancano (errore tipo `PermissionError` da `gpiozero`):

   ```bash
   sudo usermod -aG gpio $USER
   # poi disconnetti/riconnetti la sessione (o riavvia) perché il nuovo gruppo abbia effetto
   ```

5. Aggiorna in tutti i `main_banchetto_*.py` i due pin usati per simulare la pressione del
   pulsante:

   ```python
   RELAY_CHANNEL_1=<pin_GPIO_BCM>,
   RELAY_CHANNEL_2=<pin_GPIO_BCM>,
   RELAY_ACTIVE_LOW=False,  # o True, in base al punto 3
   ```

Se la nuova scheda ha un solo canale, o serve pilotarne solo uno, si può modificare
`pulse_relays()` in `banchetto_controller.py` per usare un solo canale, oppure impostare
entrambe le costanti allo stesso pin.

### 4.3 Cambiare infotainment/IP di rete

```python
TARGET_IP="<nuovo IP>",
TARGET_PORT="5555",
TARGET_SERIAL="<nuovo IP>:5555",   # deve includere sempre la porta
```

**Importante**: `TARGET_SERIAL` deve sempre includere la porta (`:5555`). Un serial senza porta
non corrisponde esattamente al device registrato da `adb connect`, e i comandi ADB successivi
(`-s <serial>`) possono restare in attesa indefinita di un device che non trovano — uno dei
problemi più insidiosi da diagnosticare, perché ADB spesso non riporta un errore immediato.

### 4.4 Altri parametri principali

| Parametro | Significato |
|---|---|
| `LEFT_STATUS_ROI` | Coordinate `(x1, y1, x2, y2)` della regione di schermo dove si trova l'icona di stato CarPlay/AA da monitorare |
| `LEFT_GRAY_TARGET_HEX` / `LEFT_GREEN_TARGET_HEX` | Colori di riferimento (grigio = in attesa, verde/azzurro = connesso) |
| `LEFT_GRAY_DISTANCE_THRESHOLD` / `LEFT_GREEN_DISTANCE_THRESHOLD` | Soglie di tolleranza colore per considerare la ROI "grigia" o "verde" |
| `GREEN_DOMINANCE_MIN` / `GREEN_PIXELS_MIN_RATIO` | Criterio alternativo (dominanza del canale verde) per rilevare il verde, usato in OR con la soglia di distanza |
| `CARPLAY_REFERENCE_IMAGE` | Immagine di riferimento (`img/immagine_carplay.png` o `img/immagine_android.png`) usata per il template matching della schermata finale |
| `CARPLAY_SIMILARITY_THRESHOLD` | Soglia minima di similarità per considerare la schermata finale CarPlay/AA valida |
| `FPS` | Frequenza di campionamento durante l'attesa grigio→verde |
| `GREEN_TIMEOUT_SECONDS` | Timeout massimo di attesa del passaggio al verde prima di dichiarare fallito il ciclo |
| `RESTART_DELAY_SECONDS` | Attesa tra un ciclo di test e il successivo (dopo la quale si verifica anche che il banco sia spento, vedi [§5](#5-note-di-progettazione)) |
| `SECOND_RELAY_DELAY_SECONDS` | (solo deep sleep) Attesa tra il primo impulso relè (enabler/accensione) e il secondo (che avvia effettivamente lo startup se il sistema è in deep sleep — vedi [nota di progettazione](#5-note-di-progettazione)) |
| `ADB_COMMAND_TIMEOUT_SECONDS` | Timeout per ogni singolo comando ADB (default 10s se non specificato); evita che uno screencap/tap bloccato congeli lo script |
| `PORT_PROBE_TIMEOUT_SECONDS` / `PORT_PROBE_INTERVAL_SECONDS` | Timeout e intervallo del probe TCP "leggero" (non passa dal server adb) usato per rilevare quando la porta ADB del banco torna raggiungibile dopo il power-cycle |
| `ADB_CONNECT_CMD_TIMEOUT_SECONDS` / `ADB_VERIFY_TIMEOUT_SECONDS` | Timeout duri per, rispettivamente, il comando `adb connect` e la successiva verifica dello stato `device` |
| `DISCONNECT_CHECK_TIMEOUT_SECONDS` | (solo deep sleep) Timeout entro cui ci si aspetta che la porta ADB del banco smetta di rispondere dopo il click relay (conferma del power-cycle) |

---

## 5. Note di progettazione

Alcuni comportamenti del codice che potrebbero sembrare bug a prima vista, ma sono intenzionali:

- **Il timer di connessione parte dal secondo click relay, non dal primo (deep sleep)**: il
  primo impulso relè funge solo da **enabler** (accende il sistema), mentre è solo con il
  **secondo click** che l'infotainment, se in deep sleep, effettivamente avvia la procedura di
  startup. Misurare da subito dopo il primo click introdurrebbe nel "Connection time" un tempo
  morto non significativo (l'attesa configurata in `SECOND_RELAY_DELAY_SECONDS`). Il timer
  (`model.second_relay_perf`) viene quindi impostato subito dopo il secondo `pulse_relays()` in
  `wait_for_device()` (`banchetto_controller.py`), non dopo il primo.
- **`tap()` non passa `-d <SCREEN_DISPLAY_ID>`**: a differenza di `capture_png`/`capture_frame_bgr`
  (dove serve per evitare l'ambiguità multi-display su `screencap`), il comando `input tap`
  su questo banco non richiede l'ID display perché esiste un solo display touch — passare
  l'argomento in più risulta superfluo o viene ignorato. Se in futuro il banco dovesse avere più
  display touch, questo andrebbe rivisto aggiungendo `_input_display_args()` anche a `tap()`.
- **La connessione ADB non fa più "spam" di `adb connect`**: il vecchio approccio lanciava
  ripetutamente `adb connect` (o `adb devices`) contro il server adb, il che poteva accumulare
  transport "zombie" dopo diversi power-cycle KL15 e mostrare errori tipo `already connected` su
  un device in realtà non più raggiungibile. Ora `wait_for_device()` fa un **probe TCP puro**
  (`device_port_open()`, un semplice `socket.create_connection` a 20-50 Hz) che non tocca mai il
  server adb; solo quando la porta risponde viene lanciato **un solo** `adb connect` con timeout
  duri (`adb_connect_and_verify()`), seguito da `wait-for-device` + `get-state` per confermare che
  il transport sia realmente `device` e non solo `offline`/`connecting`. Il server adb viene anche
  resettato (`reset_adb_server()`) prima di ogni ciclo, mentre il banco è ancora spento, così il
  costo di riavvio del server non entra nella misura del tempo di connessione.
- **Il cooldown tra un ciclo e l'altro verifica che il banco sia davvero spento**
  (`cooldown_restart()` in `banchetto_model.py`): dopo l'attesa di `RESTART_DELAY_SECONDS`, uno
  probe TCP controlla che la porta ADB del banco non risponda più. Se risponde ancora (banco
  rimasto acceso), viene inviato un altro `pulse_relays()` e il countdown riparte da capo, finché
  il banco non risulta confermato spento — evita di iniziare un nuovo ciclo di test partendo da
  uno stato incoerente.

## 6. Troubleshooting rapido

| Sintomo | Causa probabile | Verifica |
|---|---|---|
| Screenshot vuoto/corrotto, `file` non lo riconosce come PNG | `SCREEN_DISPLAY_ID` sbagliato o mancante, display multipli | Sezione [4.1](#41-cambiare-il-display-da-catturare-screen_display_id) |
| Script bloccato senza nuovi log dopo "Connessione ADB confermata" | `TARGET_SERIAL` senza porta, o comando ADB in attesa indefinita | Verifica che `TARGET_SERIAL` includa `:5555`; controlla `ADB_COMMAND_TIMEOUT_SECONDS` |
| Relè non scatta / `RuntimeError: Libreria 'gpiozero' non disponibile` | `gpiozero`/`lgpio` non installati, o pin GPIO sbagliato | `pip install gpiozero lgpio`; Sezione [4.2](#42-cambiare-la-scheda-relè--i-pin-gpio) |
| `PermissionError` durante `pulse_relays()` | Utente non nel gruppo `gpio` | Sezione [2.3](#23-scheda-relè-gpio), `sudo usermod -aG gpio $USER` e riavvia la sessione |
| `adb: no devices/emulators found` | Device non connesso, IP cambiato, rete non raggiungibile | `adb connect <ip>:5555` manuale, controlla la rete verso il banco |
| Tap/swipe non hanno effetto sullo schermo | Display multipli, evento indirizzato al pannello sbagliato | Verifica `_input_display_args()` in `banchetto_utils.py`, vedi nota su `tap()` sopra |
| Nessuna finestra esoTrace si apre / trace mancanti | `java`/`lxterminal`/jar mancanti, o sessione headless senza X | Sezione [2.4](#24-prerequisiti-esotrace-logging-sys--ivi--conmod); il test prosegue comunque senza trace |
| Il banco riparte subito dopo il click relay di fine test | `cooldown_restart()` ha rilevato la porta ADB ancora raggiungibile e ha reinviato `pulse_relays()` | Comportamento atteso, vedi [§5](#5-note-di-progettazione); verifica che il relè spenga davvero il banco |
