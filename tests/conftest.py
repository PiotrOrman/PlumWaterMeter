"""Load the integration's pure modules without Home Assistant.

``custom_components/ewebtel/__init__.py`` imports Home Assistant, so the
package is registered here as an empty module and only ``processing`` and
``api`` are imported from it.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "ewebtel"

package = types.ModuleType("ewebtel")
package.__path__ = [str(PACKAGE_DIR)]
sys.modules.setdefault("ewebtel", package)

processing = importlib.import_module("ewebtel.processing")
api = importlib.import_module("ewebtel.api")
