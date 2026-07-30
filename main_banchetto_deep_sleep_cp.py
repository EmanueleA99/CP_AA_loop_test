import time
from pathlib import Path
from types import SimpleNamespace

import banchetto_controller as controller
import banchetto_model as model

RUN_TIMESTAMP = time.strftime("%d_%m_%y_%H%M")

CONFIG = SimpleNamespace(
    ADB="adb",  # assume adb nel PATH su Ubuntu; usa path assoluto se necessario
    BASE_DIR=Path(__file__).resolve().parent,
    CARPLAY_REFERENCE_IMAGE=Path(__file__).resolve().parent / "img" / "immagine_carplay.png",
    DESKTOP_DIR=Path(__file__).resolve().parent / "output" / "Test_Deep_Sleep",
    FPS=5,
    PRECHECK_FRAMES=5,
    CARPLAY_SIMILARITY_THRESHOLD=0.40,
    ADB_CONNECT_SPAM_INTERVAL=0.0,
    ADB_CONNECT_TIMEOUT_SECONDS=120,
    ADB_SINGLE_CONNECT_TIMEOUT_SECONDS=0.5,
    RESTART_DELAY_SECONDS=240,
    GREEN_TIMEOUT_SECONDS=120,
    SECOND_RELAY_DELAY_SECONDS=1,
    CSV_SUCCESS=Path(__file__).resolve().parent / "output" / "Test_Deep_Sleep" / f"log_successi_{RUN_TIMESTAMP}.csv",
    CSV_FAILURE=Path(__file__).resolve().parent / "output" / "Test_Deep_Sleep" / f"log_fallimenti_{RUN_TIMESTAMP}.csv",
    CSV_OUTPUT_DEEP_SLEEP=Path(__file__).resolve().parent / "output" / "Test_Deep_Sleep" / f"results_deepsleep_{RUN_TIMESTAMP}.csv",
    RELAY_CHANNEL_1="QAAMZ_1",
    RELAY_CHANNEL_2="QAAMZ_2",
    RELAY_PULSE_HOLD_SECONDS=0.3,
    SCREEN_DISPLAY_ID=4633128631561747456,
    TARGET_IP="172.16.250.248",
    TARGET_PORT="5555",
    TARGET_SERIAL="172.16.250.248:5555",
    LEFT_STATUS_ROI=(29, 483, 88, 543),
    LEFT_GRAY_TARGET_HEX="#959ba4",
    LEFT_GREEN_TARGET_HEX="#60e255",
    LEFT_GRAY_DISTANCE_THRESHOLD=45.0,
    LEFT_GREEN_DISTANCE_THRESHOLD=55.0,
    GREEN_DOMINANCE_MIN=25.0,
    GREEN_PIXELS_MIN_RATIO=0.35,
    FINAL_FAIL_TAP_X=60,
    FINAL_FAIL_TAP_Y=510,
    FINAL_FAIL_PIXEL_X=58,
    FINAL_FAIL_PIXEL_Y=532,
    FINAL_FAIL_PIXEL_TARGET_HEX="#969696",
    FINAL_FAIL_PIXEL_DISTANCE_THRESHOLD=12.0,
    FINAL_FAIL_RECOVERY_TAP_X=60,
    FINAL_FAIL_RECOVERY_TAP_Y=736,
    SPECIAL_HOLD_START_X=1603,
    SPECIAL_HOLD_START_Y=260,
    SPECIAL_SWIPE_END_X=58,
    SPECIAL_SWIPE_END_Y=532,
    SPECIAL_HOLD_BEFORE_SWIPE_MS=700,
    SPECIAL_TOTAL_SWIPE_MS=1400,
)

model.load_config(CONFIG)

if __name__ == "__main__":
    controller.run_deep_sleep_loop()
