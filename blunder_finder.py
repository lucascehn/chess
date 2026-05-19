#!/usr/bin/env python3
"""
blunder_finder.py — Find the Moves That Lost the Game
======================================================

For A3 losses ("Sunk Cost Search") the position was already badly lost when
you burned the clock.  Somewhere earlier in the game you played one (or more)
moves that dropped a winning or equal position into a losing one.

This module finds those moves — statistically.  Rather than a fixed centipawn
threshold (which would flag every minor inaccuracy in complex positions), it
evaluates the game's own variance and only returns moves whose eval drop is
an outlier relative to the game's typical position-to-position fluctuation.

Algorithm
---------
1.  Replay the game, evaluating every position after OUR moves (shared between
    consecutive eval calls — eval_after[N] == eval_before[N+1], so we evaluate
    each position once, not twice).
2.  Compute all per-move eval drops for our colour.
3.  Find drops that are:
    a. Statistical outliers (> mean + 1.5 × std_dev of all drops in the game)
    b. Above an absolute floor (abs_floor_cp) to ignore normal fluctuation
    c. Dropped FROM a survivable position (≥ SURVIVABLE_CP) — positions that
       were already lost don't count as "blunders"
4.  Return the board state BEFORE each such blunder move.

Public API
----------
    from blunder_finder import find_all_blunders, BlunderResult

    blunders = find_all_blunders(pgn, our_color, evaluator)
    for b in blunders:
        img = draw_board(b.board, flipped=(our_color == "black"))
        img.save(f"blunder_move{b.move_num}.png")
        print(f"Move {b.move_num}: {b.blunder_move}  "
              f"({b.eval_before/100:+.2f} → {b.eval_after/100:+.2f})")

Dependencies
------------
    Stockfish (brew install stockfish)  — required
    Pillow    (pip3 install Pillow)     — only needed for draw_board()
"""

import re
import copy
import statistics
from collections import namedtuple

from burn_finder import (
    _init_board, _apply_move, board_to_fen,
    W, BL,
    StockfishEvaluator,
)

# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------

BlunderResult = namedtuple(
    "BlunderResult",
    ["board", "fen", "blunder_move", "move_num",
     "eval_before", "eval_after", "eval_drop"]
)
"""
Fields
------
board        : 8×8 board BEFORE the blunder move — the position to drill
fen          : FEN string of that position
blunder_move : SAN of the move that caused the drop
move_num     : full-game move number (1-based)
eval_before  : centipawns (your side) before the blunder — was survivable
eval_after   : centipawns (your side) after the blunder — now clearly losing
eval_drop    : eval_after − eval_before (negative = you dropped)
"""

# A position eval ≥ this is considered "survivable" (worth blundering from)
SURVIVABLE_CP = -100

# Minimum absolute floor for an eval drop to be considered at all
ABS_FLOOR_CP = 80


# ---------------------------------------------------------------------------
# PGN move extractor (internal)
# ---------------------------------------------------------------------------

def _extract_moves(pgn: str) -> list[tuple[int, str, str]]:
    """Return [(move_number, color_str, san), …] from a PGN."""
    moves_raw = " ".join(l for l in pgn.split("\n")
                         if not l.startswith("[") and l.strip())
    moves_raw = re.sub(r"\{[^}]*\}", "", moves_raw)
    moves_raw = re.sub(r"\$\d+", "", moves_raw)

    tokens, result, move_num, color = moves_raw.split(), [], 1, "white"
    for tok in tokens:
        if re.match(r"^\d+\.$", tok):
            move_num, color = int(tok[:-1]), "white"; continue
        if re.match(r"^\d+\.\.\.$", tok):
            color = "black"; continue
        if tok in ("1-0", "0-1", "1/2-1/2", "*"):
            break
        if not re.match(r"^[NBRQK]?[a-h]?[1-8]?x?[a-h][1-8]|O-O|0-0", tok):
            continue
        result.append((move_num, color, tok))
        color = "black" if color == "white" else "white"
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_all_blunders(pgn: str, our_color: str,
                      evaluator: StockfishEvaluator) -> list[BlunderResult]:
    """
    Return ALL statistically significant blunder moves for ``our_color`` in
    ``pgn``, sorted by eval drop severity (worst first).

    Only positions that were survivable (eval ≥ SURVIVABLE_CP) before the move
    are candidates — we're looking for the moment things fell apart, not
    confirming that a position was already hopeless.

    Returns an empty list if Stockfish is unavailable or no blunders are found.
    """
    if not evaluator.available():
        return []

    moves = _extract_moves(pgn)
    if not moves:
        return []

    # --- Replay and evaluate every position after OUR moves ---
    # We store (eval_cp, board_before, fen_before, san, move_num) per our move.
    board    = _init_board()
    our_evals = []   # list of dicts, one per OUR move

    for move_num, color_str, san in moves:
        c = W if color_str == "white" else BL

        if color_str == our_color:
            board_snap = copy.deepcopy(board)
            fen_before = board_to_fen(board, "w" if color_str == "white" else "b")
            ev = evaluator.evaluate(fen_before, our_color)
            cp = ev["cp"] if ev else None
            our_evals.append({
                "cp":           cp,
                "board_before": board_snap,
                "fen_before":   fen_before,
                "san":          san,
                "move_num":     move_num,
            })

        board = _apply_move(board, c, san)

    if len(our_evals) < 2:
        return []

    # --- Compute per-move eval drops (drop[i] = eval[i+1] - eval[i]) ---
    drops = []
    for i in range(len(our_evals) - 1):
        cp_before = our_evals[i]["cp"]
        cp_after  = our_evals[i + 1]["cp"]
        if cp_before is not None and cp_after is not None:
            drops.append((cp_after - cp_before, i))   # negative = we dropped

    if not drops:
        return []

    # --- Statistical outlier threshold, game-relative ---
    raw_drops    = [abs(d) for d, _ in drops if d < 0]   # magnitudes of negative drops only
    if not raw_drops:
        return []

    mean_drop = statistics.mean(raw_drops)
    std_drop  = statistics.pstdev(raw_drops)
    threshold = max(mean_drop + 1.5 * std_drop, ABS_FLOOR_CP)

    # --- Collect blunders that meet the bar ---
    blunders = []
    for drop, i in drops:
        if drop >= 0:
            continue    # eval improved or stayed same — not a blunder
        if abs(drop) < threshold:
            continue    # not an outlier
        cp_before = our_evals[i]["cp"]
        cp_after  = our_evals[i + 1]["cp"]
        if cp_before < SURVIVABLE_CP:
            continue    # position was already lost before this move
        blunders.append(BlunderResult(
            board        = our_evals[i]["board_before"],
            fen          = our_evals[i]["fen_before"],
            blunder_move = our_evals[i + 1]["san"],  # the move that caused the drop
            move_num     = our_evals[i + 1]["move_num"],
            eval_before  = cp_before,
            eval_after   = cp_after,
            eval_drop    = drop,
        ))

    return sorted(blunders, key=lambda b: b.eval_drop)   # worst first


def find_blunder(pgn: str, our_color: str,
                 evaluator: StockfishEvaluator) -> "BlunderResult | None":
    """Convenience wrapper: return only the single worst blunder, or None."""
    blunders = find_all_blunders(pgn, our_color, evaluator)
    return blunders[0] if blunders else None
