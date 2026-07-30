import time
from pathlib import Path
from types import SimpleNamespace

import banchetto_controller as controller
import banchetto_model as model

RUN_TIMESTAMP = time.strftime("%d_%m_%y_%H%M")

CONFIG = SimpleNamespace(
    ADB="adb",  # assume adb nel PATH su Ubuntu; usa path assoluto se necessario
    BASE_DIR=Path(__file__).resolve().parent,
    CARPLAY_REFERENCE_IMAGE=Path(__file__).resolve().parent / "img" / "immagine_android.png",
    DESKTOP_DIR=Path(__file__).resolve().parent / "output" / "Test_Soft_Boot_AndroidAuto",
    FPS=2,
    PRECHECK_FRAMES=5,
    CARPLAY_SIMILARITY_THRESHOLD=0.40,
    FINAL_SCREEN_SIMILARITY_THRESHOLD=0.40,
    ADB_CONNECT_SPAM_INTERVAL=0.10,
    ADB_CONNECT_TIMEOUT_SECONDS=120,
    ADB_SINGLE_CONNECT_TIMEOUT_SECONDS=0.5,
    RESTART_DELAY_SECONDS=80,
    GREEN_TIMEOUT_SECONDS=120,
    OUTPUT_CSV=Path(__file__).resolve().parent / "output" / "Test_Soft_Boot_AndroidAuto" / f"results_androidauto_{RUN_TIMESTAMP}.csv",
    RELAY_CHANNEL_1="QAAMZ_1",
    RELAY_CHANNEL_2="QAAMZ_2",
    RELAY_PULSE_HOLD_SECONDS=0.3,
    SCREEN_DISPLAY_ID=4633128631561747456,
    TARGET_IP="172.16.250.248",
    TARGET_PORT="5555",
    TARGET_SERIAL="172.16.250.248:5555",
    LEFT_STATUS_ROI=(29, 483, 88, 543),
    LEFT_GRAY_TARGET_HEX="#959ba4",
    LEFT_GREEN_TARGET_HEX="#15a2e7",
    LEFT_GRAY_DISTANCE_THRESHOLD=45.0,
    LEFT_GREEN_DISTANCE_THRESHOLD=55.0,
    GREEN_DOMINANCE_MIN=25.0,
    GREEN_PIXELS_MIN_RATIO=0.35,
    PARTIAL_FAIL_TAP_X=60,
    PARTIAL_FAIL_TAP_Y=510,
    PARTIAL_FAIL_WAIT_SECONDS=3,
)

model.load_config(CONFIG)

if __name__ == "__main__":
    controller.run_soft_loop()
