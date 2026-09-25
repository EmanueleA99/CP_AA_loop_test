"""
esoTraceLogger.py

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
- ConMod attende un `adb forward` prima di avviare jTraceCapture: la partizione
  ConMod non è raggiungibile via IP diretto come SYS/IVI, serve prima inoltrare
  la porta del device sulla stessa porta in locale. Lo script genera uno
  script bash dedicato (come nella versione Plugbot) che fa il loop di
  `adb forward` finché non riesce, poi lancia il jar — invece di lanciare
  subito jTraceCapture assumendo che il forward sia già attivo.
"""

import shutil
import subprocess
import time
from pathlib import Path

try:
    import banchetto_view as view
    _HAS_VIEW = True
except Exception:
    _HAS_VIEW = False

try:
    import banchetto_model as model
    _HAS_MODEL = True
except Exception:
    _HAS_MODEL = False


_processes = []

# Cartella jtrace relativa al progetto (NON hardcoded per singola macchina).
# Contiene SOLO il jar: deve contenere jtracecapture.jar. L'OUTPUT delle trace viene scritto nella cartella di sessione del
# loop corrente (model.session_dir), la stessa degli screenshot e dei log,
# così ogni ciclo di test ha tutto raggruppato in un'unica cartella.
JTRACE_DIR = Path(__file__).resolve().parent / "jtrace"
JAR_NAME = "jtracecapture.jar"
JAR_PATH = JTRACE_DIR / JAR_NAME

# SYS e IVI si collegano direttamente all'IP dell'infotainment: nessun forward necessario.
_JOBS = [
    ("SYS", 'java -jar "{jar}" -o traceSYS.esotrace_#yyyyMMddhhmm# 172.16.250.248 -p 21005'),
    ("IVI", 'java -jar "{jar}" -o traceIVI.esotrace_#yyyyMMddhhmm# 172.16.250.248 -p 21002'),
]

# ConMod non è raggiungibile via IP diretto: serve un `adb forward` sulla stessa porta
# prima di poter avviare jTraceCapture puntato su 127.0.0.1.
CONMOD_PORT = "21002"
CONMOD_CMD_TEMPLATE = 'java -jar "{jar}" -o traceConMod.esotrace_#yyyyMMddhhmm# 127.0.0.1 -p {port} -k 150'


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
        _log("esoTrace: banchetto_model non disponibile, uso jtrace/ come fallback")

    JTRACE_DIR.mkdir(parents=True, exist_ok=True)
    return JTRACE_DIR


def _adb_binary():
    """Ritorna l'eseguibile adb configurato nel CONFIG del test corrente, o 'adb' di default."""
    if _HAS_MODEL:
        config = getattr(model, "CONFIG", None)
        if config is not None:
            return getattr(config, "ADB", "adb")
    return "adb"


def _target_serial():
    """Ritorna il serial ADB del device sotto test (es. '172.16.250.248:5555'), se disponibile."""
    if _HAS_MODEL:
        config = getattr(model, "CONFIG", None)
        if config is not None:
            return getattr(config, "TARGET_SERIAL", None)
    return None


def _create_conmod_script(jar_path, output_dir):
    """Crea lo script bash che attende l'adb forward e poi avvia jTraceCapture per ConMod.

    ConMod, a differenza di SYS/IVI, non è raggiungibile via IP diretto: serve prima
    inoltrare (via `adb forward`) la porta del device sulla stessa porta in locale, e
    jTraceCapture va lanciato solo dopo che il forward è riuscito — altrimenti si connette
    a una porta locale non ancora aperta e fallisce silenziosamente.
    """
    serial = "127.0.0.1"
    if not serial:
        _log("esoTrace: TARGET_SERIAL non disponibile in CONFIG, impossibile avviare ConMod (serve per l'adb forward)")
        return None

    adb = _adb_binary()
    script_path = output_dir / "start_conmod.sh"
    content = (
        "#!/bin/bash\n"
        f"cd \"{output_dir}\"\n"
        f"echo \"[ConMod] In attesa del device {serial}...\"\n"
        f"until {adb} -s {serial} forward tcp:{CONMOD_PORT} tcp:{CONMOD_PORT} 2>/dev/null; do\n"
        "    sleep 1\n"
        "done\n"
        f"echo \"[ConMod] Port forward tcp:{CONMOD_PORT} attivo! Avvio jTraceCapture...\"\n"
        f"{CONMOD_CMD_TEMPLATE.format(jar=jar_path, port=CONMOD_PORT)}\n"
    )
    script_path.write_text(content)
    script_path.chmod(0o755)
    return script_path


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

    # ConMod: script dedicato che attende l'adb forward prima di avviare jTraceCapture
    # (vedi _create_conmod_script). Non fa parte di _JOBS perché il comando da lanciare
    # in lxterminal è lo script stesso, non una riga java diretta.
    conmod_script = _create_conmod_script(JAR_PATH, output_dir)
    if conmod_script is not None:
        try:
            p_conmod = subprocess.Popen(
                [
                    "lxterminal",
                    "--title", "ConMod",
                    "--command", f"bash \"{conmod_script}\""
                ],
                cwd=str(output_dir)
            )
            _processes.append(p_conmod)
            _log("esoTrace: avviata finestra ConMod con attesa adb forward")
        except Exception as e:
            _log(f"esoTrace: impossibile avviare acquisizione ConMod: {e}")


def stop_traces():
    """Termina tutte le acquisizioni jTraceCapture attive e chiude le relative finestre."""
    # Ferma anche l'eventuale loop di attesa dell'adb forward per ConMod, se il forward
    # non è mai riuscito e lo script è ancora fermo nel while.
    try:
        subprocess.run(["pkill", "-f", "start_conmod.sh"], check=False)
    except Exception:
        pass

    try:
        subprocess.run(["pkill", "-f", JAR_NAME], check=False)
    except Exception as e:
        _log(f"esoTrace: errore durante pkill: {e}")

    time.sleep(1)

    for title in ["SYS", "IVI", "ConMod"]:
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
