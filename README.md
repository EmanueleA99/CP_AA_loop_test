# CP/AA HIL Test Bench — Ubuntu branch

Hardware-in-the-loop automation for a physical automotive infotainment unit (IVI). The bench
simulates KL15 button presses via a relay board, connects to the IVI over ADB (TCP/IP), and
monitors the screen to detect Apple CarPlay / Android Auto startup, covering two scenarios:

- **Deep Sleep** (`main_banchetto_deep_sleep_CP_ubuntu.py`, `main_banchetto_deep_sleep_AA_ubuntu.py`):
  the IVI starts powered off / in deep sleep. Two relay pulses are sent (power-on enabler, then
  the actual KL15 pulse), and the script waits for the ADB connection to drop and come back
  before checking the status icon and the final CarPlay/AA screen.
- **Soft Boot** (`main_banchetto_soft_CP_ubuntu.py`, `main_banchetto_soft_sleep_AA_ubuntu.py`):
  the IVI is already on. A single relay pulse simulates the KL15 press, then the same
  gray→green icon check and final screen validation run.

This README covers the **`ubuntu` branch only**. On this branch the relay board is a generic
USB-HID relay compatible with the `usbrelay` CLI, driven over USB regardless of the host
machine. The `main` branch differs mainly in that it targets a Raspberry Pi driving the relay
directly via its GPIO pins (`gpiozero`) instead of a USB relay board — it's being reworked
separately and is out of scope here.

---

## 1. Code architecture

Lightweight MVC, with shared state/config held in `banchetto_model_ubuntu`:

| File | Role |
|---|---|
| `banchetto_model_ubuntu.py` | `CONFIG` holder, session timers (`session_start_perf`, `gray_detect_start_perf`, `second_relay_perf`...), event timeline (`mark_event`), `cooldown_restart()` (waits between cycles **and** actively verifies the IVI is powered off before starting the next one) |
| `banchetto_view_ubuntu.py` | All logging/reporting I/O: per-session text log, result CSVs (`append_csv`, `append_output_csv`, `append_deep_sleep_csv`) |
| `banchetto_utils_ubuntu.py` | Physical actions: ADB screen capture (`capture_png`, `capture_frame_bgr`), OpenCV colour analysis, tap/swipe/motionevent via `adb shell input`, relay pulses via `usbrelay` |
| `banchetto_controller_ubuntu.py` | Test logic: ADB connect/disconnect handling, gray→green wait loop, CarPlay/AA screen validation, the two main loops (`run_deep_sleep_loop`, `run_soft_loop`) |
| `esoTraceLogger_ubuntu.py` | Starts/stops esoTrace (jTraceCapture) acquisitions for the SYS, IVI and ConMod partitions, one per test cycle |
| `main_banchetto_*_ubuntu.py` | Entry points: define the test-specific `CONFIG` (thresholds, coordinates, paths) and launch the matching loop |

Each `main_*.py` is independent and can be run on its own, e.g. `python main_banchetto_deep_sleep_CP_ubuntu.py`.

