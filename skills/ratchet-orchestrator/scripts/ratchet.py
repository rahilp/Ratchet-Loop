#!/usr/bin/env python3
"""CLI/hook entry: thin launcher for ratchet_engine.main."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ratchet_engine import main

if __name__ == "__main__":
    raise SystemExit(main())