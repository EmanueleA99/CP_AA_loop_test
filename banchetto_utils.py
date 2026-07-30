import subprocess
import time
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

import banchetto_model as model
import banchetto_view as view

import struct

def ensure_bgr_array(image_input):
    """Accetta sia un array NumPy (BGR) che byte PNG, restituendo sempre un array BGR."""
    if isinstance(image_input, np.ndarray):
        return image_input
    return png_to_bgr_array(image_input)

def save_png(png_bytes, filename):
    """Salva uno screenshot PNG nella cartella sessione."""
    path = model.session_dir / filename
    with open(path, "wb") as f:
        f.write(png_bytes)
    return path

def capture_frame_bgr(serial):
    """Cattura lo schermo via ADB e restituisce una matrice OpenCV BGR in memoria (ultra-veloce)."""
    timeout = getattr(model.CONFIG, "ADB_COMMAND_TIMEOUT_SECONDS", 10)
    cmd = [model.CONFIG.ADB, "-s", serial, "exec-out", "screencap"]
    display_id = getattr(model.CONFIG, "SCREEN_DISPLAY_ID", None)
    if display_id is not None:
        cmd += ["-d", str(display_id)]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        raw = result.stdout
        if len(raw) < 12:
            return None

        width, height, _ = struct.unpack("<III", raw[:12])
        expected_len = width * height * 4
        header_size = len(raw) - expected_len
        
        pixel_data = raw[header_size : header_size + expected_len]
        img_rgba = np.frombuffer(pixel_data, dtype=np.uint8).reshape((height, width, 4))
        
        # Converte da RGBA ad BGR per l'analisi immediata
        return cv2.cvtColor(img_rgba, cv2.COLOR_RGBA2BGR)
    except Exception:
        return None

