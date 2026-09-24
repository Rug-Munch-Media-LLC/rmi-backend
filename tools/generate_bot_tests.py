"""Generate contract tests for the admin-bot chat and notify packages.

One test per file, each a single TestCase method, under the 50-line budget.
Asserts use the bare `assert` form this repo uses (6,319 bare vs 14 unittest
style). Collaborators are injected by assignment-and-restore, never patched.

Run from the repo root:  python3 tools/generate_bot_tests.py
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "unit" / "adminbot_chat"
BUDGET = 50

TEMPLATE = '''"""{doc}"""

from __future__ import annotations

import unittest

{body}

if __name__ == "__main__":
    unittest.main()
'''

CASES: dict[str, tuple[str, str]] = {}

CASES["intents_translate_known"] = (
    "Known phrases translate to an existing command",
    """from app.domains.telegram.adminbot.chat.intents import classify


class T(unittest.TestCase):
    def test_translates(self) -> None:
        for text, want in (
            ("restart nginx", "svc nginx restart"),
            ("stop the api", "svc api stop"),
            ("show me the docker containers", "dps"),
            ("how are the disks", "disk"),
        ):
            assert classify(text) == want, text""",
)

CASES["intents_ignore_unknown"] = (
    "Free-form text is never guessed at a command",
    """from app.domains.telegram.adminbot.chat.intents import classify


class T(unittest.TestCase):
    def test_ignores(self) -> None:
        for text in ("what is the weather", "hello", "", "   "):
            assert classify(text) is None, text""",
)

CASES["intents_deploy_is_owner_only"] = (
    "Deploy is routed but flagged owner-only",
    """from app.domains.telegram.adminbot.chat.intents import OPEN_INTENTS, classify


class T(unittest.TestCase):
    def test_owner_only(self) -> None:
        assert classify("deploy now") == "deploy"
        assert "deploy" not in OPEN_INTENTS""",
)

CASES["owner_allowlist_readonly"] = (
    "Read-only commands stay open to every allowed user",
    """import app.domains.telegram.adminbot.chat.owner_commands as unit


class T(unittest.TestCase):
    def test_readonly(self) -> None:
        for command in ("ls -la", "df -h", "ps aux | head"):
            assert unit.allowed_for(command, False), command""",
)

CASES["owner_allowlist_widens_for_owner"] = (
    "Write commands are allowed for the owner only",
    """import app.domains.telegram.adminbot.chat.owner_commands as unit


class T(unittest.TestCase):
    def test_widens(self) -> None:
        assert unit.allowed_for("systemctl restart nginx", True)
        assert not unit.allowed_for("systemctl restart nginx", False)""",
)

CASES["owner_allowlist_precedence"] = (
    "Owner-only verbs win even when they are also read-only allowed",
    """import app.domains.telegram.adminbot.chat.owner_commands as unit


class T(unittest.TestCase):
    def test_precedence(self) -> None:
        # systemctl/docker/git appear in BOTH lists. Checking the read-only list
        # first would let any allowed user restart services or push code.
        for command in ("systemctl restart nginx", "docker ps", "git push"):
            assert not unit.allowed_for(command, False), command
            assert unit.allowed_for(command, True), command""",
)

CASES["owner_allowlist_blocks_smuggling"] = (
    "A privileged verb cannot hide behind a harmless command",
    """import app.domains.telegram.adminbot.chat.owner_commands as unit


class T(unittest.TestCase):
    def test_blocks(self) -> None:
        for command in ("ls; reboot", "ls && rm -rf /", "ls | shutdown"):
            assert not unit.allowed_for(command, False), command""",
)

CASES["health_severity_ok"] = (
    "A calm host rates ok",
    '''from app.domains.telegram.adminbot.notify.health_severity import rate


class T(unittest.TestCase):
    def test_ok(self) -> None:
        level, _ = rate({"load1": 1.0, "disk_pct": 40.0, "mem_pct": 45.0})
        assert level == "ok"''',
)

CASES["health_severity_warns"] = (
    "A host over a warning threshold rates warn",
    """from app.domains.telegram.adminbot.notify.health_severity import rate


