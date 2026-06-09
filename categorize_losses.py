#!/usr/bin/env python3
"""
categorize_losses.py — Loss Root-Cause Classifier
==================================================

Classifies every loss by root cause using Stockfish to evaluate the position
at the moment you burned the most time. Combines clock data with engine eval
to tell you *why* you lost, not just that you lost.

Categories
----------
  A1  Sensing a Real Win      — big burn in the middlegame AND engine says you
                                were winning (+1.5 pawns or better). You had
                                the right instinct. Drill burn_quiz/ positions.

  A2  Complex / Double-Edged  — big burn in the middlegame AND roughly equal
                                position. Justified calculation, just need speed.
                                Also in burn_quiz/.

  A3  Sunk Cost Search        — big burn in the middlegame BUT you were already
                                losing (-1.5 or worse). Wasted time hunting magic.
                                See blunder_quiz/ to find where it actually went wrong.

  B   Slow Accumulation       — timed out with no single large burn. General
                                pacing problem across the whole game.

  C   Time + Board Loss       — in time trouble AND outplayed on the board.

  D   Outplayed               — lost on the board with plenty of time left.

Usage
-----
    python3 categorize_losses.py             # full analysis with Stockfish
    python3 categorize_losses.py --no-eval   # heuristics only, no engine

Setup (one-time):
    brew install stockfish
"""

import json
import glob
import os
import sys
import urllib.parse
import argparse
from datetime import datetime

