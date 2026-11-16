import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# izolowany katalog cache dla testów (przed importem src.core.config)
os.environ.setdefault("MROLNIK_CACHE_DIR", tempfile.mkdtemp(prefix="mrolnik-test-cache-"))
