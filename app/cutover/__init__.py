"""Cutover module: vendored live-tested PROD/DR switchover engine + console integration.

The files under vendor/ are byte-identical copies of the UK toolkit that was
validated on the live UK PROD/DR clusters (see CHECKSUMS.sha256). They must
never be edited here: all console adaptation lives in wrapper.py / runner.py.
verify_vendor() enforces this at startup and before every run workspace build.
"""
from __future__ import annotations

import hashlib
import pathlib

VENDOR_DIR = pathlib.Path(__file__).resolve().parent / "vendor"
CHECKSUMS_FILE = VENDOR_DIR / "CHECKSUMS.sha256"

ENGINE_FILE = "prod_dr_cutover.py"
ORCHESTRATOR_FILE = "prod_dr_cutover_uk_orchestrate.py"
# The orchestrator hardcodes this wrapper filename next to itself.
WRAPPER_FILE = "prod_dr_cutover_uk.sh"


class VendorIntegrityError(RuntimeError):
    pass


def expected_checksums() -> dict[str, str]:
    sums: dict[str, str] = {}
    for line in CHECKSUMS_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, name = line.partition("  ")
        sums[name.strip()] = digest.strip()
    return sums


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_vendor(directory: pathlib.Path | None = None) -> dict[str, str]:
    """Verify vendored engine files match the recorded live-tested checksums.

    Returns {filename: sha256}. Raises VendorIntegrityError on any mismatch.
    When `directory` is given (a run workspace), the copies there are verified
    instead of the package vendor dir.
    """
    base = directory or VENDOR_DIR
    expected = expected_checksums()
    if not expected:
        raise VendorIntegrityError(f"no checksums recorded in {CHECKSUMS_FILE}")
    actual: dict[str, str] = {}
    for name, digest in expected.items():
        path = base / name
        if not path.is_file():
            raise VendorIntegrityError(f"vendored file missing: {path}")
        actual[name] = sha256_file(path)
        if actual[name] != digest:
            raise VendorIntegrityError(
                f"vendored file drifted from live-tested version: {path} "
                f"(expected {digest}, got {actual[name]})"
            )
    return actual
