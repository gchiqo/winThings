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

# In a PyInstaller one-folder bundle the data directories (piper/, voices/,
# kokoro/) are placed next to the .exe, not next to __file__ (which resolves
# into _internal/). BUNDLE_DIR always points to the right base so submodules
# can find bundled assets in both the dev and packaged layouts.
if getattr(sys, 'frozen', False):
    BUNDLE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BUNDLE_DIR = HERE
