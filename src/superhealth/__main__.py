"""SuperHealth CLI entry point.

Usage:
    python -m superhealth [command]

Commands:
    pipeline   Run the daily pipeline (default)
    goals      Manage health goals
    dashboard  Start the web dashboard
"""
from __future__ import annotations

import sys


def main():
    from superhealth.log_config import setup_logging

    setup_logging()

    if len(sys.argv) < 2:
        _print_help()
        sys.exit(0)

    cmd = sys.argv[1]
    rest = sys.argv[2:]

    if cmd == "pipeline":
        from superhealth.daily_pipeline import main as pipeline_main

        sys.argv = [sys.argv[0]] + rest
        pipeline_main()
    elif cmd == "goals":
        from superhealth.goals.cli import main as goals_main

        sys.argv = [sys.argv[0]] + rest
        goals_main()
    elif cmd == "dashboard":
        import subprocess

        subprocess.run(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                "src/superhealth/dashboard/app.py",
                *rest,
            ]
        )
    else:
        print(f"Unknown command: {cmd}")
        _print_help()
        sys.exit(1)


def _print_help():
    print(__doc__)


if __name__ == "__main__":
    main()
