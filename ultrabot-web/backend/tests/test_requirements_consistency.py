"""Regression guards for the dependency-resolution contract (v0.4.11.1).

A fresh `pip install -r requirements.txt` used to be ResolutionImpossible:
fyers-apiv3 (every published version) hard-pins aiohttp==3.8.x/3.9.x while
requirements.txt demanded aiohttp>=3.10.0 AND listed fyers-apiv3 itself —
no version combination could ever satisfy both. Anyone setting up a fresh
machine, CI, or clone hit the wall before a single line of code ran.

The contract guarded here:
  1. requirements.txt NEVER lists fyers-apiv3 — the SDK is installed
     separately with --no-deps (requirements-fyers.txt; setup.sh step 3).
  2. The aiohttp pin in requirements.txt stays exactly at the version the
     pinned fyers SDK requires, so both files resolve as one consistent set.
  3. requirements-fyers.txt keeps pinning fyers-apiv3 and documenting the
     --no-deps install; requirements-fyers-extra.txt keeps the SDK's
     undeclared runtime deps; setup.sh keeps performing the two-step install.
  4. Direct aiohttp consumers in backend code keep their dependency
     declared in requirements.txt.

These are static, deterministic checks — no network, no pip invocation.
"""

import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REQUIREMENTS = BACKEND_DIR / "requirements.txt"
REQUIREMENTS_FYERS = BACKEND_DIR / "requirements-fyers.txt"
REQUIREMENTS_FYERS_EXTRA = BACKEND_DIR / "requirements-fyers-extra.txt"
SETUP_SH = BACKEND_DIR.parent.parent / "setup.sh"

# fyers-apiv3 version -> the exact aiohttp version its package metadata pins.
# Verified against the installed SDK (3.1.16 -> aiohttp 3.9.3, the combination
# the full backend suite runs green on). When the SDK pin in
# requirements-fyers.txt is ever bumped, add the new version's required
# aiohttp here after checking its metadata.
FYERS_AIOHTTP_PIN = {
    "3.1.16": "3.9.3",
}


def _requirement_lines(path: Path):
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            yield line


def _find_requirement(path: Path, package: str):
    """Return the requirement line for `package`, ignoring comments."""
    for line in _requirement_lines(path):
        name = re.split(r"[<>=!~\[ ]", line, 1)[0].strip().lower()
        if name == package:
            return line
    return None


def test_core_requirements_do_not_list_fyers():
    line = _find_requirement(REQUIREMENTS, "fyers-apiv3")
    assert line is None, (
        f"requirements.txt lists {line!r} — fyers-apiv3 hard-pins old aiohttp "
        "versions, which makes a plain `pip install -r requirements.txt` "
        "ResolutionImpossible on a clean machine. The SDK must only be "
        "installed separately with --no-deps from requirements-fyers.txt "
        "(setup.sh step 3 performs this)."
    )


def test_aiohttp_pin_matches_fyers_sdk_requirement():
    aio = _find_requirement(REQUIREMENTS, "aiohttp")
    assert aio is not None, (
        "aiohttp vanished from requirements.txt — news/news_engine.py imports "
        "it directly, so it must stay declared"
    )
    m = re.search(r"==\s*([0-9][0-9a-zA-Z.]*)", aio)
    assert m, (
        f"aiohttp must be EXACT-pinned (==) to the version the fyers SDK "
        f"requires so both files always resolve as one set; got: {aio!r}"
    )
    aio_version = m.group(1)

    fyers_line = _find_requirement(REQUIREMENTS_FYERS, "fyers-apiv3")
    assert fyers_line, "requirements-fyers.txt no longer pins fyers-apiv3"
    fm = re.search(r"fyers[-_]apiv3==([0-9][0-9a-zA-Z.]*)", fyers_line, re.IGNORECASE)
    assert fm, f"cannot parse fyers-apiv3 pin: {fyers_line!r}"
    fyers_version = fm.group(1)

    expected = FYERS_AIOHTTP_PIN.get(fyers_version)
    assert expected is not None, (
        f"Unknown fyers-apiv3 pin {fyers_version!r}: after bumping the SDK, "
        "check which aiohttp the new version requires and add it to "
        "FYERS_AIOHTTP_PIN in this file"
    )
    assert aio_version == expected, (
        f"requirements.txt pins aiohttp=={aio_version} but fyers-apiv3=="
        f"{fyers_version} requires aiohttp=={expected} — fresh installs "
        "would become ResolutionImpossible again"
    )


def test_fyers_requirements_files_keep_their_contract():
    assert REQUIREMENTS_FYERS.exists(), "requirements-fyers.txt is missing"
    assert REQUIREMENTS_FYERS_EXTRA.exists(), (
        "requirements-fyers-extra.txt is missing (the SDK needs "
        "aws_lambda_powertools at runtime but never declares it)"
    )
    assert _find_requirement(REQUIREMENTS_FYERS, "fyers-apiv3"), (
        "requirements-fyers.txt no longer pins fyers-apiv3"
    )
    text = REQUIREMENTS_FYERS.read_text(encoding="utf-8")
    assert "--no-deps" in text, (
        "requirements-fyers.txt must document the --no-deps install — "
        "without it pip forces the SDK's stale pins project-wide"
    )
    extra_text = REQUIREMENTS_FYERS_EXTRA.read_text(encoding="utf-8")
    assert "aws_lambda_powertools" in extra_text, (
        "requirements-fyers-extra.txt no longer declares aws_lambda_powertools"
    )


def test_setup_sh_performs_two_step_install():
    assert SETUP_SH.exists(), f"setup.sh not found at {SETUP_SH}"
    text = SETUP_SH.read_text(encoding="utf-8")
    assert "pip install -r requirements.txt" in text, (
        "setup.sh no longer installs core requirements"
    )
    assert "pip install --no-deps -r requirements-fyers.txt" in text, (
        "setup.sh no longer performs the --no-deps fyers SDK install — "
        "the documented setup flow would break or force stale pins"
    )
    assert "pip install -r requirements-fyers-extra.txt" in text, (
        "setup.sh no longer installs the SDK's undeclared runtime deps"
    )


def test_direct_aiohttp_consumer_keeps_declaration():
    news_engine = BACKEND_DIR / "news" / "news_engine.py"
    assert news_engine.exists(), "news/news_engine.py moved — update this test"
    source = news_engine.read_text(encoding="utf-8")
    assert re.search(r"^\s*(import aiohttp|from aiohttp)", source, re.MULTILINE), (
        "news_engine no longer imports aiohttp — this contract test can be "
        "updated to reflect the new direct consumer"
    )
    assert _find_requirement(REQUIREMENTS, "aiohttp"), (
        "aiohttp is imported directly by backend code but missing from "
        "requirements.txt"
    )