def bgr_to_png(img_bgr):
    """Converte una matrice BGR in byte PNG solo quando occorre salvarla."""
    _, encoded_png = cv2.imencode('.png', img_bgr, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    return encoded_png.tobytes()

def capture_png(serial):
    """Acquisisce uno screenshot via ADB in modalità RAW (veloce) e lo converte in PNG sul PC."""
    timeout = getattr(model.CONFIG, "ADB_COMMAND_TIMEOUT_SECONDS", 10)
    
    # Rimuoviamo '-p' per evitare che lo smartphone perda tempo a comprimere il PNG
    cmd = [model.CONFIG.ADB, "-s", serial, "exec-out", "screencap"]
    display_id = getattr(model.CONFIG, "SCREEN_DISPLAY_ID", None)
    if display_id is not None:
        cmd += ["-d", str(display_id)]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        msg = f"Timeout ({timeout}s) durante screencap via ADB: device non risponde"
        print(msg)
        view.safe_log_line(msg)
        return None

    raw = result.stdout
    if len(raw) < 12:
        msg = f"screencap ADB ha restituito dati insufficienti ({len(raw)} bytes)"
        print(msg)
        view.safe_log_line(msg)
        return None

    try:
        # I primi 12 byte dell'header RAW contengono: Width (4b), Height (4b), PixelFormat (4b)
        width, height, _ = struct.unpack("<III", raw[:12])
        expected_data_len = width * height * 4  # 4 byte per pixel (RGBA)

        # Calcolo dinamico dell'header (12 byte su Android vecchi, 16 byte su Android 7+)
        header_size = len(raw) - expected_data_len
        if header_size < 12:
            raise ValueError(f"Payload incompleto: ricevuti {len(raw)} byte, attesi almeno {expected_data_len + 12}")

        # Estragga il buffer dei pixel e converte in matrice NumPy RGBA
        pixel_data = raw[header_size : header_size + expected_data_len]
        img_rgba = np.frombuffer(pixel_data, dtype=np.uint8).reshape((height, width, 4))

        # Converti da RGBA (Android) a BGR (OpenCV)
        img_bgr = cv2.cvtColor(img_rgba, cv2.COLOR_RGBA2BGR)

        # Compressione PNG ultra-rapida sul PC (Livello 1)
        success, encoded_png = cv2.imencode('.png', img_bgr, [cv2.IMWRITE_PNG_COMPRESSION, 1])
        if success:
            return encoded_png.tobytes()

    except Exception as e:
        msg = f"Errore nella decodifica dello screenshot RAW: {e}"
        print(msg)
        view.safe_log_line(msg)
        return None

    return None

def png_to_gray_array(png_bytes):
    """Converte un PNG in memoria in un array in scala di grigi."""
    return np.array(Image.open(BytesIO(png_bytes)).convert("L"))

def png_to_bgr_array(png_bytes):
    """Converte un PNG in memoria in un array BGR per OpenCV."""
    rgb = np.array(Image.open(BytesIO(png_bytes)).convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

def similarity_score(reference_path, png_bytes):
    """Calcola la similarità tra screenshot e immagine di riferimento."""
    ref = cv2.imread(str(reference_path), cv2.IMREAD_GRAYSCALE)
    if ref is None:
        raise FileNotFoundError(f"Immagine di riferimento non trovata: {reference_path}")

    frame = png_to_gray_array(png_bytes)
    frame = cv2.resize(frame, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_AREA)
    result = cv2.matchTemplate(frame, ref, cv2.TM_CCOEFF_NORMED)
    return float(result[0][0])

def hex_to_bgr(hex_color):
    """Converte un valore HEX in un array BGR per calcoli OpenCV."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) != 6:
        raise ValueError(f"Colore HEX non valido: {hex_color}")

    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return np.array([b, g, r], dtype=np.float32)

def extract_roi(frame_bgr, roi):
    """Estrae una regione di interesse da un frame BGR."""
    x1, y1, x2, y2 = roi
    h, w = frame_bgr.shape[:2]

    if x1 < 0 or y1 < 0 or x2 > w or y2 > h or x1 >= x2 or y1 >= y2:
        raise ValueError(f"ROI non valida. Frame={w}x{h}, ROI={roi}")

    cut = frame_bgr[y1:y2, x1:x2]
    if cut.size == 0:
        raise ValueError(f"ROI vuota: {roi}")
    return cut

def mean_color_distance(image_input, roi, target_hex):
    """Calcola la distanza del colore medio della ROI dal colore target.
    
    Accetta sia byte PNG sia matici NumPy BGR.
    """
    # Se è già NumPy usa la RAM direttamente, altrimenti converte i byte PNG
    frame_bgr = ensure_bgr_array(image_input)
    
    roi_frame = extract_roi(frame_bgr, roi)
    mean_bgr = roi_frame.mean(axis=(0, 1)).astype(np.float32)
    target_bgr = hex_to_bgr(target_hex)
    distance = float(np.linalg.norm(mean_bgr - target_bgr))
    return distance, mean_bgr


def get_pixel_bgr_and_distance(png_bytes, x, y, target_hex):
    """Restituisce colore e distanza dal target per un singolo pixel."""
    frame_bgr = png_to_bgr_array(png_bytes)
    h, w = frame_bgr.shape[:2]

    if x < 0 or y < 0 or x >= w or y >= h:
        raise ValueError(f"Pixel fuori immagine. Frame={w}x{h}, pixel=({x},{y})")

    pixel_bgr = frame_bgr[y, x].astype(np.float32)
    target_bgr = hex_to_bgr(target_hex)
    distance = float(np.linalg.norm(pixel_bgr - target_bgr))
    return pixel_bgr, distance


def roi_green_metrics(image_input, roi):
    """Estrae le metriche cromatiche utili per decidere se la ROI è verde.
    
    Accetta sia byte PNG sia array NumPy BGR (in RAM).
    """
    # Convertiamo solo se necessario (se è già un array NumPy, passa direttamente)
    frame_bgr = ensure_bgr_array(image_input)
    roi_frame = extract_roi(frame_bgr, roi).astype(np.float32)

    b = roi_frame[:, :, 0]
    g = roi_frame[:, :, 1]
    r = roi_frame[:, :, 2]

    mean_b = float(np.mean(b))
    mean_g = float(np.mean(g))
    mean_r = float(np.mean(r))
    dominance = mean_g - max(mean_r, mean_b)
    green_mask = (g >= r + 20) & (g >= b + 20) & (g >= 110)
    green_ratio = float(np.mean(green_mask))

    # Passiamo image_input a mean_color_distance senza rieseguire decodifiche
    distance_to_green, _ = mean_color_distance(image_input, roi, model.CONFIG.LEFT_GREEN_TARGET_HEX)

    return {
        "mean_b": mean_b,
        "mean_g": mean_g,
        "mean_r": mean_r,
        "dominance": dominance,
        "green_ratio": green_ratio,
        "distance_to_green": distance_to_green,
    }


def is_roi_green(image_input, roi):
    """Determina se una ROI è considerabile verde sulla base delle metriche calcolate.
    
    Accetta sia byte PNG sia matrici NumPy BGR.
    """
    metrics = roi_green_metrics(image_input, roi)
    by_distance = metrics["distance_to_green"] <= model.CONFIG.LEFT_GREEN_DISTANCE_THRESHOLD
    by_dominance = (
        metrics["dominance"] >= model.CONFIG.GREEN_DOMINANCE_MIN
        and metrics["green_ratio"] >= model.CONFIG.GREEN_PIXELS_MIN_RATIO
    )
    return (by_distance or by_dominance), metrics


def _input_display_args():
    """Restituisce gli argomenti -d <id> per il comando 'input', se un display è configurato."""
    display_id = getattr(model.CONFIG, "SCREEN_DISPLAY_ID", None)
    return ["-d", str(display_id)] if display_id is not None else []


def tap(serial, x, y):
    """Esegue un tap via ADB sul device alle coordinate specificate."""
    cmd = [model.CONFIG.ADB, "-s", serial, "shell", "input"] + ["tap", str(x), str(y)]
    timeout = getattr(model.CONFIG, "ADB_COMMAND_TIMEOUT_SECONDS", 10)
    print(f"Comando tap ADB: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        msg = f"Timeout ({timeout}s) durante tap ADB su ({x}, {y}): device non risponde"
        print(msg)
        view.safe_log_line(msg)


def motion_event(serial, action, x, y):
    """Esegue un evento touchpoint ADB con azione e coordinate specificate."""
    cmd = (
        [model.CONFIG.ADB, "-s", serial, "shell", "input"] + _input_display_args() +
        ["touchscreen", "motionevent", action, str(x), str(y)]
    )
    timeout = getattr(model.CONFIG, "ADB_COMMAND_TIMEOUT_SECONDS", 10)
    print(f"Comando motionevent ADB: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, capture_output=True, check=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        msg = f"Timeout ({timeout}s) durante motionevent ADB {action} su ({x}, {y}): device non risponde"
        print(msg)
        view.safe_log_line(msg)
        raise


def swipe(serial, x1, y1, x2, y2, duration_ms):
    """Esegue uno swipe sul device tramite ADB."""
    cmd = (
        [model.CONFIG.ADB, "-s", serial, "shell", "input"] + _input_display_args() +
        ["swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)]
    )
    base_timeout = getattr(model.CONFIG, "ADB_COMMAND_TIMEOUT_SECONDS", 10)
    timeout = max(base_timeout, (duration_ms / 1000.0) + 5)
    print(f"Comando swipe ADB: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        msg = f"Timeout ({timeout:.1f}s) durante swipe ADB da ({x1},{y1}) a ({x2},{y2}): device non risponde"
        print(msg)
        view.safe_log_line(msg)


def special_hold_and_swipe(serial):
    """Esegue la sequenza speciale di hold + swipe usata dalla recovery no-green."""
    hold_seconds = model.CONFIG.SPECIAL_HOLD_BEFORE_SWIPE_MS / 1000.0

    model.mark_event(
        f"Eseguo gesto speciale: DOWN su "
        f"({model.CONFIG.SPECIAL_HOLD_START_X}, {model.CONFIG.SPECIAL_HOLD_START_Y}), "
        f"attesa {model.CONFIG.SPECIAL_HOLD_BEFORE_SWIPE_MS}ms, poi drag fino a "
        f"({model.CONFIG.SPECIAL_SWIPE_END_X}, {model.CONFIG.SPECIAL_SWIPE_END_Y})"
    )

    motion_event(serial, "DOWN", model.CONFIG.SPECIAL_HOLD_START_X, model.CONFIG.SPECIAL_HOLD_START_Y)
    time.sleep(hold_seconds)
    motion_event(serial, "MOVE", model.CONFIG.SPECIAL_SWIPE_END_X, model.CONFIG.SPECIAL_SWIPE_END_Y)
    motion_event(serial, "UP", model.CONFIG.SPECIAL_SWIPE_END_X, model.CONFIG.SPECIAL_SWIPE_END_Y)


def maybe_execute_no_green_final_recovery(serial, final_fail_path):
    """Esegue la recovery speciale nel caso di no-green finale, se applicabile."""
    if not final_fail_path or not Path(final_fail_path).exists():
        model.mark_event("Recovery no-green saltata: screenshot finale non disponibile")
        return False

    try:
        with open(final_fail_path, "rb") as f:
            png_bytes = f.read()

        pixel_bgr, pixel_distance = get_pixel_bgr_and_distance(
            png_bytes,
            model.CONFIG.FINAL_FAIL_PIXEL_X,
            model.CONFIG.FINAL_FAIL_PIXEL_Y,
            model.CONFIG.FINAL_FAIL_PIXEL_TARGET_HEX
        )

        msg = (
            f"Controllo pixel finale ({model.CONFIG.FINAL_FAIL_PIXEL_X}, {model.CONFIG.FINAL_FAIL_PIXEL_Y}): "
            f"bgr=({pixel_bgr[0]:.1f},{pixel_bgr[1]:.1f},{pixel_bgr[2]:.1f}), "
            f"distanza da {model.CONFIG.FINAL_FAIL_PIXEL_TARGET_HEX} = {pixel_distance:.2f}"
        )
        print(msg)
        view.safe_log_line(msg)

        if pixel_distance > model.CONFIG.FINAL_FAIL_PIXEL_DISTANCE_THRESHOLD:
            model.mark_event(
                f"Pixel finale non abbastanza simile a {model.CONFIG.FINAL_FAIL_PIXEL_TARGET_HEX}: nessuna recovery speciale"
            )
            return False

        model.mark_event(
            f"Pixel finale compatibile con {model.CONFIG.FINAL_FAIL_PIXEL_TARGET_HEX}: avvio recovery speciale"
        )

        time.sleep(0.5)
        model.mark_event(
            f"Tocco di recovery dopo 0.500s su ({model.CONFIG.FINAL_FAIL_RECOVERY_TAP_X}, {model.CONFIG.FINAL_FAIL_RECOVERY_TAP_Y})"
        )
        tap(serial, model.CONFIG.FINAL_FAIL_RECOVERY_TAP_X, model.CONFIG.FINAL_FAIL_RECOVERY_TAP_Y)

        time.sleep(1.0)
        special_hold_and_swipe(serial)

        time.sleep(2.0)
        model.mark_event("Recovery speciale completata; procedura di fallimento terminata")
        return True

    except Exception as e:
        err = f"Errore durante la recovery speciale no-green: {e}"
        print(err)
        view.safe_log_line(err)
        return False
