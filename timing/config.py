from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
DATA = ROOT / "data"
SEPARATOR_DIR = MODELS / "separator"
SEPARATOR_NAME = "melband-roformer-kim-vocals"
os.environ.setdefault("HF_HOME", str(MODELS / ".cache"))
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def initialize():
    DATA.mkdir(exist_ok=True)
    MODELS.mkdir(exist_ok=True)
