import subprocess
import time

import banchetto_model as model
import banchetto_view as view
import banchetto_utils as utils


def fatal_stop(reason, screenshot_path=None):
    """Termina lo script dopo aver registrato il motivo di stop."""
    print(reason)
    view.safe_log_line(reason)
    view.append_output_csv("FAILED", reason)
    try:
        subprocess.run([model.CONFIG.ADB, "kill-server"], capture_output=True, text=True)
    except Exception:
        pass
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


def adb_devices_contains_target():
    """Verifica se il device target è presente nella lista adb devices."""
    out = subprocess.run([model.CONFIG.ADB, "devices"], capture_output=True, text=True).stdout.splitlines()
    for line in out[1:]:
        line = line.strip()
        if line.startswith(model.CONFIG.TARGET_SERIAL) and line.endswith("device"):
            return True
    return False


def try_adb_connect_once(attempt):
    """Esegue un tentativo di connessione adb al device target."""
    try:
        result = subprocess.run(
            [model.CONFIG.ADB, "connect", model.CONFIG.TARGET_SERIAL],
            capture_output=True,
            text=True,
            timeout=model.CONFIG.ADB_SINGLE_CONNECT_TIMEOUT_SECONDS
        )

        stdout_text = result.stdout.strip()
        stderr_text = result.stderr.strip()

        if stdout_text:
            print(f"[ADB connect #{attempt}] {stdout_text}")
            view.safe_log_line(f"[ADB connect #{attempt}] {stdout_text}")

        if stderr_text:
            print(f"[ADB connect #{attempt}][stderr] {stderr_text}")
            view.safe_log_line(f"[ADB connect #{attempt}][stderr] {stderr_text}")

        return result.returncode, stdout_text, stderr_text, False

    except subprocess.TimeoutExpired:
        msg = (
            f"[ADB connect #{attempt}] timeout locale dopo "
            f"{model.CONFIG.ADB_SINGLE_CONNECT_TIMEOUT_SECONDS:.2f}s, nuovo tentativo immediato"
        )
        print(msg)
        view.safe_log_line(msg)
        return None, "", "", True


def wait_for_device():
    """Avvia la sessione e prova a connettersi al device tramite ADB."""
    model.start_session_timing()
    model.mark_event("Primo click relay di avvio test")
    pulse_relays()
    model.second_relay_perf = time.perf_counter()
    model.mark_event("Cronometro principale avviato: misuro dal click relay al verde")

    if getattr(model.CONFIG, "SECOND_RELAY_DELAY_SECONDS", None) is not None:
        model.mark_event(f"Attesa passiva di {model.CONFIG.SECOND_RELAY_DELAY_SECONDS} secondi dopo il primo click relay")
        time.sleep(model.CONFIG.SECOND_RELAY_DELAY_SECONDS)
        model.mark_event("Secondo click relay prima della connessione ADB")
        pulse_relays()

    model.mark_event(
        f"Avvio spam di adb connect con timeout locale {model.CONFIG.ADB_SINGLE_CONNECT_TIMEOUT_SECONDS:.2f}s e retry immediato per massimo {model.CONFIG.ADB_CONNECT_TIMEOUT_SECONDS}s"
    )

    deadline = time.perf_counter() + model.CONFIG.ADB_CONNECT_TIMEOUT_SECONDS
    attempt = 0

    while time.perf_counter() < deadline:
        if adb_devices_contains_target():
            model.mark_event("Device già visibile in adb devices")
            return model.CONFIG.TARGET_SERIAL

        attempt += 1
        _, _, _, timed_out = try_adb_connect_once(attempt)

        if adb_devices_contains_target():
            model.mark_event(f"Connessione ADB riuscita al tentativo {attempt}")
            return model.CONFIG.TARGET_SERIAL

        if timed_out:
            continue

        if model.CONFIG.ADB_CONNECT_SPAM_INTERVAL > 0:
            time.sleep(model.CONFIG.ADB_CONNECT_SPAM_INTERVAL)

    model.mark_event(f"Timeout connessione ADB dopo {model.CONFIG.ADB_CONNECT_TIMEOUT_SECONDS} secondi")
    return None


