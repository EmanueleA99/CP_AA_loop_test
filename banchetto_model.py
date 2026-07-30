import time
from pathlib import Path
from types import SimpleNamespace

CONFIG = None
session_dir = None
log_file = None
session_start_perf = None
session_events = []
gray_detect_start_perf = None
relay_start_perf = None
second_relay_perf = None
second_relay_to_green_elapsed = None
 
import banchetto_view as view


def load_config(config):
    """Carica la configurazione specifica del test nel modulo."""
    global CONFIG
    if not isinstance(config, SimpleNamespace):
        raise TypeError("Il config deve essere un SimpleNamespace")
    CONFIG = config
    return CONFIG


def reset_session_timing():
    """Reimposta i timer e gli eventi di sessione per un nuovo ciclo di test."""
    global session_start_perf, session_events, gray_detect_start_perf, relay_start_perf, second_relay_perf, second_relay_to_green_elapsed
    session_start_perf = None
    session_events = []
    gray_detect_start_perf = None
    relay_start_perf = None
    second_relay_perf = None
    second_relay_to_green_elapsed = None


def start_session_timing():
    """Avvia il timer principale della sessione e resetta gli eventi."""
    global session_start_perf, session_events, gray_detect_start_perf, relay_start_perf, second_relay_perf, second_relay_to_green_elapsed
    session_start_perf = time.perf_counter()
    session_events = []
    gray_detect_start_perf = None
    relay_start_perf = None
    second_relay_perf = None
    second_relay_to_green_elapsed = None


def cooldown_restart(seconds=None):
    """Esegue un countdown prima di ripetere un nuovo test; scrive anche sul log di view."""
    if seconds is None:
        seconds = CONFIG.RESTART_DELAY_SECONDS
    msg = f"Inizio attesa di {seconds} secondi prima del nuovo test..."
    print(msg)
    try:
        view.safe_log_line(msg)
    except Exception:
        pass

    remaining = seconds
    while remaining > 0:
        should_print = False

        if remaining > 60:
            should_print = (remaining % 20 == 0)
        elif remaining > 5:
            should_print = (remaining % 10 == 0)
        else:
            should_print = True

        if should_print:
            msg = f"Ripartenza test tra {remaining} secondi..."
            print(msg)
            try:
                view.safe_log_line(msg)
            except Exception:
                pass

        time.sleep(1)
        remaining -= 1

    msg = "Attesa terminata."
    print(msg)
    try:
        view.safe_log_line(msg)
    except Exception:
        pass


def session_elapsed():
    """Restituisce i secondi trascorsi dall'inizio della sessione."""
    if session_start_perf is None:
        return 0.0
    return time.perf_counter() - session_start_perf


def format_elapsed(seconds):
    """Formatta un intervallo di tempo in secondi con due decimali."""
    return f"{seconds:.2f}s"


def mark_event(label):
    """Registra un evento sulla timeline della sessione e lo stampa."""
    elapsed = session_elapsed()
    session_events.append((elapsed, label))
    msg = f"[T+{format_elapsed(elapsed)}] {label}"
    print(msg)
    try:
        import banchetto_view as view
        view.safe_log_line(msg)
    except Exception:
        pass
    return msg


def build_session_timeline_text():
    """Costruisce una stringa riassuntiva degli eventi registrati nella sessione."""
    if not session_events:
        return "Timeline sessione: nessun evento registrato"

    lines = ["Timeline sessione:"]
    for elapsed, label in session_events:
        lines.append(f"- T+{format_elapsed(elapsed)} -> {label}")
    lines.append(f"Durata totale sessione: {format_elapsed(session_elapsed())}")
    return " | ".join(lines)


def new_session_dir():
    """Crea la cartella della sessione corrente sul Desktop."""
    path = CONFIG.DESKTOP_DIR / f"cattura schermate_{time.strftime('%Y%m%d_%H%M%S')}"
    path.mkdir(parents=True, exist_ok=True)
    return path
