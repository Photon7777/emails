"""Safe sender wrapper.

By default this runs the sender in dry-run mode. Live sending requires both:
1. passing --live
2. setting LIVE_SEND_CONFIRM=I_UNDERSTAND_SEND_LIVE_EMAILS in .env or the shell
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

from dotenv import load_dotenv

from main import main


CONFIRM_VALUE = "I_UNDERSTAND_SEND_LIVE_EMAILS"
load_dotenv(Path(__file__).resolve().parent / ".env")


if __name__ == "__main__":
    args = sys.argv[1:]
    forwarded_args = []
    for index, arg in enumerate(args):
        if arg == "--limit" and index + 1 < len(args):
            forwarded_args.extend(["--limit", args[index + 1]])
        elif arg.startswith("--limit="):
            forwarded_args.append(arg)

    if "--live" in args:
        if os.getenv("LIVE_SEND_CONFIRM", "") != CONFIRM_VALUE:
            print(
                "Refusing live send. Set LIVE_SEND_CONFIRM=I_UNDERSTAND_SEND_LIVE_EMAILS "
                "and rerun with --live when you are ready.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        raise SystemExit(main(["send", "--live", *forwarded_args]))

    raise SystemExit(main(["send", "--dry-run", *forwarded_args]))
