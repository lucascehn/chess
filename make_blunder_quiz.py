#!/usr/bin/env python3
"""
make_blunder_quiz.py — Generate the Blunder Drill Set
======================================================

Handles A3 losses ("Sunk Cost Search") — games where you were already badly
losing when you burned the clock.  Instead of showing you the burn position
(which was lost anyway), this script hunts backwards to find the earlier move
that actually dropped the position from equal/winning into losing territory.

That's the real position to drill: the moment you fell into a trap or made a
positional mistake you didn't recover from.

Output
------
  blunder_quiz/blunder_NN_Opponent.png  — board image before the blunder move
  blunder_quiz/blunder_answers.json     — blunder move, eval drop, FEN, lichess link

Usage
-----
    python3 make_blunder_quiz.py
    python3 make_blunder_quiz.py --no-download   # Unicode piece fallback

Setup
-----
    brew install stockfish
    pip3 install Pillow --break-system-packages
"""

import json
import os
import re
import glob
import argparse
import urllib.parse

from burn_finder import (
    find_burn, draw_board, download_pieces,
    StockfishEvaluator, find_stockfish, classify_burn, EvalCache,
)
from blunder_finder import find_all_blunders

USERNAME     = "handsomestbedbug"
BLUNDER_DIR  = os.path.join(os.path.dirname(__file__), "blunder_quiz")
PIECES_DIR   = os.path.join(os.path.dirname(__file__), "pieces")
STOCKFISH_MS = 100   # lower per-position time — we evaluate many positions per game


def latest_games_file() -> str:
    pattern = os.path.join(os.path.dirname(__file__), "raw_games_*.json")
    files   = sorted(glob.glob(pattern))
    if not files:
        print("No raw_games_*.json found. Run analyze_chess.py first.")
        import sys; sys.exit(1)
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
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args()

    games_file = latest_games_file()
    print(f"Using: {os.path.basename(games_file)}")
    losses = load_losses(games_file)

    if not args.no_download:
        print("Checking piece images …")
        n = download_pieces(PIECES_DIR)
        print(f"  {n}/12 piece images ready\n")
    pieces_dir = PIECES_DIR if not args.no_download else None

    sf_path = find_stockfish()
    if not sf_path:
        print("Stockfish not found. Install with: brew install stockfish")
        return
    print(f"Stockfish: {sf_path}  ({STOCKFISH_MS}ms/pos, evaluating multiple positions/game)\n")

    os.makedirs(BLUNDER_DIR, exist_ok=True)
    answers = []
    n_out   = 0

    shared_cache = EvalCache()
    with StockfishEvaluator(sf_path, movetime_ms=300, cache=shared_cache) as classify_sf, \
         StockfishEvaluator(sf_path, movetime_ms=STOCKFISH_MS, cache=shared_cache) as blunder_sf:

        for game in losses:
            our_color = game["our_color"]
            burn = find_burn(game["pgn"], our_color)
            if burn is None:
                continue

            # Only process A3 losses
            ev = classify_sf.evaluate(burn.fen, our_color)
            category, _ = classify_burn(burn, ev, our_color, game["result"])
            if category != "A3":
                continue

            ev_str = f"[{ev['cp']/100:+.2f}]" if ev else "(?)"
            print(f"  A3  vs {game['opponent']} ({game['opp_rating']}) "
                  f"[{our_color}]  burn pos {ev_str} — searching for blunder …")

            blunders = find_all_blunders(game["pgn"], our_color, blunder_sf)
            if not blunders:
                print(f"       ✗ no blunder isolated (may have been lost from the opening)")
                continue

            for rank, blunder in enumerate(blunders, 1):
                n_out += 1
                drop_str = (f"{blunder.eval_before/100:+.2f} → "
                            f"{blunder.eval_after/100:+.2f}  "
                            f"(Δ{blunder.eval_drop/100:+.2f})")
                print(f"       ✓ blunder #{rank} on move {blunder.move_num}: "
                      f"{blunder.blunder_move}  {drop_str}")

                flipped  = (our_color == "black")
                img      = draw_board(blunder.board, flipped=flipped, size=480,
                                      pieces_dir=pieces_dir)
                safe_opp = re.sub(r"[^a-zA-Z0-9_-]", "_", game["opponent"])
                img_name = f"blunder_{n_out:02d}_{safe_opp}.png"
                img.save(os.path.join(BLUNDER_DIR, img_name))

                answers.append({
                    "num":           n_out,
                    "opponent":      game["opponent"],
                    "opp_rating":    game["opp_rating"],
                    "our_color":     our_color,
                    "result":        game["result"],
                    "move_num":      blunder.move_num,
                    "blunder_move":  blunder.blunder_move,
                    "eval_before":   blunder.eval_before,
                    "eval_after":    blunder.eval_after,
                    "eval_drop":     blunder.eval_drop,
                    "burn_move_num": burn.move_num,
                    "burn_seconds":  burn.burn_seconds,
                    "fen":           blunder.fen,
                    "lichess":       "https://lichess.org/analysis/" + urllib.parse.quote(blunder.fen, safe=""),
                    "image":         img_name,
                })

    answers_path = os.path.join(BLUNDER_DIR, "blunder_answers.json")
    with open(answers_path, "w") as f:
        json.dump(answers, f, indent=2)

    print()
    print(f"Done. {n_out} blunder positions saved to blunder_quiz/")
    print(f"Answers: {answers_path}")
    print()
    print("For each image: find the correct move BEFORE the blunder.")
    print("The blunder_answers.json shows what you played and the eval drop.")


if __name__ == "__main__":
    main()
