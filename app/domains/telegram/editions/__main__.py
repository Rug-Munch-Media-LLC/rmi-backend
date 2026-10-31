"""``python -m app.domains.telegram.editions`` — the dry run.

Render-only: constructs no sender, resolves no live channel, writes nothing.
It imports the app package (and therefore ``app.config``), so it needs the
same env every other entrypoint needs — ``ENVIRONMENT=dev`` is enough for a
render:

    ENVIRONMENT=dev .venv/bin/python -m app.domains.telegram.editions --pretty

Production still refuses to boot without a real secret; this module cannot
mask that.
"""

from __future__ import annotations

import sys

from app.domains.telegram.editions.cli import main

if __name__ == "__main__":
    sys.exit(main())
