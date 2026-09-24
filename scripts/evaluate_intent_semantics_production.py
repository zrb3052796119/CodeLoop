"""Source-checkout wrapper for privacy-safe production intent replay."""

from minicode.intent_semantic_production_eval import build_parser, main

__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
