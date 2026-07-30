import csv
import time
from pathlib import Path

import banchetto_model as model


def safe_log_line(text):
    """Scrive una riga nel file di log della sessione senza far fallire lo script."""
    try:
        with open(model.log_file, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception as e:
        print(f"Impossibile scrivere il log testuale: {e}")


def excel_hyperlink(path_obj):
    """Genera una formula HYPERLINK Excel per una path di file."""
    if not path_obj:
        return ""
    p = Path(path_obj)
    display = p.name
    target = str(p).replace('"', '""')
    return f'=HYPERLINK("{target}","{display}")'


def ensure_csv(csv_path):
    """Crea un CSV di log con intestazione se non esiste."""
    try:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        if not csv_path.exists():
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["timestamp", "status", "log", "final_screenshot", "final_screenshot_link"]
                )
                writer.writeheader()
        return True
    except Exception as e:
        print(f"Impossibile inizializzare il CSV {csv_path}: {e}")
        safe_log_line(f"Errore inizializzazione CSV {csv_path}: {e}")
        return False


def append_csv(csv_path, status, log, screenshot_path):
    """Aggiunge una riga al CSV di log profondo con screenshot finale opzionale."""
    try:
        if not ensure_csv(csv_path):
            return False

        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["timestamp", "status", "log", "final_screenshot", "final_screenshot_link"]
            )
            writer.writerow({
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": status,
                "log": log,
                "final_screenshot": str(screenshot_path) if screenshot_path else "",
                "final_screenshot_link": excel_hyperlink(screenshot_path)
            })
        return True
    except PermissionError as e:
        print(f"CSV occupato o non scrivibile: {csv_path} -> {e}")
        safe_log_line(f"Errore CSV occupato {csv_path}: {e}")
        return False
    except Exception as e:
        print(f"Errore scrittura CSV {csv_path}: {e}")
        safe_log_line(f"Errore scrittura CSV {csv_path}: {e}")
        return False


def ensure_output_csv(csv_path):
    """Crea il CSV di output standard se non esiste."""
    try:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        if not csv_path.exists():
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "status", "reason"])
        return True
    except Exception as e:
        print(f"Impossibile inizializzare il CSV {csv_path}: {e}")
        safe_log_line(f"Errore inizializzazione CSV {csv_path}: {e}")
        return False


def append_output_csv(status, reason):
    """Aggiunge una riga al CSV di output standard."""
    try:
        if not ensure_output_csv(model.CONFIG.OUTPUT_CSV):
            return False
        with open(model.CONFIG.OUTPUT_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), status, reason])
        return True
    except Exception as e:
        print(f"Errore scrittura CSV {model.CONFIG.OUTPUT_CSV}: {e}")
        safe_log_line(f"Errore scrittura CSV {model.CONFIG.OUTPUT_CSV}: {e}")
        return False


def ensure_deep_sleep_csv():
    """Inizializza il CSV di output deep sleep se non esiste."""
    try:
        model.CONFIG.CSV_OUTPUT_DEEP_SLEEP.parent.mkdir(parents=True, exist_ok=True)
        if not model.CONFIG.CSV_OUTPUT_DEEP_SLEEP.exists():
            with open(model.CONFIG.CSV_OUTPUT_DEEP_SLEEP, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "status", "detail"])
        return True
    except Exception as e:
        print(f"Impossibile inizializzare il CSV output_deep_sleep: {e}")
        safe_log_line(f"Errore inizializzazione output_deep_sleep: {e}")
        return False


def append_deep_sleep_csv(status, detail):
    """Aggiunge una riga al CSV di output deep sleep."""
    try:
        if not ensure_deep_sleep_csv():
            return False

        with open(model.CONFIG.CSV_OUTPUT_DEEP_SLEEP, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                time.strftime("%Y-%m-%d %H:%M:%S"),
                status,
                detail
            ])
        return True
    except Exception as e:
        print(f"Errore scrittura output_deep_sleep.csv: {e}")
        safe_log_line(f"Errore scrittura output_deep_sleep.csv: {e}")
        return False


def log_output_deep_sleep_passed():
    """Registra il risultato PASS nel CSV deep sleep."""
    if model.second_relay_to_green_elapsed is None:
        detail = "Test PASSED: icona verde rilevata, ma tempo secondo click->verde non disponibile"
    else:
        detail = (
            f"Test PASSED: icona verde rilevata in {model.format_elapsed(model.second_relay_to_green_elapsed)} "
            f"dal secondo click relay"
        )
    append_deep_sleep_csv("PASSED", detail)
    safe_log_line(f"output_deep_sleep -> PASSED | {detail}")


def log_output_deep_sleep_partially_failed(avg_score):
    """Registra un risultato PARTIALLY FAILED nel CSV deep sleep."""
    if model.second_relay_to_green_elapsed is None:
        timing = "tempo secondo click->verde non disponibile"
    else:
        timing = f"icona verde rilevata in {model.format_elapsed(model.second_relay_to_green_elapsed)} dal secondo click relay"

    detail = (
        f"Test PARTIALLY FAILED: {timing}, ma schermata CarPlay non rilevata. "
        f"Similarita media finale: {avg_score:.3f}"
    )
    append_deep_sleep_csv("PARTIALLY FAILED", detail)
    safe_log_line(f"output_deep_sleep -> PARTIALLY FAILED | {detail}")


def log_output_deep_sleep_failed(reason):
    """Registra un risultato FAIL nel CSV deep sleep."""
    append_deep_sleep_csv("FAILED", reason)
    safe_log_line(f"output_deep_sleep -> FAILED | {reason}")
