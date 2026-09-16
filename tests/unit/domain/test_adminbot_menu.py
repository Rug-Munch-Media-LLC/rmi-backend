"""Guard: every admin-bot menu button resolves to a callback handler.

Scans the keyboard definitions (helpers.py) and asserts each callback_data
string appears in the handler sources OR matches a prefix family handled via
``startswith(...)``. Catches dead buttons in CI before users ever tap them.
"""

from __future__ import annotations

from pathlib import Path

import app.domains.telegram.adminbot as adminbot_pkg
from app.domains.telegram.adminbot.helpers import (
    SECTION_MENUS,
    control_panel_keyboard,
    main_menu_keyboard,
    section_menu_keyboard,
)

ADMINBOT_DIR = Path(adminbot_pkg.__file__).parent


def _collect_callback_data() -> set[str]:
    data: set[str] = set()
    keyboards = [main_menu_keyboard(), control_panel_keyboard()]
    for section in SECTION_MENUS:
        keyboards.append(section_menu_keyboard(section))
    for kb in keyboards:
        for row in kb.inline_keyboard:
            for button in row:
                if button.callback_data:
                    data.add(button.callback_data)
    return data


def _handler_corpus() -> str:
    """All adminbot source EXCEPT helpers.py (where the buttons are defined)."""
    parts: list[str] = []
    for path in ADMINBOT_DIR.rglob("*.py"):
        if "__pycache__" in str(path) or path.name == "helpers.py":
            continue
        parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _resolves(data: str, corpus: str) -> bool:
    if data in corpus:
        return True
    prefixes = {data.split("_")[0] + "_", data.rsplit("_", 1)[0] + "_"}
    return any(f'startswith("{prefix}")' in corpus or f"startswith('{prefix}')" in corpus for prefix in prefixes)


def test_every_menu_button_has_a_handler() -> None:
    corpus = _handler_corpus()
    dead = sorted(d for d in _collect_callback_data() if not _resolves(d, corpus))
    assert not dead, f"menu buttons with no handler: {dead}"


def test_sections_have_titles() -> None:
    from app.domains.telegram.adminbot.helpers import _SECTION_TITLES

    for section in SECTION_MENUS:
        assert section in _SECTION_TITLES, f"section '{section}' missing a title"


def test_menu_sections_not_empty() -> None:
    for section, rows in SECTION_MENUS.items():
        assert rows, f"section '{section}' has no buttons"
