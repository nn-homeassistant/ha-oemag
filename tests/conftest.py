"""Make the pure modules of the integration importable on their own."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components" / "oemag"))