class T(unittest.TestCase):
    def test_warn(self) -> None:
        level, reason = rate({"load1": 1.0, "disk_pct": 85.0, "mem_pct": 45.0})
        assert level == "warn"
        assert "disk" in reason""",
)

CASES["health_severity_critical_outranks"] = (
    "Critical outranks warn so the worst signal is reported",
    """from app.domains.telegram.adminbot.notify.health_severity import rate


class T(unittest.TestCase):
    def test_critical(self) -> None:
        level, reason = rate({"load1": 1.0, "disk_pct": 85.0, "mem_pct": 96.0})
        assert level == "critical"
        assert "memory" in reason""",
)

CASES["payload_shape_matches_formatter"] = (
    "Payloads carry the keys the existing alert formatter reads",
    """from app.domains.telegram.adminbot.notify.payload import build


class T(unittest.TestCase):
    def test_shape(self) -> None:
        alert = build("HostHealth", "warn", "Host warn: disk 85%", "threshold crossed")
        assert alert["status"] == "firing"
        assert alert["labels"]["alertname"] == "HostHealth"
        assert alert["labels"]["severity"] == "warn"
        assert alert["annotations"]["summary"] == "Host warn: disk 85%"
        assert "startsAt" in alert""",
)

CASES["payload_resolved_status"] = (
    "A resolved alert keeps its name and severity",
    '''from app.domains.telegram.adminbot.notify.payload import build


class T(unittest.TestCase):
    def test_resolved(self) -> None:
        alert = build("HostHealth", "info", "back to normal", status="resolved")
        assert alert["status"] == "resolved"
        assert alert["labels"]["alertname"] == "HostHealth"''',
)

CASES["models_choose_rejects_unknown"] = (
    "An unknown lane is refused rather than stored",
    """from app.domains.telegram.adminbot.chat.lanes import LANES
from app.domains.telegram.adminbot.chat.models_choose import choose
from app.domains.telegram.adminbot.chat.models_current import KEY


class FakeContext:
    def __init__(self) -> None:
        self.user_data: dict = {}


class T(unittest.TestCase):
    def test_rejects(self) -> None:
        context = FakeContext()
        assert choose(context, "not-a-lane") == "Unknown model"
        assert KEY not in context.user_data
        known = LANES[0]["id"]
        assert choose(context, known) != "Unknown model"
        assert context.user_data[KEY] == known""",
)

CASES["models_current_defaults"] = (
    "The selected lane defaults to the workhorse",
    '''from app.domains.telegram.adminbot.chat.models_current import DEFAULT, current


class FakeContext:
    def __init__(self) -> None:
        self.user_data: dict = {}


class T(unittest.TestCase):
    def test_default(self) -> None:
        assert current(FakeContext()) == DEFAULT
        context = FakeContext()
        context.user_data["selected_model"] = "hermes"
        assert current(context) == "hermes"''',
)


def write_all() -> int:
    """Write every test module, enforce the line budget, and compile-check."""
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "__init__.py").write_text('"""Contract tests for the admin-bot chat and notify packages."""\n')
    for old in OUT.glob("test_*.py"):
        old.unlink()
    for name, (doc, body) in CASES.items():
        (OUT / f"test_{name}.py").write_text(TEMPLATE.format(doc=doc, body=body))
    problems: list[str] = []
    for path in sorted(OUT.glob("test_*.py")):
        text = path.read_text()
        lines = len(text.splitlines())
        if lines >= BUDGET:
            problems.append(f"{path.name}: {lines} lines (budget {BUDGET})")
        try:
            compile(text, str(path), "exec")
        except SyntaxError as exc:
            problems.append(f"{path.name}: syntax {exc.msg} line {exc.lineno}")
    for problem in problems:
        print("  " + problem)
    if problems:
        return 1
    print(f"generated {len(CASES)} test modules, each under {BUDGET} lines, all compile")
    return 0


if __name__ == "__main__":
    raise SystemExit(write_all())