def end_of_test_relay_sequence():
    """Chiude il test azionando il relay e attivando il cooldown."""
    model.mark_event("Click relay di fine test")
    pulse_relays()
    model.cooldown_restart(model.CONFIG.RESTART_DELAY_SECONDS)


def wait_for_gray_to_green(serial):
    """Monitora la ROI finché non passa da grigio a verde."""
    model.mark_event("Avvio catture schermata e monitoraggio ROI sinistra CarPlay")
    if getattr(model.CONFIG, "START_ANALYSIS_TAP_X", None) is not None:
        model.mark_event(f"Tap singolo di avvio analisi su ({model.CONFIG.START_ANALYSIS_TAP_X}, {model.CONFIG.START_ANALYSIS_TAP_Y})")
        utils.tap(serial, model.CONFIG.START_ANALYSIS_TAP_X, model.CONFIG.START_ANALYSIS_TAP_Y)

    idx = 1
    gray_seen = False
    green_elapsed = None
    hard_deadline = time.perf_counter() + model.CONFIG.GREEN_TIMEOUT_SECONDS

    while time.perf_counter() <= hard_deadline:
        png = utils.capture_png(serial)
        if not png:
            time.sleep(1 / model.CONFIG.FPS)
            continue

        utils.save_png(png, f"monitor_left_roi_{idx}_{time.strftime('%Y%m%d_%H%M%S')}.png")
        gray_distance, _ = utils.mean_color_distance(png, model.CONFIG.LEFT_STATUS_ROI, model.CONFIG.LEFT_GRAY_TARGET_HEX)
        is_green_now, green_metrics = utils.is_roi_green(png, model.CONFIG.LEFT_STATUS_ROI)

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

        if not gray_seen and gray_distance <= model.CONFIG.LEFT_GRAY_DISTANCE_THRESHOLD:
            gray_seen = True
            model.gray_detect_start_perf = time.perf_counter()
            model.mark_event(f"Rilevato stato grigio nella ROI sinistra (target {model.CONFIG.LEFT_GRAY_TARGET_HEX})")

        if is_green_now:
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
        time.sleep(1 / model.CONFIG.FPS)

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
        png = utils.capture_png(serial)
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
                    f"Device ADB {model.CONFIG.TARGET_IP} non disponibile dopo {model.CONFIG.ADB_CONNECT_TIMEOUT_SECONDS} secondi di spam adb connect con timeout locale "
                    f"{model.CONFIG.ADB_SINGLE_CONNECT_TIMEOUT_SECONDS:.2f}s. Riavvio procedura..."
                )
                print(reason)
                save_failure_final(model.CONFIG.TARGET_IP, reason, None)
                view.safe_log_line(reason)
                view.log_output_deep_sleep_failed(reason)
                subprocess.run([model.CONFIG.ADB, "kill-server"], capture_output=True, text=True)
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
                subprocess.run([model.CONFIG.ADB, "kill-server"], capture_output=True, text=True)
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
                subprocess.run([model.CONFIG.ADB, "kill-server"], capture_output=True, text=True)
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

            subprocess.run([model.CONFIG.ADB, "kill-server"], capture_output=True, text=True)
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
                subprocess.run([model.CONFIG.ADB, "kill-server"], capture_output=True, text=True)
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
                subprocess.run([model.CONFIG.ADB, "kill-server"], capture_output=True, text=True)
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
                subprocess.run([model.CONFIG.ADB, "kill-server"], capture_output=True, text=True)
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
                subprocess.run([model.CONFIG.ADB, "kill-server"], capture_output=True, text=True)
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

            subprocess.run([model.CONFIG.ADB, "kill-server"], capture_output=True, text=True)
            end_of_test_relay_sequence()
            continue

        except SystemExit:
            raise
        except Exception as e:
            print(f"Errore inatteso, riavvio procedura: {e}")
            try:
                view.safe_log_line(f"Errore inatteso: {e}")
                view.append_output_csv("FAILED", f"Errore inatteso: {e}")
                subprocess.run([model.CONFIG.ADB, "kill-server"], capture_output=True, text=True)
            except Exception:
                pass
            end_of_test_relay_sequence()
            continue
