"""OpenShift CronJob entry point for one idempotent collection pass."""
from __future__ import annotations

import json
import sys

from .service import scheduled_collect_all


def main() -> int:
    result = scheduled_collect_all()
    # Results contain IDs and sanitized status only; credentials are never serialized.
    print(json.dumps(result, default=str, separators=(",", ":")))
    return 0 if result.get("status") in {"SUCCEEDED", "PARTIAL"} else 1


if __name__ == "__main__":
    sys.exit(main())
