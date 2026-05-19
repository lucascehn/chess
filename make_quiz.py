#!/usr/bin/env python3
"""
make_quiz.py — Generate the Burn Position Drill Set
====================================================

Reads raw game data, evaluates every loss position with Stockfish, and produces
board images ONLY for A1 and A2 losses — the positions where you were winning or
in a genuinely complex position when you burned the clock.

A3 (sunk cost search) losses are skipped here. Run make_blunder_quiz.py instead
to find the actual blunder that made those positions lost.

Output
------
  burn_quiz/quiz_NN_Opponent.png    — board PNG, your pieces always at the bottom
  burn_quiz/quiz_answers.json       — move played, FEN, eval, lichess link

Usage
-----
    python3 make_quiz.py                 # download pieces + eval with Stockfish
    python3 make_quiz.py --no-download   # skip piece download (Unicode fallback)
    python3 make_quiz.py --no-eval       # skip Stockfish (keeps all burn positions)

Setup
-----
    pip3 install Pillow --break-system-packages
    brew install stockfish
"""

import json
import os
import re
import sys
import glob
import argparse
import urllib.parse
from datetime import datetime

from burn_finder import (
    find_all_burns, draw_board, download_pieces,
    StockfishEvaluator, find_stockfish, classify_burn,
)

USERNAME   = "handsomestbedbug"
QUIZ_DIR   = os.path.join(os.path.dirname(__file__), "burn_quiz")
PIECES_DIR = os.path.join(os.path.dirname(__file__), "pieces")

DRILL_CATEGORIES = {"A1", "A2"}   # only these get a board image
STOCKFISH_MS     = 300


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-download", action="store_true",
                        help="Skip piece image download (use Unicode fallback)")
    parser.add_argument("--no-eval", action="store_true",
                        help="Skip Stockfish eval (keep all burn positions)")
    args = parser.parse_args()

    games_file = latest_games_file()
    print(f"Using: {os.path.basename(games_file)}")
    losses = load_losses(games_file)
    print(f"Found {len(losses)} losses\n")

    # Download chess.com Neo piece images (cached after first run)
    if not args.no_download:
        print("Checking piece images …")
        n = download_pieces(PIECES_DIR)
        print(f"  {n}/12 piece images ready\n")
    pieces_dir = PIECES_DIR if not args.no_download else None

    # Start Stockfish if available
    sf_path = None if args.no_eval else find_stockfish()
    if not args.no_eval:
        if sf_path:
            print(f"Stockfish: {sf_path}  ({STOCKFISH_MS}ms/pos)")
        else:
            print("Stockfish not found (brew install stockfish). Keeping all burn positions.\n")

    os.makedirs(QUIZ_DIR, exist_ok=True)
    answers  = []
    skipped  = {"no_burn": 0, "a3_b_c_d": 0}
    drill_n  = 0

    with StockfishEvaluator(sf_path, STOCKFISH_MS) as sf:
        for game in losses:
            our_color = game["our_color"]
            burns = find_all_burns(game["pgn"], our_color)

            if not burns:
                skipped["no_burn"] += 1
                continue

            kept_any = False
            for rank, burn in enumerate(burns, 1):
                eval_result = sf.evaluate(burn.fen, our_color) if sf.available() else None
                category, label = classify_burn(burn, eval_result, our_color, game["result"])

                if category not in DRILL_CATEGORIES:
                    ev_str = f" [{eval_result['cp']/100:+.2f}]" if eval_result else ""
                    print(f"  skip  {category:2}  vs {game['opponent']} "
                          f"burn #{rank} move {burn.move_num}{ev_str}")
                    skipped["a3_b_c_d"] += 1
                    continue

                drill_n += 1
                kept_any = True
                ev_str = f" [{eval_result['cp']/100:+.2f}]" if eval_result else ""
                suffix = chr(ord('a') + rank - 1)   # a, b, c, …
                print(f"  [{drill_n:02d}]  {category}  vs {game['opponent']} "
                      f"({game['opp_rating']}) [{our_color}]"
                      f"  move {burn.move_num}  {burn.burn_seconds}s{ev_str}"
                      + (f"  (burn #{rank})" if rank > 1 else ""))

                flipped  = (our_color == "black")
                img      = draw_board(burn.board, flipped=flipped, size=480,
                                      pieces_dir=pieces_dir)
                safe_opp = re.sub(r"[^a-zA-Z0-9_-]", "_", game["opponent"])
                img_name = f"quiz_{drill_n:02d}_{safe_opp}.png"
                img.save(os.path.join(QUIZ_DIR, img_name))

                answers.append({
                    "num":          drill_n,
                    "category":     category,
                    "label":        label,
                    "opponent":     game["opponent"],
                    "opp_rating":   game["opp_rating"],
                    "our_color":    our_color,
                    "result":       game["result"],
                    "move_num":     burn.move_num,
                    "actual_move":  burn.move_played,
                    "burn_seconds": burn.burn_seconds,
                    "clock_before": burn.clock_before,
                    "clock_after":  burn.clock_after,
                    "eval_cp":      eval_result["cp"]      if eval_result else None,
                    "eval_mate_in": eval_result["mate_in"] if eval_result else None,
                    "fen":          burn.fen,
                    "lichess":      "https://lichess.org/analysis/" + urllib.parse.quote(burn.fen, safe=""),
                    "image":        img_name,
                })

    answers_path = os.path.join(QUIZ_DIR, "quiz_answers.json")
    with open(answers_path, "w") as f:
        json.dump(answers, f, indent=2)

    print()
    print(f"Done. {drill_n} drill positions saved to burn_quiz/")
    print(f"Skipped: {skipped['a3_b_c_d']} non-drill (A3/B/C/D), "
          f"{skipped['no_burn']} with no clock data")
    print(f"Answers: {answers_path}")
    print()
    print("Open burn_quiz/ in Finder. For each PNG, find the move before")
    print("checking quiz_answers.json or the lichess link.")


if __name__ == "__main__":
    main()
