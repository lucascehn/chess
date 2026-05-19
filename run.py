#!/usr/bin/env python3
"""
run.py — Run everything in one shot
=====================================
Fetches your latest chess.com games, classifies losses, and regenerates
both quiz folders.  Run this after every session.

    python3 run.py
"""

import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def section(title):
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


# ------------------------------------------------------------------
# 1. Fetch latest games
# ------------------------------------------------------------------
section("1 / 3   Fetching games from chess.com")
import analyze_chess
analyze_chess.main()

# ------------------------------------------------------------------
# 2. Generate burn_quiz (A1 + A2 positions)
# ------------------------------------------------------------------
section("2 / 3   Building burn_quiz  (A1 + A2 positions)")
import make_quiz
make_quiz.main()

# ------------------------------------------------------------------
# 3. Generate blunder_quiz (A3 positions)
# ------------------------------------------------------------------
section("3 / 3   Building blunder_quiz  (A3 positions)")
import make_blunder_quiz
make_blunder_quiz.main()

# ------------------------------------------------------------------
# Done
# ------------------------------------------------------------------
print()
print("=" * 60)
print("  All done.")
print(f"  burn_quiz/    → positions where you were winning or equal")
print(f"  blunder_quiz/ → positions where things went wrong earlier")
print("=" * 60)
