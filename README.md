# Banchetto Test CarPlay / Android Auto — Ubuntu

Script per l'automazione del banco di test hardware-in-the-loop che verifica il risveglio e
l'avvio di Apple CarPlay / Android Auto su un infotainment (ICC), simulando la pressione del
pulsante KL15 tramite una scheda relè USB e monitorando lo schermo via ADB.

Il banco copre due scenari:

- **Deep Sleep** (`main_banchetto_deep_sleep_cp.py`): il device parte da spento/deep sleep,
  richiede due impulsi relè in sequenza (accensione + KL15) e verifica sia il passaggio
  dell'icona di stato CarPlay da grigia a verde, sia che la sessione CarPlay sia effettivamente
  in foreground a schermo intero.
- **Soft Boot** (`main_banchetto_soft_cp.py` per CarPlay, `main_banchetto_soft_sleep_aa.py` per
  Android Auto): il device è già acceso, si simula solo il pulsante KL15 (un impulso singolo) e
  si verifica lo stesso passaggio grigio→verde + foreground.

---

## 1. Architettura del codice

Il progetto segue una struttura MVC leggera, con stato e configurazione condivisi tramite un
modulo globale:

| File | Ruolo |
|---|---|
| `banchetto_model.py` | Stato di sessione: `CONFIG` (parametri di test), timer (`session_start_perf`, `gray_detect_start_perf`, `second_relay_perf`...), timeline degli eventi (`mark_event`) |
| `banchetto_view.py` | Tutto l'I/O di log e reportistica: log testuale di sessione, CSV di risultato (`append_csv`, `append_output_csv`, `append_deep_sleep_csv`) |
| `banchetto_utils.py` | Azioni fisiche: cattura schermo via ADB (`capture_png`, `capture_frame_bgr`), analisi colore con OpenCV, tap/swipe/motionevent via `adb shell input`, impulsi relè via `usbrelay` |
| `banchetto_controller.py` | Logica del test: connessione ADB, attesa grigio→verde, validazione schermata CarPlay/AA, i due loop principali (`run_deep_sleep_loop`, `run_soft_loop`) |
| `main_banchetto_*.py` | Entry point: definiscono il `CONFIG` specifico del test (soglie, coordinate, path) e lanciano il loop corrispondente |

Ogni `main_*.py` è indipendente e lanciabile singolarmente: `python main_banchetto_deep_sleep_cp.py`.

---

## 2. Requisiti di sistema (installazione da zero su un nuovo Ubuntu)

### 2.1 Python e dipendenze

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

cd /path/al/progetto
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` installa `opencv-python-headless`, `numpy` e `Pillow` — nessuna libreria
Python è necessaria per il relè (gestito dal comando `usbrelay`, non da una libreria HID Python).

Ricordati di attivare il venv (`source venv/bin/activate`) in ogni nuova sessione di terminale
prima di lanciare uno script.

### 2.2 ADB (Android Debug Bridge)

```bash
sudo apt install -y android-tools-adb
```

Verifica che sia raggiungibile semplicemente con:

```bash
adb version
which adb
```

Se il comando non è nel `PATH`, aggiorna il campo `ADB=` in ciascun `main_*.py` con il percorso
assoluto dell'eseguibile.

### 2.3 usbrelay (pilotaggio della scheda relè)

```bash
sudo apt install -y usbrelay
```

`usbrelay`, lanciato senza argomenti, elenca tutte le schede relè collegate e il loro stato:

```bash
usbrelay
```

Se il comando dà errore di permessi (o richiede `sudo`), serve una regola udev che assegni i
permessi corretti al dispositivo HID. Vedi la sezione [4.2](#42-cambiare-la-scheda-relè) più
sotto per i dettagli su come crearla e su come trovare l'identificativo esatto della tua scheda.

### 2.4 Verifica finale

Con il banco collegato (relè via USB, infotainment raggiungibile in rete):

```bash
usbrelay                         # deve elencare la scheda senza errori
adb connect 172.16.250.248:5555  # deve confermare la connessione
adb devices                      # il device deve apparire come "172.16.250.248:5555   device"
```

Se tutto risponde correttamente, il banco è pronto per eseguire i test.

---

## 3. Come lanciare i test

```bash
source venv/bin/activate

