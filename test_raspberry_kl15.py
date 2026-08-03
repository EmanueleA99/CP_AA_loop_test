import atexit
import subprocess
import time

import banchetto_model as model
import banchetto_view as view
import banchetto_utils as utils

try:
    from gpiozero import LED
except ImportError:
    # gpiozero non installato: lo segnaliamo solo quando serve davvero pilotare
    # un relè, con un messaggio chiaro su cosa installare.
    LED = None

def pulse_relays():
    """Invia un impulso ai relè via GPIO per simulare la pressione del pulsante KL15.

    Il pilotaggio avviene direttamente sui pin GPIO del Raspberry Pi (libreria
    gpiozero), non più tramite la CLI 'usbrelay': quest'ultima serve solo per
    relay board USB HID, mentre le board a moduli relè (es. keyestudio) si
    collegano direttamente ai pin GPIO e vanno pilotate a livello TTL.

    Per cambiare board relè in futuro NON serve toccare questa funzione: basta
    aggiornare in CONFIG (nel main_banchetto_*.py):
      - RELAY_CHANNEL_1 / RELAY_CHANNEL_2 -> numero del pin GPIO (numerazione
        BCM) collegato a ciascun canale relè
      - RELAY_ACTIVE_LOW -> True se il relè si chiude portando il pin a LOW,
        False se si chiude portando il pin a HIGH (va verificato empiricamente
        sul modulo specifico: board diverse hanno polarità diverse)
      - RELAY_PULSE_HOLD_SECONDS -> durata dell'impulso, come già oggi
    """
    pin1 = 06
    pin2 = 26
    active_low = false
    hold_seconds = 0.3

    try:

        if active_low:
            relay1.off()
            relay2.off()
        else:
            relay1.on()
            relay2.on()

        time.sleep(hold_seconds)

        if active_low:
            relay1.on()
            relay2.on()
        else:
            relay1.off()
            relay2.off()

    except Exception as e:
        fatal_stop(
            f"Impossibile pilotare i relè GPIO (pin {pin1}/{pin2}). "
            f"Controlla i collegamenti, i permessi del gruppo 'gpio' e che "
            f"gpiozero/lgpio siano installati. Errore: {e}. Arresto definitivo dello script."
        )
