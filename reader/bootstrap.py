"""Put the repo root on sys.path so `import common` works however we're launched.

The reader is run as a plain script (`python reader\\app.py`), not as an
installed package, so nothing else puts the repo root on the path. Every
module in here that touches `common` imports this first.
"""

import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(REPO, "reader")

for path in (REPO, HERE):
    if path not in sys.path:
        sys.path.insert(0, path)
