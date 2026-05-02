"""pytest 配置：将 src/ 加入 sys.path，使 superhealth 包可直接 import。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

FIXTURE_DIR = Path(__file__).parent.parent / "data" / "activity-data" / "garmin"
