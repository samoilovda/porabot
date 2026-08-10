"""Test-collection bootstrap.

bot/config.py instantiates Settings() at import time, which requires
BOT_TOKEN to be set — every test module transitively imports bot.config the
moment it imports anything under bot.*, so collection fails before a single
test runs unless BOT_TOKEN is already in the environment. CI sets it
explicitly (see .github/workflows/deploy.yml); locally, every contributor
hits this the first time they run pytest. setdefault() only fills in what's
missing, so CI's real value (and anyone else's local override) still wins.
"""

import os

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
