import os
import socket
import subprocess
import time

import banchetto_model_ubuntu as model
import banchetto_view_ubuntu as view
import banchetto_utils_ubuntu as utils
import esoTraceLogger_ubuntu as esotrace


def fatal_stop(reason, screenshot_path=None):
    """Termina lo script dopo aver registrato il motivo di stop."""
    print(reason)
    view.safe_log_line(reason)
    view.append_output_csv("FAILED", reason)
    adb_kill_server()
    raise SystemExit(1)


def pulse_relays():
    """Invia un impulso ai relè tramite usbrelay per simulare la pressione del pulsante."""
    ch1 = model.CONFIG.RELAY_CHANNEL_1
    ch2 = model.CONFIG.RELAY_CHANNEL_2
    hold_seconds = getattr(model.CONFIG, "RELAY_PULSE_HOLD_SECONDS", 0.5)

    try:
        subprocess.run(
            f"usbrelay {ch1}=1 {ch2}=1",
            shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(hold_seconds)
        subprocess.run(
            f"usbrelay {ch1}=0 {ch2}=0",
            shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        fatal_stop("Impossibile comunicare con usbrelay. Controlla la connessione USB o i permessi udev. Arresto definitivo dello script.")


# ---------------------------------------------------------------------------
# Helper ADB con timeout duri
# ---------------------------------------------------------------------------

def _run_adb(args, timeout, capture=True):
    """Esegue un comando adb con timeout DURO. Non solleva mai su timeout.

    Ritorna (returncode, stdout, stderr, timed_out).
    capture=False usa DEVNULL: indispensabile per start-server/kill-server, perche' il
    demone adb puo' ereditare le pipe e far restare appeso subprocess.run().
    """
    cmd = [model.CONFIG.ADB] + list(args)
    try:
        if capture:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
            return r.returncode, r.stdout.strip(), r.stderr.strip(), False
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=timeout)
        return r.returncode, "", "", False
    except subprocess.TimeoutExpired:
        msg = f"[ADB] TIMEOUT duro ({timeout}s) su: {' '.join(cmd)}"
        print(msg)
        view.safe_log_line(msg)
        return None, "", "", True


def adb_kill_server():
    """kill-server con timeout (non blocca mai il loop)."""
    _run_adb(["kill-server"], timeout=5, capture=False)


def adb_start_server():
    """start-server esplicito con timeout; True se il server risponde."""
    _run_adb(["start-server"], timeout=10, capture=False)
    rc, _, _, timed_out = _run_adb(["devices"], timeout=5)
    return (not timed_out) and rc == 0


def reset_adb_server():
    """Riparte da un server adb pulito (nessuna transport residua).

    Va chiamato mentre il banco e' SPENTO, cioe' PRIMA del click relay e fuori dalla
    finestra cronometrata: cosi' il costo di avvio del server non entra nella misura.
    """
    view.safe_log_line("[ADB] reset server adb pre-test")
    adb_kill_server()
    time.sleep(0.3)
    ok = adb_start_server()
    if not ok:
        # secondo tentativo, poi si prosegue comunque (il loop di connessione ha i suoi timeout)
        adb_kill_server()
        time.sleep(1.0)
        ok = adb_start_server()
    msg = f"[ADB] server adb pronto: {ok}"
    print(msg)
    view.safe_log_line(msg)
    return ok


# ---------------------------------------------------------------------------
# Probe TCP: rileva la porta adbd SENZA passare dal server adb
# ---------------------------------------------------------------------------

def device_port_open(timeout=0.15):
    """True se la porta ADB TCP del banco accetta connessioni.

    Non tocca il server adb: quindi non puo' creare transport zombie, e si puo'
    sparare a 20-50 Hz senza accumulare richieste pendenti.
    """
    try:
        with socket.create_connection(
            (model.CONFIG.TARGET_IP, int(model.CONFIG.TARGET_PORT)), timeout=timeout
        ):
            return True
    except OSError:
        return False


def wait_for_port_down(timeout_seconds):
    """Conferma via TCP che il banco sparisce dopo il click relay (power-cycle avvenuto)."""
    if not device_port_open(0.1):
        model.mark_event(
            "Banco non raggiungibile via TCP al momento del click relay (atteso in deep sleep)"
        )
        return "not_present_before"

    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        if not device_port_open(0.2):
            model.mark_event("Banco non piu' raggiungibile dopo il click relay: power-cycle avvenuto")
            return "disconnected"
        time.sleep(0.05)

    model.mark_event(
        f"ATTENZIONE: la porta ADB e' rimasta raggiungibile per {timeout_seconds}s dal click relay. "
        f"Sospetto che il relay non abbia effettivamente fatto il power-cycle KL15 in questo ciclo."
    )
    return "never_disconnected"


def adb_connect_and_verify(attempt):
    """UN solo 'adb connect' (mai in parallelo ad altri) + attesa dello stato 'device'.

    Ritorna (ok, dettaglio). Tutti i timeout sono duri e larghi: qui il timeout NON serve
    a "sparare e dimenticare" ma solo come rete di sicurezza. Il client non viene mai
    ucciso a meta' handshake, quindi non restano connect pendenti sul server adb.
    """
    serial = model.CONFIG.TARGET_SERIAL
    connect_timeout = getattr(model.CONFIG, "ADB_CONNECT_CMD_TIMEOUT_SECONDS", 4)
    verify_timeout = getattr(model.CONFIG, "ADB_VERIFY_TIMEOUT_SECONDS", 3)

    rc, out, err, timed_out = _run_adb(["connect", serial], connect_timeout)
    text = (out + " " + err).strip()
    if text:
        print(f"[ADB connect #{attempt}] {text}")
        view.safe_log_line(f"[ADB connect #{attempt}] {text}")
    if timed_out:
        return False, "connect_timeout"
    if "connected to" not in text.lower():   # copre anche 'already connected to'
        return False, text or f"rc={rc}"

    # Il connect puo' tornare OK con transport ancora 'offline' (handshake CNXN/AUTH in corso).
    _, _, _, timed_out = _run_adb(["-s", serial, "wait-for-device"], verify_timeout)
    if timed_out:
        return False, "wait_for_device_timeout (transport rimasta offline/connecting)"

    rc, out, err, timed_out = _run_adb(["-s", serial, "get-state"], 2)
    if not timed_out and out == "device":
        return True, "device"
    return False, f"stato={out or err or 'n/d'}"


def wait_for_device():
    """Avvia la sessione e si connette via ADB il piu' presto possibile dopo il boot del banco."""
    # Server adb pulito PRIMA del click relay (banco spento): niente costo di avvio server
    # e niente transport residue dal ciclo precedente dentro la finestra cronometrata.
    reset_adb_server()

    model.start_session_timing()
    esotrace.start_traces()
    model.mark_event("Primo click relay di avvio test")
    pulse_relays()

    if getattr(model.CONFIG, "SECOND_RELAY_DELAY_SECONDS", None) is not None:
        model.mark_event(f"Attesa passiva di {model.CONFIG.SECOND_RELAY_DELAY_SECONDS} secondi dopo il primo click relay")
        time.sleep(model.CONFIG.SECOND_RELAY_DELAY_SECONDS)
        model.mark_event("Secondo click relay prima della connessione ADB")
        pulse_relays()
        model.second_relay_perf = time.perf_counter()
        model.mark_event("Cronometro principale avviato: misuro dal click relay al verde")

        disconnect_timeout = getattr(model.CONFIG, "DISCONNECT_CHECK_TIMEOUT_SECONDS", 5)
        wait_for_port_down(disconnect_timeout)

    probe_timeout = getattr(model.CONFIG, "PORT_PROBE_TIMEOUT_SECONDS", 0.15)
    probe_interval = getattr(model.CONFIG, "PORT_PROBE_INTERVAL_SECONDS", 0.02)

    model.mark_event(
        f"Avvio spam di probe TCP su {model.CONFIG.TARGET_IP}:{model.CONFIG.TARGET_PORT} "
        f"(timeout {probe_timeout:.2f}s, intervallo {probe_interval:.2f}s) per massimo "
        f"{model.CONFIG.ADB_CONNECT_TIMEOUT_SECONDS}s; 'adb connect' solo quando la porta risponde"
    )

    deadline = time.perf_counter() + model.CONFIG.ADB_CONNECT_TIMEOUT_SECONDS
    attempt = 0
    port_reported = False

    while time.perf_counter() < deadline:
        if not device_port_open(probe_timeout):
            port_reported = False
            if probe_interval > 0:
                time.sleep(probe_interval)
            continue

        if not port_reported:
            port_reported = True
            model.mark_event("Porta ADB raggiungibile via TCP: lancio adb connect")

        attempt += 1
        ok, detail = adb_connect_and_verify(attempt)
        if ok:
            model.mark_event(f"Connessione ADB riuscita al tentativo {attempt}")
            return model.CONFIG.TARGET_SERIAL

        model.mark_event(f"Tentativo adb connect #{attempt} non riuscito ({detail}): disconnect e nuovo tentativo")
        _run_adb(["disconnect", model.CONFIG.TARGET_SERIAL], 2)
        time.sleep(0.1)

    model.mark_event(f"Timeout connessione ADB dopo {model.CONFIG.ADB_CONNECT_TIMEOUT_SECONDS} secondi")
    return None


def end_of_test_relay_sequence():
    """Chiude il test azionando il relay e attivando il cooldown."""
    model.mark_event("Click relay di fine test")
    pulse_relays()
    esotrace.stop_traces()
    model.cooldown_restart(model.CONFIG.RESTART_DELAY_SECONDS)


def wait_for_gray_to_green(serial):
    """Monitora la ROI finché non passa da grigio a verde ad alte prestazioni (10+ FPS)."""
    model.mark_event("Avvio catture schermata e monitoraggio ROI sinistra CarPlay")
    if getattr(model.CONFIG, "START_ANALYSIS_TAP_X", None) is not None:
        model.mark_event(f"Tap singolo di avvio analisi su ({model.CONFIG.START_ANALYSIS_TAP_X}, {model.CONFIG.START_ANALYSIS_TAP_Y})")
        utils.tap(serial, model.CONFIG.START_ANALYSIS_TAP_X, model.CONFIG.START_ANALYSIS_TAP_Y)

    idx = 1
    gray_seen = False
    green_elapsed = None
    hard_deadline = time.perf_counter() + model.CONFIG.GREEN_TIMEOUT_SECONDS

    # Imposta la cadenza del loop in base a CONFIG.FPS (es. 10 FPS = 0.1s a frame)
    target_fps = getattr(model.CONFIG, "FPS", 10)
    frame_interval = 1.0 / target_fps

    while time.perf_counter() <= hard_deadline:
        loop_start = time.perf_counter()

        # 1. Cattura ultra-veloce diretta in RAM (senza codifica PNG su Android)
        frame_bgr = utils.capture_frame_bgr(serial)
        if frame_bgr is None:
            # Se la cattura fallisce, rispetta la cadenza ed esegui il retry
            elapsed = time.perf_counter() - loop_start
            time.sleep(max(0.0, frame_interval - elapsed))
            continue

        # 2. Analisi cromatiche in memoria RAM su matrice NumPy
        gray_distance, _ = utils.mean_color_distance(frame_bgr, model.CONFIG.LEFT_STATUS_ROI, model.CONFIG.LEFT_GRAY_TARGET_HEX)
        is_green_now, green_metrics = utils.is_roi_green(frame_bgr, model.CONFIG.LEFT_STATUS_ROI)

        # 3. Log a schermo e su file
        msg = (
            f"[T+{model.format_elapsed(model.session_elapsed())}] ROI sinistra frame {idx}: "
            f"dist_grigio={gray_distance:.2f}, "
            f"dist_verde={green_metrics['distance_to_green']:.2f}, "
            f"dominanza_verde={green_metrics['dominance']:.2f}, "
            f"green_ratio={green_metrics['green_ratio']:.2%}, "
            f"mean_bgr=({green_metrics['mean_b']:.1f},{green_metrics['mean_g']:.1f},{green_metrics['mean_r']:.1f})"
        )
        print(msg)
        view.safe_log_line(msg)

        # 4. Controllo transizione FASE GRIGIA
        if not gray_seen and gray_distance <= model.CONFIG.LEFT_GRAY_DISTANCE_THRESHOLD:
            gray_seen = True
            model.gray_detect_start_perf = time.perf_counter()
            model.mark_event(f"Rilevato stato grigio nella ROI sinistra (target {model.CONFIG.LEFT_GRAY_TARGET_HEX})")
            
            # Salva su disco solo lo screenshot di avvenuta rilevazione del grigio
            png_bytes = utils.bgr_to_png(frame_bgr)
            utils.save_png(png_bytes, f"monitor_left_GRAY_frame_{idx}_{time.strftime('%Y%m%d_%H%M%S')}.png")

        # 5. Controllo transizione FASE VERDE (Obiettivo)
        if is_green_now:
            # Salva su disco lo screenshot dell'obiettivo raggiunto
            png_bytes = utils.bgr_to_png(frame_bgr)
            utils.save_png(png_bytes, f"monitor_left_GREEN_frame_{idx}_{time.strftime('%Y%m%d_%H%M%S')}.png")

            if model.second_relay_perf is not None:
                model.second_relay_to_green_elapsed = time.perf_counter() - model.second_relay_perf
                model.mark_event(
                    f"Obiettivo principale: verde raggiunto in {model.format_elapsed(model.second_relay_to_green_elapsed)} dal click relay"
                )
            else:
                model.second_relay_to_green_elapsed = None

            if gray_seen and model.gray_detect_start_perf is not None:
                green_elapsed = time.perf_counter() - model.gray_detect_start_perf
                model.mark_event(
                    f"Rilevato passaggio al verde nella ROI sinistra in {model.format_elapsed(green_elapsed)} dalla fase grigia"
                )
            else:
                green_elapsed = None
                model.mark_event("ROI sinistra già verde senza fase grigia agganciata: test fatto proseguire comunque")

            return True, green_elapsed

        idx += 1

        # 6. Pacing dinamico del tempo per mantenere l'FPS richiesto
        elapsed = time.perf_counter() - loop_start
        sleep_time = frame_interval - elapsed
        if sleep_time > 0:
            time.sleep(sleep_time)

    model.mark_event(f"Timeout massimo di {model.CONFIG.GREEN_TIMEOUT_SECONDS} secondi senza passaggio al verde")
    return False, green_elapsed

def validate_carplay_frames(serial):
    """Verifica la schermata CarPlay e ritorna il risultato e la similarità."""
    model.mark_event("Avvio controllo finale CarPlay")
    scores = []

    for i in range(1, model.CONFIG.PRECHECK_FRAMES + 1):
        png = utils.capture_png(serial)
        if not png:
            return False, scores, None, False

        utils.save_png(png, f"carplay_check_{i}_{time.strftime('%Y%m%d_%H%M%S')}.png")
        score = utils.similarity_score(model.CONFIG.CARPLAY_REFERENCE_IMAGE, png)
        scores.append(score)

        msg = f"[T+{model.format_elapsed(model.session_elapsed())}] CarPlay frame {i}: similarita={score:.3f}"
        print(msg)
        view.safe_log_line(msg)

        time.sleep(1 / model.CONFIG.FPS)

    avg_score = (sum(scores) / len(scores)) if scores else -1
    passed = avg_score >= model.CONFIG.CARPLAY_SIMILARITY_THRESHOLD
    if passed:
        model.mark_event(
            f"Controllo finale CarPlay superato tramite media dei {model.CONFIG.PRECHECK_FRAMES} frame: {avg_score:.3f} "
            f"(soglia {model.CONFIG.CARPLAY_SIMILARITY_THRESHOLD:.3f})"
        )
    else:
        model.mark_event(
            f"Controllo finale CarPlay fallito tramite media dei {model.CONFIG.PRECHECK_FRAMES} frame: {avg_score:.3f} "
            f"(soglia {model.CONFIG.CARPLAY_SIMILARITY_THRESHOLD:.3f})"
        )

    final_png = utils.capture_png(serial)
    if final_png is None:
        model.mark_event("Impossibile acquisire lo screen FINAL per il controllo finale")
        return passed, scores, None, False

    utils.save_png(final_png, f"carplay_final_check_{time.strftime('%Y%m%d_%H%M%S')}.png")
    final_score = utils.similarity_score(model.CONFIG.CARPLAY_REFERENCE_IMAGE, final_png)
    msg = f"[T+{model.format_elapsed(model.session_elapsed())}] Screen FINAL: similarita={final_score:.3f}"
    print(msg)
    view.safe_log_line(msg)

    final_threshold = getattr(model.CONFIG, "FINAL_SCREEN_SIMILARITY_THRESHOLD", model.CONFIG.CARPLAY_SIMILARITY_THRESHOLD)
    if not passed and final_score >= final_threshold:
        model.mark_event(
            f"Controllo finale dello screen FINAL superato: similarita={final_score:.3f} "
            f"(soglia {final_threshold:.3f}); media {model.CONFIG.PRECHECK_FRAMES} frame={avg_score:.3f}"
        )
        return True, scores, final_score, True

    if passed:
        return True, scores, final_score, False

    model.mark_event(
        f"Controllo finale dello screen FINAL fallito: similarita={final_score:.3f} "
        f"(soglia {final_threshold:.3f}); media {model.CONFIG.PRECHECK_FRAMES} frame={avg_score:.3f}"
    )
    return False, scores, final_score, False


def save_failure_final(serial, reason, gray_to_green_elapsed=None):
    """Salva il frame finale di fallimento e registra il risultato e la timeline."""
    path = None
    try:
        png = utils.capture_png(serial) if serial else None
        if png:
            final_name = f"FINAL_FAIL_{time.strftime('%Y%m%d_%H%M%S')}.png"
            path = utils.save_png(png, final_name)
            model.mark_event(f"Frame finale di fallimento salvato: {final_name}")
    except Exception as e:
        print(f"Errore nel salvataggio screenshot di fallimento: {e}")
        view.safe_log_line(f"Errore screenshot di fallimento: {e}")

    final_log = compose_result_log(reason, gray_to_green_elapsed)
    view.append_csv(model.CONFIG.CSV_FAILURE, "FAIL", final_log, path)
    view.safe_log_line(final_log)
    return path


def save_success_final(serial, reason, gray_to_green_elapsed=None):
    """Salva il frame finale di successo e registra il risultato e la timeline."""
    path = None
    try:
        png = utils.capture_png(serial)
        if png:
            final_name = f"FINAL_OK_{time.strftime('%Y%m%d_%H%M%S')}.png"
            path = utils.save_png(png, final_name)
            model.mark_event(f"Frame finale di successo salvato: {final_name}")
    except Exception as e:
        print(f"Errore nel salvataggio screenshot di successo: {e}")
        view.safe_log_line(f"Errore screenshot di successo: {e}")

    final_log = compose_result_log(reason, gray_to_green_elapsed)
    view.append_csv(model.CONFIG.CSV_SUCCESS, "SUCCESS", final_log, path)
    view.safe_log_line(final_log)
    return path


def compose_result_log(reason, gray_to_green_elapsed=None):
    """Compone una stringa di log finale con tempi e timeline."""
    total = f"Durata totale sessione: {model.format_elapsed(model.session_elapsed())}"

    if gray_to_green_elapsed is None:
        gray_to_green_text = "Tempo grigio->verde: non disponibile"
    else:
        gray_to_green_text = f"Tempo grigio->verde: {model.format_elapsed(gray_to_green_elapsed)}"

    if model.second_relay_to_green_elapsed is None:
        second_relay_text = "Tempo KL15 click->verde: non disponibile"
    else:
        second_relay_text = f"Tempo KL15 click->verde: {model.format_elapsed(model.second_relay_to_green_elapsed)}"

    timeline = model.build_session_timeline_text()
    return f"{reason} | {second_relay_text} | {gray_to_green_text} | {total} | {timeline}"


def run_deep_sleep_loop():
    """Esegue il ciclo principale del test Deep Sleep."""
    while True:
        try:
            model.reset_session_timing()
            model.session_dir = model.new_session_dir()
            model.log_file = model.session_dir / "tempo_connessione.txt"

            serial = wait_for_device()

            if not serial:
                reason = (
                    f"Device ADB {model.CONFIG.TARGET_SERIAL} non disponibile dopo {model.CONFIG.ADB_CONNECT_TIMEOUT_SECONDS} secondi "
                    f"(probe TCP + adb connect verificato). Riavvio procedura..."
                )
                print(reason)
                save_failure_final(None, reason, None)
                view.safe_log_line(reason)
                view.log_output_deep_sleep_failed(reason)
                adb_kill_server()
                end_of_test_relay_sequence()
                continue

            model.mark_event(f"Connessione ADB confermata: {serial}")

            green_ok, gray_to_green_elapsed = wait_for_gray_to_green(serial)
            if not green_ok:
                reason = (
                    f"La ROI sinistra {model.CONFIG.LEFT_STATUS_ROI} non è passata da grigio a verde "
                    f"entro {model.CONFIG.GREEN_TIMEOUT_SECONDS} secondi. Test fallito."
                )
                print(reason)

                final_fail_path = save_failure_final(serial, reason, gray_to_green_elapsed)
                utils.maybe_execute_no_green_final_recovery(serial, final_fail_path)

                view.log_output_deep_sleep_failed(reason)
                adb_kill_server()
                end_of_test_relay_sequence()
                continue

            carplay_ok, carplay_scores, _, _ = validate_carplay_frames(serial)
            if not carplay_ok:
                avg_score = (sum(carplay_scores) / len(carplay_scores)) if carplay_scores else -1
                reason = (
                    f"I {model.CONFIG.PRECHECK_FRAMES} frame finali non sono abbastanza simili a immagine_carplay.png. "
                    f"Similarita media: {avg_score:.3f}. Test parzialmente fallito."
                )
                print(reason)
                save_failure_final(serial, reason, gray_to_green_elapsed)

                model.mark_event(f"Eseguo tap finale di recovery su ({model.CONFIG.FINAL_FAIL_TAP_X}, {model.CONFIG.FINAL_FAIL_TAP_Y})")
                utils.tap(serial, model.CONFIG.FINAL_FAIL_TAP_X, model.CONFIG.FINAL_FAIL_TAP_Y)

                view.log_output_deep_sleep_partially_failed(avg_score)
                adb_kill_server()
                end_of_test_relay_sequence()
                continue

            avg_score = sum(carplay_scores) / len(carplay_scores)
            model.mark_event(f"Test concluso con successo. Similarita media CarPlay: {avg_score:.3f}")
            success_msg = (
                f"Test concluso correttamente. "
                f"Tempo KL15 click->verde: {model.format_elapsed(model.second_relay_to_green_elapsed) if model.second_relay_to_green_elapsed is not None else 'non disponibile'}. "
                f"Tempo grigio->verde: {model.format_elapsed(gray_to_green_elapsed) if gray_to_green_elapsed is not None else 'non disponibile'}. "
                f"CarPlay rilevato con similarita media {avg_score:.3f}."
            )
            print(success_msg)
            save_success_final(serial, success_msg, gray_to_green_elapsed)
            view.log_output_deep_sleep_passed()

            adb_kill_server()
            end_of_test_relay_sequence()
            continue

        except SystemExit:
            raise
        except Exception as e:
            print(f"Errore inatteso, riavvio procedura: {e}")
            try:
                view.safe_log_line(f"Errore inatteso: {e}")
                fail_reason = f"Errore inatteso: {e} | {model.build_session_timeline_text()}"
                view.append_csv(
                    model.CONFIG.CSV_FAILURE,
                    "FAIL",
                    fail_reason,
                    None
                )
                view.log_output_deep_sleep_failed(fail_reason)
                adb_kill_server()
            except Exception:
                pass
            end_of_test_relay_sequence()
            continue


def run_soft_loop():
    """Esegue il ciclo principale del test Soft Boot."""
    while True:
        try:
            model.reset_session_timing()
            model.session_dir = model.new_session_dir()
            model.log_file = model.session_dir / "tempo_connessione.txt"

            serial = wait_for_device()

            if not serial:
                reason = (
                    f"Device ADB {model.CONFIG.TARGET_SERIAL} non disponibile dopo {model.CONFIG.ADB_CONNECT_TIMEOUT_SECONDS} secondi di tentativi ogni "
                    f"{model.CONFIG.ADB_CONNECT_SPAM_INTERVAL:.2f} secondi."
                )
                print(reason)
                utils.save_png(b"", "")
                view.append_output_csv("FAILED", reason)
                adb_kill_server()
                end_of_test_relay_sequence()
                continue

            model.mark_event(f"Connessione ADB confermata: {serial}")

            green_ok, relay_to_green_elapsed = wait_for_gray_to_green(serial)
            if not green_ok:
                reason = (
                    f"La ROI sinistra {model.CONFIG.LEFT_STATUS_ROI} non è passata da grigio a verde entro {model.CONFIG.GREEN_TIMEOUT_SECONDS} secondi."
                )
                print(reason)
                utils.save_png(b"", "")
                view.append_output_csv("FAILED", reason)
                adb_kill_server()
                end_of_test_relay_sequence()
                continue

            carplay_ok, carplay_scores, final_score, used_final_fallback = validate_carplay_frames(serial)
            if not carplay_ok:
                avg_score = (sum(carplay_scores) / len(carplay_scores)) if carplay_scores else -1
                partial_reason = (
                    f"Tempo impiegato per la connessione CarPlay: {relay_to_green_elapsed:.2f} secondi. "
                    f"View non combacia con quella di CP. Similarita media: {avg_score:.3f}."
                ) if relay_to_green_elapsed is not None else (
                    f"View non combacia con quella di CP. Similarita media: {avg_score:.3f}."
                )

                print(partial_reason)
                model.mark_event(f"Tap correttivo alle coordinate ({model.CONFIG.PARTIAL_FAIL_TAP_X}, {model.CONFIG.PARTIAL_FAIL_TAP_Y})")
                utils.tap(serial, model.CONFIG.PARTIAL_FAIL_TAP_X, model.CONFIG.PARTIAL_FAIL_TAP_Y)
                time.sleep(model.CONFIG.PARTIAL_FAIL_WAIT_SECONDS)
                model.mark_event(f"Attesa correttiva di {model.CONFIG.PARTIAL_FAIL_WAIT_SECONDS} secondi completata")

                utils.save_png(b"", "")
                view.append_output_csv("PARTIALLY FAILED", partial_reason)
                adb_kill_server()
                end_of_test_relay_sequence()
                continue

            avg_score = sum(carplay_scores) / len(carplay_scores)
            model.mark_event(f"Test concluso con successo. Similarita media CarPlay: {avg_score:.3f}")
            success_reason = (
                f"Tempo impiegato per la connessione CarPlay: {relay_to_green_elapsed:.2f} secondi. "
                f"Schermata CarPlay rilevata e aperta."
            ) if relay_to_green_elapsed is not None else "Schermata CarPlay rilevata e aperta."
            if used_final_fallback:
                success_reason += " L'analisi è stata completata sullo screen FINAL perché i 5 frame iniziali non hanno superato la soglia di similarità."

            print(success_reason)
            utils.save_png(b"", "")
            view.append_output_csv("PASSED", success_reason)

            adb_kill_server()
            end_of_test_relay_sequence()
            continue

        except SystemExit:
            raise
        except Exception as e:
            print(f"Errore inatteso, riavvio procedura: {e}")
            try:
                view.safe_log_line(f"Errore inatteso: {e}")
                view.append_output_csv("FAILED", f"Errore inatteso: {e}")
                adb_kill_server()
            except Exception:
                pass
            end_of_test_relay_sequence()
            continue