All four main scripts share the exact same `wait_for_device()` / `end_of_test_relay_sequence()`
functions in the controller, which is also where esoTrace is hooked in (see [§5](#5-esotrace-integration)) —
so trace capture is automatic for every cycle of every script, with no per-script wiring needed.

---

## 2. System requirements (fresh Ubuntu / Raspberry Pi setup)

### 2.1 Python and dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

cd /path/to/project
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` installs `opencv-python-headless`, `numpy` and `Pillow`. Remember to
activate the venv (`source venv/bin/activate`) in every new terminal session before running a
script.

### 2.2 ADB (Android Debug Bridge)

```bash
sudo apt install -y android-tools-adb
adb version
which adb
```

If `adb` is not on the `PATH`, update `ADB=` in each `main_*.py` with the absolute path to the
executable.

### 2.3 usbrelay (relay board)

This branch drives the relay via a generic **USB-HID relay board**, controlled through the
`usbrelay` CLI — this is what lets the bench run on plain Ubuntu (or a Raspberry Pi running
Ubuntu desktop) without depending on GPIO pins. The `main` branch instead targets a relay board
wired directly to a Raspberry Pi's GPIO header, driven via `gpiozero`; the two are not
interchangeable without also swapping `pulse_relays()` in `banchetto_controller_ubuntu.py`.

```bash
sudo apt install -y usbrelay
usbrelay   # lists all connected relay boards and their channel states
```

If this errors out on permissions, a udev rule is needed for the HID device (vendor `16c0`,
product `05df`):

```bash
sudo tee /etc/udev/rules.d/99-usbrelay.rules <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="16c0", ATTR{idProduct}=="05df", MODE="0666"
KERNEL=="hidraw*", ATTRS{idVendor}=="16c0", ATTRS{idProduct}=="05df", MODE="0666"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger
```

### 2.4 esoTrace prerequisites (SYS / IVI / ConMod logging)

esoTrace acquisitions run as `jtracecapture.jar` inside dedicated `lxterminal` windows, one per
partition. This needs:

```bash
sudo apt install -y default-jdk lxterminal wmctrl
```

- **`default-jdk`**: provides `java`, used to run `jtracecapture.jar`.
- **`lxterminal`**: opens one visible terminal window per partition (SYS/IVI/ConMod). This
  **requires a graphical/X session** on the machine running the bench — it will not work over a
  plain headless SSH session without a desktop environment. If your bench runs headless, let me
  know and the module can be adapted to run the java processes in the background instead of in
  terminal windows (same start/stop behaviour, no visible windows).
- **`wmctrl`**: used to close the SYS/IVI/ConMod terminal windows when a cycle ends. Not
  strictly required — if missing, the underlying `java` processes are still killed (via `pkill`),
  only the empty terminal windows are left open.
- **`jtracecapture.jar`** itself must be placed in the `jtrace/` folder at the project root
  (already present in this checkout). It is *not* pulled by `requirements.txt` — it's a
  standalone tool copied in manually.

If any of `java`, the jar, or `lxterminal` is missing, `esoTraceLogger_ubuntu.py` logs a clear
warning and **skips tracing for that cycle without stopping the test** — a missing esoTrace
prerequisite never blocks the bench.

### 2.5 Final check

With the bench wired up (relay via USB, IVI reachable on the network):

```bash
usbrelay                         # must list the board with no errors
adb connect 172.16.250.248:5555  # must confirm the connection
adb devices                      # device must show as "172.16.250.248:5555   device"
java -version                    # must print a JDK/JRE version
ls jtrace/jtracecapture.jar      # must exist
```

---

## 3. Running the tests

```bash
source venv/bin/activate

python main_banchetto_deep_sleep_CP_ubuntu.py    # Deep Sleep + CarPlay
python main_banchetto_deep_sleep_AA_ubuntu.py    # Deep Sleep + Android Auto
python main_banchetto_soft_CP_ubuntu.py          # Soft Boot + CarPlay
python main_banchetto_soft_sleep_AA_ubuntu.py    # Soft Boot + Android Auto
```

Each script runs an **infinite loop**: at the end of every cycle (PASSED/FAILED/PARTIALLY
FAILED) it waits `RESTART_DELAY_SECONDS`, actively checks that the IVI is really powered off
before continuing (see `cooldown_restart()` in `banchetto_model_ubuntu.py`), then starts the
next cycle automatically. Stop with `Ctrl+C`.

### Output structure

Every launch creates/updates, inside `output/<Test_Name>/`:

- one `cattura schermate_<timestamp>/` folder **per test cycle**, containing:
  - the screenshots captured during that cycle (gray phase, green phase, final CarPlay/AA
    check frames, any failure frame),
  - the per-cycle text log `tempo_connessione.txt`,
  - **the three esoTrace files for that cycle** (`traceSYS.esotrace_...`,
    `traceIVI.esotrace_...`, `traceConMod.esotrace_...`) — see [§5](#5-esotrace-integration).
- one or more CSVs named with the **script launch timestamp** (e.g.
  `results_deepsleep_30_07_26_1547.csv`), accumulating one row per cycle run in that session. A
  new launch always creates a new CSV rather than overwriting or appending to an old one.

**Deep Sleep CSV** (`results_deepsleep_*.csv` / `results_deepsleep_AA_*.csv`), four columns:

| Column | Meaning |
|---|---|
| Timestamp evento | Row date/time |
| Stato test | `PASSED` (green + CarPlay/AA in foreground) / `PARTIALLY PASSED` (green ok, not in foreground) / `FAILED` (icon never turned green) |
| Last Mode | `PASSED`/`FAILED` depending on foreground/background, `N/A` if green was never reached |
| Connection time | Seconds from the KL15 relay click to the green transition, or `N/A` |

**Soft Boot CSVs** (`results_carplay_*.csv` / `results_androidauto_*.csv`): three columns
`timestamp`, `status` (`PASSED`/`PARTIALLY FAILED`/`FAILED`), `reason` (free-text description).

---

## 4. Configuration guide — `CONFIG` parameters

All test parameters live in the `CONFIG = SimpleNamespace(...)` block at the top of each
`main_banchetto_*_ubuntu.py`. The four scripts share the same parameter *names*; only the
values differ (ROI coordinates, colour targets, output folders, timings). This section explains
what each group of parameters actually controls, using `main_banchetto_deep_sleep_CP_ubuntu.py`
and `main_banchetto_deep_sleep_AA_ubuntu.py` as the reference (Soft Boot scripts use the same
fields minus the deep-sleep-only ones, see [§4.6](#46-fields-only-present-in-soft-boot-scripts)).

### 4.1 ADB / network connection

| Parameter | Effect |
|---|---|
| `ADB` | Path to the `adb` executable. `"adb"` assumes it's on the `PATH`; use an absolute path otherwise. |
| `TARGET_IP` | IP address of the IVI on the bench network. |
| `TARGET_PORT` | ADB TCP port on the IVI, normally `"5555"`. |
| `TARGET_SERIAL` | **Must always be `"<TARGET_IP>:<TARGET_PORT>"`.** All ADB calls use this as the `-s` device identifier; a serial without the port won't match the device registered by `adb connect`, and later commands can hang waiting for a device that "isn't found" — one of the least obvious failure modes to diagnose, since ADB often doesn't report an immediate, clear error. |
| `ADB_CONNECT_TIMEOUT_SECONDS` | Overall time budget for reconnecting after the relay pulses, before the cycle is declared FAILED (`Device ADB ... non disponibile`). |
| `ADB_SINGLE_CONNECT_TIMEOUT_SECONDS` | Local timeout for *each individual* `adb connect` attempt during that budget (the loop retries immediately on timeout). |
| `ADB_CONNECT_SPAM_INTERVAL` | Pause between successive `adb connect` retries when a single attempt does *not* time out (`0.0` in the deep sleep scripts = retry back-to-back; `0.10` in the soft scripts). |
| `DISCONNECT_CHECK_TIMEOUT_SECONDS` | *(optional, not set explicitly in these scripts — defaults to 15s)* How long to wait, after the second relay pulse, for the device to actually disappear from `adb devices` before starting to reconnect. This confirms the KL15 power-cycle genuinely happened rather than proceeding straight to a reconnect attempt that would just time out later. |
| `ADB_COMMAND_TIMEOUT_SECONDS` | *(optional, defaults to 10s)* Timeout applied to every individual ADB command (screencap, tap, swipe...), so a stuck command can't freeze the whole script. |

The controller also automatically detects and recovers from **stale "already connected" ADB
transports** (a known issue after KL15 power-cycles, where `adb` keeps reporting a dead
connection as alive) — this logic (`adb_disconnect_target`, consecutive-stale detection,
escalation to `adb kill-server`) is not itself configurable; it kicks in automatically inside
`wait_for_device()`.

### 4.2 Relay / KL15 simulation

| Parameter | Effect |
|---|---|
| `RELAY_CHANNEL_1` / `RELAY_CHANNEL_2` | Channel names as reported by `usbrelay` (format `<board_serial>_<channel_number>`, e.g. `QAAMZ_1`). Both are pulsed together to simulate one button press. |
| `RELAY_PULSE_HOLD_SECONDS` | How long the relay stays closed (button "held down") before opening again. |
| `SECOND_RELAY_DELAY_SECONDS` | **Deep sleep only.** Delay between the *first* relay pulse (power-on enabler) and the *second* (the pulse that actually starts the boot sequence if the IVI is in deep sleep). See the design note in [§6](#6-design-notes) on why the connection timer starts from the second pulse, not the first. |

### 4.3 Screen capture / display

| Parameter | Effect |
|---|---|
| `SCREEN_DISPLAY_ID` | **Physical** display ID passed to `screencap -d` (not the logical Android display ID from `dumpsys display`). Needed whenever the IVI exposes more than one physical panel — without it, `screencap` can prepend a warning string to the PNG bytes and corrupt every screenshot. Stable per piece of hardware; only needs changing if you swap to a different bench/IVI unit. |
| `FPS` | Sampling rate for the gray→green monitoring loop (`5` in the deep sleep scripts, `2` in the soft scripts) and for the pacing between the `PRECHECK_FRAMES` final-check captures. Higher = more responsive detection, more ADB/CPU load. |
| `PRECHECK_FRAMES` | Number of consecutive frames averaged when validating the final CarPlay/AA screen against the reference image. |

### 4.4 Colour / icon detection (gray→green transition)

| Parameter | Effect |
|---|---|
| `LEFT_STATUS_ROI` | `(x1, y1, x2, y2)` region of the screen where the CarPlay/AA status icon is monitored. Differs between the CP and AA scripts because the icon sits in a slightly different spot/size on each screen. |
| `LEFT_GRAY_TARGET_HEX` | Reference colour for the "waiting" state. |
| `LEFT_GREEN_TARGET_HEX` | Reference colour for the "connected" state — despite the name this is CarPlay's green (`#60e255`) in the CP script and Android Auto's blue (`#15a2e7`) in the AA scripts; the variable name is shared but the meaning ("target/connected colour") is the same. |
| `LEFT_GRAY_DISTANCE_THRESHOLD` / `LEFT_GREEN_DISTANCE_THRESHOLD` | Maximum Euclidean colour distance (in BGR space) from the reference colour for the ROI to count as "gray" / "green". |
| `GREEN_DOMINANCE_MIN` / `GREEN_PIXELS_MIN_RATIO` | Alternative green-detection rule, OR'd with the distance threshold: the green channel must dominate red/blue by at least `GREEN_DOMINANCE_MIN`, over at least `GREEN_PIXELS_MIN_RATIO` of the ROI's pixels. Useful when the exact colour drifts slightly but is still clearly green. |
| `GREEN_TIMEOUT_SECONDS` | Hard timeout for the gray→green wait loop; if exceeded, the cycle is declared FAILED (and, for deep sleep, the no-green recovery gesture in §4.5 is attempted). |

### 4.5 Final-screen validation and failure recovery (deep sleep)

| Parameter | Effect |
|---|---|
| `CARPLAY_REFERENCE_IMAGE` | Reference image used for template matching against the final screen (`img/immagine_carplay.png` for CP, `img/immagine_android.png` for AA). |
| `CARPLAY_SIMILARITY_THRESHOLD` | Minimum average similarity (over `PRECHECK_FRAMES`) for the final screen to count as a valid CarPlay/AA foreground screen. |
| `FINAL_FAIL_TAP_X` / `FINAL_FAIL_TAP_Y` | Coordinates tapped as a recovery/dismiss action when the final screen check fails (icon went green, but the foreground app isn't CarPlay/AA). |
| `FINAL_FAIL_PIXEL_X` / `FINAL_FAIL_PIXEL_Y` / `FINAL_FAIL_PIXEL_TARGET_HEX` / `FINAL_FAIL_PIXEL_DISTANCE_THRESHOLD` | Single-pixel check used to decide whether the **"no-green" special recovery gesture** applies (see below): if this pixel on the failure screenshot is close enough to the target colour, the gesture is attempted. |
| `FINAL_FAIL_RECOVERY_TAP_X` / `FINAL_FAIL_RECOVERY_TAP_Y` | Tap coordinates for that recovery, sent before the hold+swipe gesture below. |
| `SPECIAL_HOLD_START_X` / `SPECIAL_HOLD_START_Y` | Starting point for the special recovery gesture (a touch-and-hold). |
| `SPECIAL_SWIPE_END_X` / `SPECIAL_SWIPE_END_Y` | End point the hold is dragged to. |
| `SPECIAL_HOLD_BEFORE_SWIPE_MS` | How long the touch is held at the start point before the drag begins. |
| `SPECIAL_TOTAL_SWIPE_MS` | *(present in CONFIG; currently informational — the actual drag in `special_hold_and_swipe()` is driven by discrete DOWN/MOVE/UP `motionevent` calls, not a timed `swipe` command)* |

This whole block only fires when the icon never turns green within `GREEN_TIMEOUT_SECONDS`
(`maybe_execute_no_green_final_recovery` in `banchetto_utils_ubuntu.py`): it's a best-effort
attempt to unstick the IVI (e.g. dismiss a stuck dialog) before the cycle ends and the relay
powers everything off for the next attempt.

### 4.6 Fields only present in Soft Boot scripts

The Soft Boot scripts (`main_banchetto_soft_CP_ubuntu.py`,
`main_banchetto_soft_sleep_AA_ubuntu.py`) drop the deep-sleep-only fields above
(`SECOND_RELAY_DELAY_SECONDS`, `FINAL_FAIL_*`, `SPECIAL_*`) since there's no power-on sequence
or no-green recovery gesture to worry about, and add:

| Parameter | Effect |
|---|---|
| `FINAL_SCREEN_SIMILARITY_THRESHOLD` | Fallback similarity threshold for the single "FINAL" screenshot, used if the average of `PRECHECK_FRAMES` didn't pass `CARPLAY_SIMILARITY_THRESHOLD` — lets a cycle still PASS if just the very last frame is clearly correct. |
| `PARTIAL_FAIL_TAP_X` / `PARTIAL_FAIL_TAP_Y` | Corrective tap sent when the final screen check fails (simpler recovery than the deep sleep gesture, since the IVI was never fully powered down). |
| `PARTIAL_FAIL_WAIT_SECONDS` | Pause after that corrective tap before ending the cycle. |

### 4.7 Output paths

| Parameter | Effect |
|---|---|
| `DESKTOP_DIR` | Base output folder for that script (e.g. `output/Test_Deep_Sleep`). Per-cycle session folders are created inside it by `model.new_session_dir()`. |
| `CSV_SUCCESS` / `CSV_FAILURE` | *(deep sleep only)* Detailed per-event CSVs with full log text and a screenshot link. |
| `CSV_OUTPUT_DEEP_SLEEP` | *(deep sleep only)* The summary 4-column CSV described in [§3](#3-running-the-tests). |
| `OUTPUT_CSV` | *(soft boot only)* The 3-column summary CSV for that script. |
| `BASE_DIR` | Project root, used to build all the paths above — normally left as `Path(__file__).resolve().parent` and not edited by hand. |

---

## 5. esoTrace integration

`esoTraceLogger_ubuntu.py` starts three `jtracecapture.jar` acquisitions (SYS, IVI, ConMod) at
the beginning of every test cycle and stops them at the end, hooked directly into the two
functions shared by all four main scripts:

- `start_traces()` is called at the very start of `wait_for_device()` (right after the session
  timer starts, before the first relay pulse) — so the trace covers the whole cycle including
  the KL15 power-cycle itself.
- `stop_traces()` is called inside `end_of_test_relay_sequence()`, right after the end-of-test
  relay pulse and before the cooldown wait — so it always runs exactly once per cycle, on every
  exit path (PASS, FAIL, PARTIALLY FAILED, or an unexpected exception), since that function sits
  on every one of those paths already.

**Where the files end up**: trace output is written into the **same per-cycle session folder**
as the screenshots and `tempo_connessione.txt` (`model.session_dir`), not into `jtrace/`. The
jar itself stays in `jtrace/` (it's invoked with an absolute path), but each `java` process runs
with its working directory set to that cycle's session folder, so the `-o` output files land
right next to everything else for that cycle. If `model.session_dir` isn't available yet for
some reason, the module falls back to `jtrace/` and logs a warning rather than failing silently.

**Fault tolerance**: if `java`, the jar, or `lxterminal` are missing, `start_traces()` logs a
clear message and skips tracing for that cycle — the test itself is never blocked by a missing
esoTrace prerequisite.

---

## 6. Design notes

Behaviours that might look like bugs at first glance but are intentional:

- **The connection timer starts from the second relay click, not the first (deep sleep only)**:
  the first pulse only acts as a power-on **enabler**; it's the **second** click that actually
  triggers the boot sequence if the IVI was in deep sleep. Timing from the first click would add
  the `SECOND_RELAY_DELAY_SECONDS` wait as dead time inside "Connection time". The timer
  (`model.second_relay_perf`) is set right after the second `pulse_relays()` call in
  `wait_for_device()`.
- **`tap()` does not pass `-d <SCREEN_DISPLAY_ID>`**: unlike `capture_png`/`capture_frame_bgr`
  (where it's required to avoid `screencap` ambiguity on multi-display units), `input tap` on
  this bench doesn't need the display ID — there's only one touch-capable display, and passing
  the argument breaks the command on this setup. If a future bench has multiple touch displays,
  this would need revisiting (`_input_display_args()` is already used by `motion_event()` and
  `swipe()`, just not by `tap()`).
- **`cooldown_restart()` actively re-checks the IVI is off, it doesn't just sleep**: after the
  configured wait, it tries an ADB connect; if the IVI answers, it assumes the relay's
  power-off pulse didn't actually take effect, pulses the relay again, and restarts the wait —
  rather than starting the next cycle against an IVI that's still on.

---

## 7. Quick troubleshooting

| Symptom | Likely cause | Check |
|---|---|---|
| Blank/corrupted screenshot | Wrong or missing `SCREEN_DISPLAY_ID` on a multi-display unit | §4.3 |
| Script stuck with no new logs after relay pulses | `TARGET_SERIAL` missing the port, or a stuck ADB command | §4.1 — confirm `TARGET_SERIAL` includes `:5555`; check `ADB_COMMAND_TIMEOUT_SECONDS` |
| `usbrelay` permission error | Missing/unloaded udev rule | §2.3 |
| `adb: no devices/emulators found` | Device unreachable, IP changed, network down | Manual `adb connect <ip>:5555`, check network to the bench |
| esoTrace windows never open, log says prerequisites missing | `java`/jar/`lxterminal` not installed, or no desktop/X session | §2.4 |
| esoTrace windows open but immediately show a Java error | `jtracecapture.jar` not in `jtrace/`, or a stale session folder path | Confirm `jtrace/jtracecapture.jar` exists; check the "trace saranno salvate in ..." log line |
| Tap/swipe has no visible effect | Wrong display targeted | Check `_input_display_args()` in `banchetto_utils_ubuntu.py` and the `tap()` note in §6 |
