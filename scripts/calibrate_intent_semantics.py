"""Source-checkout wrapper for the installed calibration command."""

from minicode.intent_semantic_calibration_cli import build_parser, main

__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