python main_banchetto_deep_sleep_cp.py     # test Deep Sleep + CarPlay
python main_banchetto_soft_cp.py           # test Soft Boot + CarPlay
python main_banchetto_soft_sleep_aa.py     # test Soft Boot + Android Auto
```

Ogni script esegue un **loop infinito**: al termine di ogni ciclo (PASSED/FAILED/PARTIALLY
FAILED) attende `RESTART_DELAY_SECONDS`, poi ricomincia automaticamente. Si interrompe con
`Ctrl+C`.

### Struttura dell'output

Ogni lancio dello script crea/aggiorna, dentro `output/<Nome_Test>/`:

- una cartella `cattura schermate_<timestamp>/` per ogni ciclo di test, contenente gli
  screenshot salvati durante quel ciclo (fase grigia, fase verde, controllo finale CarPlay,
  eventuale frame di fallimento) e il log testuale `tempo_connessione.txt` di quel ciclo;
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

5. Una volta confermato, aggiorna in **tutti e tre** i `main_banchetto_*.py`:

   ```python
   SCREEN_DISPLAY_ID=<ID_fisico_confermato>,
   ```

L'ID fisico di un pannello è stabile nel tempo (deriva dall'hardware del display, non cambia
al riavvio), quindi va aggiornato solo se cambi banco/infotainment fisico.

### 4.2 Cambiare la scheda relè

I canali relè sono identificati da un nome tipo `QAAMZ_1` / `QAAMZ_2` (formato
`<serial_scheda>_<numero_canale>`), usato da `usbrelay` per indirizzare il comando al relè
giusto (rilevante se hai più schede collegate).

**Procedura per trovare gli identificativi della nuova scheda:**

1. Collega la scheda relè via USB.

2. Verifica che il sistema la veda a livello USB (le schede relè HID comuni usano il vendor ID
   `16c0` e product ID `05df`):

   ```bash
   lsusb | grep -i "16c0:05df"
   ```

3. Elenca i canali disponibili con `usbrelay` (senza argomenti): stampa una riga per ogni relè
   rilevato, nel formato `<SERIAL>_<NUMERO>=<STATO>` (0 = aperto, 1 = chiuso):

   ```bash
   usbrelay
   ```

   Esempio di output:
   ```
   QAAMZ_1=0
   QAAMZ_2=0
   ```

   Il prefisso prima del `_` (qui `QAAMZ`) è il serial univoco di quella scheda — cambia da
   scheda a scheda.

4. Se il comando dà errore di permessi, serve una regola udev. Crea
   `/etc/udev/rules.d/99-usbrelay.rules` con:

   ```
   SUBSYSTEM=="usb", ATTR{idVendor}=="16c0", ATTR{idProduct}=="05df", MODE="0666"
   KERNEL=="hidraw*", ATTRS{idVendor}=="16c0", ATTRS{idProduct}=="05df", MODE="0666"
   ```

   poi ricarica le regole e riconnetti la scheda:

   ```bash
   sudo udevadm control --reload-rules
   sudo udevadm trigger
   ```

5. Verifica di poter azionare un canale manualmente:

   ```bash
   usbrelay QAAMZ_1=1   # chiude il relè 1
   usbrelay QAAMZ_1=0   # lo riapre
   ```

6. Aggiorna in tutti i `main_banchetto_*.py` i due canali usati per simulare la pressione del
   pulsante:

   ```python
   RELAY_CHANNEL_1="<SERIAL>_1",
   RELAY_CHANNEL_2="<SERIAL>_2",
   ```

Se la nuova scheda ha un solo canale, o serve pilotarne solo uno, si può modificare
`pulse_relays()` in `banchetto_controller.py` per usare un solo canale, oppure impostare
entrambe le costanti allo stesso valore.

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
| `RESTART_DELAY_SECONDS` | Attesa tra un ciclo di test e il successivo |
| `SECOND_RELAY_DELAY_SECONDS` | (solo deep sleep) Attesa tra il primo impulso relè (enabler/accensione) e il secondo (che avvia effettivamente lo startup se il sistema è in deep sleep — vedi [nota di progettazione](#5-note-di-progettazione)) |
| `ADB_COMMAND_TIMEOUT_SECONDS` | Timeout per ogni singolo comando ADB (default 10s se non specificato); evita che uno screencap/tap bloccato congeli lo script |

---

## 5. Note di progettazione

Due comportamenti del codice che potrebbero sembrare bug a prima vista, ma sono intenzionali:

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

## 6. Troubleshooting rapido

| Sintomo | Causa probabile | Verifica |
|---|---|---|
| Screenshot vuoto/corrotto, `file` non lo riconosce come PNG | `SCREEN_DISPLAY_ID` sbagliato o mancante, display multipli | Sezione [4.1](#41-cambiare-il-display-da-catturare-screen_display_id) |
| Script bloccato senza nuovi log dopo "Connessione ADB confermata" | `TARGET_SERIAL` senza porta, o comando ADB in attesa indefinita | Verifica che `TARGET_SERIAL` includa `:5555`; controlla `ADB_COMMAND_TIMEOUT_SECONDS` |
| `usbrelay` dà errore di permessi | Regola udev assente/non caricata | Sezione [4.2](#42-cambiare-la-scheda-relè), punto 4 |
| `adb: no devices/emulators found` | Device non connesso, IP cambiato, rete non raggiungibile | `adb connect <ip>:5555` manuale, controlla la rete verso il banco |
| Tap/swipe non hanno effetto sullo schermo | Display multipli, evento indirizzato al pannello sbagliato | Verifica `_input_display_args()` in `banchetto_utils.py`, vedi nota su `tap()` sopra |
