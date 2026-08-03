# test_relay_pins.py — cicla i pin candidati e osserva quale relè scatta
from gpiozero import LED
from time import sleep

candidate_pins = [4, 17, 27, 22, 6, 26, 5, 13]  # aggiungi/rimuovi in base al layout

for pin in candidate_pins:
    print(f"Test GPIO{pin}: attivo per 1s...")
    relay = LED(pin)
    relay.on()
    sleep(1)
    relay.off()
    relay.close()
    sleep(0.5)
