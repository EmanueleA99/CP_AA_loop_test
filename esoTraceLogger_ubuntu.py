"""
esoTraceLogger_ubuntu.py

Avvia/ferma le acquisizioni jTraceCapture (SYS, IVI, ConMod) in sincronia
con ogni ciclo di test CP/AA (deep sleep e soft boot).

Difetti corretti rispetto allo script originale ricevuto:
- Il percorso della working directory era hardcoded su una macchina specifica
  ("/home/connectivity/python/CP_AA_loop_test-main/jtrace"). Ora è derivato in
  modo relativo al progetto (accanto a questo file), quindi funziona su
  qualunque checkout, incluso il Raspberry Pi.
- start_traces()/stop_traces() non gestivano eccezioni: se lxterminal, wmctrl
  o il jar non erano disponibili, l'intero loop di test si sarebbe fermato con
  un traceback non gestito. Ora ogni fallimento viene loggato e degradato
  senza interrompere il banco (il test prosegue comunque, solo senza trace).
- Il job "ConMod" era presente nel codice ma commentato: è stato riattivato
  perché il logging delle tre partizioni SYS, IVI e ConMod è quanto richiesto.
- Aggiunto un controllo dei prerequisiti (java, jar, lxterminal) prima di
  lanciare i processi, con messaggi chiari nel log invece di fallimenti muti.
"""

import shutil
import subprocess
import time
from pathlib import Path

try:
    import banchetto_view_ubuntu as view
    _HAS_VIEW = True
except Exception:
    _HAS_VIEW = False

try:
    import banchetto_model_ubuntu as model
    _HAS_MODEL = True
except Exception:
    _HAS_MODEL = False


_processes = []

# Cartella jtrace relativa al progetto (NON più hardcoded per singola macchina).
# Contiene SOLO il jar: deve contenere jtracecapture.jar. L'OUTPUT delle trace viene scritto nella cartella di sessione del
# loop corrente (model.session_dir), la stessa degli screenshot e dei log,
# così ogni ciclo di test ha tutto raggruppato in un'unica cartella.
JTRACE_DIR = Path(__file__).resolve().parent / "jtrace"
JAR_NAME = "jtracecapture.jar"
JAR_PATH = JTRACE_DIR / JAR_NAME

_JOBS = [
    ("SYS", 'java -jar "{jar}" -o traceSYS.esotrace_#yyyyMMddhhmm# 172.16.250.248 -p 21005'),
    ("IVI", 'java -jar "{jar}" -o traceIVI.esotrace_#yyyyMMddhhmm# 172.16.250.248 -p 21002'),
    ("ConMod", 'java -jar "{jar}" -o traceConMod.esotrace_#yyyyMMddhhmm# 127.0.0.1 -p 21002 -k 150'),
]


def _log(msg):
    print(msg)
    if _HAS_VIEW:
        try:
            view.safe_log_line(msg)
        except Exception:
            pass


def _preflight_ok():
    """Verifica i prerequisiti prima di avviare le acquisizioni; logga cosa manca."""
    ok = True

    if shutil.which("java") is None:
        _log("esoTrace: 'java' non trovato nel PATH (installa un JRE/JDK, es. 'sudo apt install default-jdk').")
        ok = False

    if not JAR_PATH.exists():
        _log(f"esoTrace: {JAR_NAME} non trovato in {JTRACE_DIR}. Copia il jar in questa cartella.")
        ok = False


    if shutil.which("lxterminal") is None:
        _log(
            "esoTrace: 'lxterminal' non trovato. Installa un emulatore di terminale grafico "
            "('sudo apt install lxterminal') e assicurati che il banco giri con una sessione "
            "desktop/X attiva (lxterminal richiede un DISPLAY)."
        )
        ok = False

    return ok

def _resolve_output_dir():
    """Determina dove devono atterrare i file di trace.
 
    Preferisce model.session_dir (la cartella creata per il loop corrente,
    la stessa di screenshot e log testuale). Se per qualche motivo non è
    ancora disponibile, ripiega su JTRACE_DIR e lo segnala nel log, invece
    di fallire silenziosamente.
    """
    if _HAS_MODEL:
        session_dir = getattr(model, "session_dir", None)
        if session_dir:
            session_dir = Path(session_dir)
            if session_dir.exists():
                return session_dir
            _log(f"esoTrace: model.session_dir ({session_dir}) non esiste ancora, uso {JTRACE_DIR}")
        else:
            _log("esoTrace: model.session_dir non impostato, uso jtrace/ come fallback")
    else:
        _log("esoTrace: banchetto_model_ubuntu non disponibile, uso jtrace/ come fallback")
 
    JTRACE_DIR.mkdir(parents=True, exist_ok=True)
    return JTRACE_DIR


def start_traces():
    """Avvia le acquisizioni jTraceCapture per SYS, IVI e ConMod, una per finestra lxterminal."""
    global _processes

    # Evita di aprire nuove finestre se quelle del ciclo precedente risultano ancora aperte.
    if any(p.poll() is None for p in _processes):
        _log("esoTrace: tracce già attive, avvio saltato")
        return
    _processes.clear()

    if not _preflight_ok():
        _log("esoTrace: avvio tracce saltato per prerequisiti mancanti (il test prosegue comunque, senza trace)")
        return

    output_dir = _resolve_output_dir()
    _log(f"esoTrace: le trace di questo ciclo verranno salvate in {output_dir}")

    for title, cmd_template in _JOBS:
        cmd = cmd_template.format(jar=JAR_PATH)
        try:
            p = subprocess.Popen(
                [
                    "lxterminal",
                    "--title", title,
                    "--command",
                    f"bash -c 'cd \"{output_dir}\" && {cmd}; exec bash'"
                ],
                cwd=str(output_dir)
            )
            _processes.append(p)
            _log(f"esoTrace: avviata acquisizione {title}")
        except Exception as e:
            _log(f"esoTrace: impossibile avviare acquisizione {title}: {e}")


def stop_traces():
    """Termina tutte le acquisizioni jTraceCapture attive e chiude le relative finestre."""
    try:
        subprocess.run(["pkill", "-f", JAR_NAME], check=False)
    except Exception as e:
        _log(f"esoTrace: errore durante pkill: {e}")

    time.sleep(1)

    for title, _cmd in _JOBS:
        try:
            subprocess.run(["wmctrl", "-c", title], check=False)
        except FileNotFoundError:
            # wmctrl non installato: i processi java sono comunque già terminati da pkill,
            # restano solo le finestre di shell aperte (innocuo, ma da installare per pulizia).
            pass
        except Exception as e:
            _log(f"esoTrace: errore durante chiusura finestra {title}: {e}")

    _processes.clear()
    _log("esoTrace: acquisizioni terminate")