from burn_finder import (
    find_burn, StockfishEvaluator, find_stockfish, classify_burn,
    BURN_THRESHOLD, WINNING_CP, LOSING_CP, MIDDLEGAME_START, MIDDLEGAME_END,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

USERNAME         = "lucasc3hn"
STOCKFISH_MS     = 300   # ms per position

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def latest_games_file() -> str:
    pattern = os.path.join(os.path.dirname(__file__), "raw_games_*.json")
    files   = sorted(glob.glob(pattern))
    if not files:
        print("No raw_games_*.json found. Run analyze_chess.py first.")
        sys.exit(1)
    return files[-1]


def load_losses(games_file: str) -> list[dict]:
    LOSS_RESULTS = {"checkmated", "timeout", "resigned",
                    "abandoned", "timevsinsufficient"}
    with open(games_file) as f:
        data = json.load(f)
    losses = []
    for g in data.get("games", []):
        w = g.get("white", {})
        b = g.get("black", {})
        if w.get("username", "").lower() == USERNAME.lower():
            our_color, our_result = "white", w.get("result", "")
            opp, opp_rating = b.get("username", "?"), b.get("rating", "?")
        else:
            our_color, our_result = "black", b.get("result", "")
            opp, opp_rating = w.get("username", "?"), w.get("rating", "?")
        if our_result in LOSS_RESULTS:
            losses.append({"our_color": our_color, "result": our_result,
                           "opponent": opp, "opp_rating": opp_rating,
                           "pgn": g.get("pgn", "")})
    return losses


# ---------------------------------------------------------------------------
# Classify one game
# ---------------------------------------------------------------------------

def _eval_label(cp, mate_in) -> str:
    if cp is None:          return "?"
    if mate_in is not None:
        side = "you" if mate_in > 0 else "them"
        return f"Mate in {abs(mate_in)} ({side})"
    sign = "+" if cp >= 0 else ""
    return f"{sign}{cp / 100:.2f}"


def classify_game(game: dict, evaluator: StockfishEvaluator) -> dict:
    our_color = game["our_color"]
    result    = game["result"]

    burn        = find_burn(game["pgn"], our_color)
    eval_result = evaluator.evaluate(burn.fen, our_color) if (burn and evaluator.available()) else None
    category, label = classify_burn(burn, eval_result, our_color, result)

    fen = burn.fen if burn else None
    return {
        "opponent":      game["opponent"],
        "opp_rating":    game["opp_rating"],
        "our_color":     our_color,
        "result":        result,
        "category":      category,
        "label":         label,
        "biggest_burn":  burn.burn_seconds if burn else 0,
        "burn_move":     burn.move_num     if burn else None,
        "clock_before":  burn.clock_before if burn else None,
        "clock_after":   burn.clock_after  if burn else None,
        "move_played":   burn.move_played  if burn else None,
        "fen":           fen,
        "eval_cp":       eval_result["cp"]      if eval_result else None,
        "eval_mate_in":  eval_result["mate_in"] if eval_result else None,
        "eval_depth":    eval_result["depth"]   if eval_result else None,
        "eval_label":    _eval_label(
            eval_result["cp"]      if eval_result else None,
            eval_result["mate_in"] if eval_result else None,
        ),
        "lichess": (
            "https://lichess.org/analysis/" + urllib.parse.quote(fen, safe="")
            if fen else None
        ),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

CATEGORY_META = {
    "A1": ("Sensing a Real Win",     "You were winning — right instinct, wrong speed. → burn_quiz/"),
    "A2": ("Complex / Double-Edged", "Roughly equal — justified calc, need to be faster. → burn_quiz/"),
    "A3": ("Sunk Cost Search",       "Already losing when you burned — find the real blunder. → blunder_quiz/"),
    "A?": ("Burn / No Eval",         "Big burn detected, no engine eval available."),
    "B":  ("Slow Accumulation",      "Timed out with no single big burn — general pacing issue."),
    "C":  ("Time + Board Loss",      "Time trouble AND outplayed — both contributed."),
    "D":  ("Outplayed",              "Lost on the board with time to spare — pure chess problem."),
}


def print_report(results: list[dict], has_eval: bool) -> None:
    total  = len(results)
    by_cat = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)

    print("\n" + "=" * 70)
    print(f"  LOSS ROOT-CAUSE BREAKDOWN — {USERNAME}")
    print(f"  Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if has_eval:
        print(f"  burn≥{BURN_THRESHOLD}s  |  winning≥+{WINNING_CP/100:.1f}  |  "
              f"losing≤{LOSING_CP/100:.1f}  |  stockfish {STOCKFISH_MS}ms/pos")
    else:
        print("  (no engine eval — run without --no-eval for A1/A2/A3 breakdown)")
    print("=" * 70)
    print(f"\n  Total losses: {total}\n")

    for cat in ["A1", "A2", "A3", "A?", "B", "C", "D"]:
        games = by_cat.get(cat, [])
        if not games:
            continue
        n, pct = len(games), round(len(games) / total * 100, 1)
        name, desc = CATEGORY_META[cat]
        print(f"  {cat:2}  {n:2d}/{total}  ({pct:4.1f}%)  {name}")
        print(f"            {desc}")
        for r in games:
            ev  = f" [{r['eval_label']}]" if r["eval_cp"] is not None or r["eval_mate_in"] is not None else ""
            bstr = f"burn={r['biggest_burn']}s on move {r['burn_move']}" if r["biggest_burn"] else "—"
            print(f"            • vs {r['opponent']} ({r['opp_rating']}) "
                  f"[{r['our_color']}] {bstr}{ev} → {r['result']}")
        print()

    drill  = len(by_cat.get("A1",[])) + len(by_cat.get("A2",[]))
    sunk   = len(by_cat.get("A3",[]))
    a_all  = drill + sunk + len(by_cat.get("A?",[]))
    print("-" * 70)
    print(f"  Burn-related (A*):                   {a_all}/{total}  ({round(a_all/total*100,1)}%)")
    if has_eval:
        print(f"  ► DRILL (A1+A2) → burn_quiz/:        {drill}/{total}")
        print(f"  ► BLUNDER FIND (A3) → blunder_quiz/: {sunk}/{total}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-eval", action="store_true")
    args = parser.parse_args()

    games_file = latest_games_file()
    print(f"Using: {os.path.basename(games_file)}")
    losses = load_losses(games_file)

    sf_path = None if args.no_eval else find_stockfish()
    if not args.no_eval:
        if sf_path:
            print(f"Stockfish: {sf_path}  ({STOCKFISH_MS}ms/pos)\n")
        else:
            print("Stockfish not found — install with: brew install stockfish\n")

    results = []
    with StockfishEvaluator(sf_path, STOCKFISH_MS) as sf:
        for i, game in enumerate(losses, 1):
            r = classify_game(game, sf)
            ev = f" [{r['eval_label']}]" if r["eval_cp"] is not None or r["eval_mate_in"] is not None else ""
            print(f"  [{i:02d}] {r['category']:2}  vs {game['opponent']}"
                  f"  burn={r['biggest_burn']}s{ev}")
            results.append(r)

    print_report(results, has_eval=(sf_path is not None))

    ts       = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(os.path.dirname(__file__), f"categorization_{ts}.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved → {os.path.basename(out_path)}")


if __name__ == "__main__":
    main()
