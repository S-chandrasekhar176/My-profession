"""Regression guards for the dependency-resolution contract (v0.4.11.1).

A fresh `pip install -r requirements.txt` used to be ResolutionImpossible:
fyers-apiv3 (every published version) hard-pins aiohttp==3.8.x/3.9.x while
requirements.txt demanded aiohttp>=3.10.0 AND listed fyers-apiv3 itself —
no version combination could ever satisfy both. Anyone setting up a fresh
machine, CI, or clone hit the wall before a single line of code ran.

The contract guarded here:
  1. requirements.txt NEVER lists fyers-apiv3 — the SDK is installed
     separately with --no-deps (requirements-fyers.txt; setup.sh step 3).
  2. The aiohttp pin in requirements.txt stays EXACT (==) at the version
     the backend suite is verified against. v0.4.18 note: aca19e0
     deliberately raised it to 3.10.11, DIVERGING from the SDK's own
     aiohttp==3.9.3 pin — this is safe precisely because of contract #1
     (the SDK is never co-resolved; it is installed --no-deps and the
     suite runs green on 3.10.11 with the SDK present).
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
# Documentation map (kept accurate for SDK bumps); since the SDK is always
# installed --no-deps this no longer constrains the requirements.txt pin.
FYERS_AIOHTTP_PIN = {
    "3.1.16": "3.9.3",
}

# The aiohttp version the backend suite is verified green against.
# aca19e0 raised the shipped pin 3.9.3 -> 3.10.11 (full suite green incl.
# the Fyers SDK running alongside via --no-deps); v0.4.18 encodes it here
# so future bumps must be conscious (update this constant together).
VERIFIED_AIOHTTP = "3.10.11"


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


def test_aiohttp_pin_is_exact_and_matches_verified_version():
    """v0.4.18: re-scoped after aca19e0 deliberately raised the aiohttp pin
    to 3.10.11 (diverging from fyers-apiv3's own aiohttp==3.9.3 pin). The
    divergence is safe because contract #1 keeps the SDK out of the main
    requirements set (installed --no-deps by setup.sh), so pip never
    co-resolves the two files. What must still hold:
      * aiohttp stays EXACT-pinned (==) and declared,
      * the pin equals the version the suite is verified against
        (VERIFIED_AIOHTTP) so an unreviewed bump cannot slip through,
      * the SDK pin in requirements-fyers.txt remains one we know the
        aiohttp requirement for (FYERS_AIOHTTP_PIN documentation map)."""
    aio = _find_requirement(REQUIREMENTS, "aiohttp")
    assert aio is not None, (
        "aiohttp vanished from requirements.txt — news/news_engine.py imports "
        "it directly, so it must stay declared"
    )
    m = re.search(r"==\s*([0-9][0-9a-zA-Z.]*)", aio)
    assert m, (
        f"aiohttp must be EXACT-pinned (==) to the version the backend suite "
        f"is verified against; got: {aio!r}"
    )
    aio_version = m.group(1)
    assert aio_version == VERIFIED_AIOHTTP, (
        f"requirements.txt pins aiohttp=={aio_version} but the verified pin "
        f"is {VERIFIED_AIOHTTP!r} — if this bump is intentional, update "
        "VERIFIED_AIOHTTP in tests/test_requirements_consistency.py together "
        "with the pin (and re-run the full suite)."
    )

    fyers_line = _find_requirement(REQUIREMENTS_FYERS, "fyers-apiv3")
    assert fyers_line, "requirements-fyers.txt no longer pins fyers-apiv3"
    fm = re.search(r"fyers[-_]apiv3==([0-9][0-9a-zA-Z.]*)", fyers_line, re.IGNORECASE)
    assert fm, f"cannot parse fyers-apiv3 pin: {fyers_line!r}"
    fyers_version = fm.group(1)
    assert fyers_version in FYERS_AIOHTTP_PIN, (
        f"Unknown fyers-apiv3 pin {fyers_version!r}: after bumping the SDK, "
        "check which aiohttp the new version requires and add it to "
        "FYERS_AIOHTTP_PIN in this file"
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
