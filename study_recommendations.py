#!/usr/bin/env python3
"""
study_recommendations.py — Personalized Chess Study Plan
=========================================================

Reads your latest loss categorization and produces a prioritized study plan
tailored to your specific weaknesses.

Run after categorize_losses.py (or just use run.py which calls everything):
    python3 study_recommendations.py
"""

import json
import glob
import os
import sys
import re
from datetime import datetime
from collections import Counter

USERNAME = "lucasc3hn"


# ── Helpers ────────────────────────────────────────────────────────────────


def latest_file(pattern: str) -> str | None:
    files = sorted(glob.glob(os.path.join(os.path.dirname(__file__), pattern)))
    return files[-1] if files else None


def pct(n: int, total: int) -> float:
    return round(n / total * 100, 1) if total else 0.0


def bar(n: int, total: int, width: int = 20) -> str:
    filled = round(n / total * width) if total else 0
    return "█" * filled + "░" * (width - filled)


# ── Data loading ───────────────────────────────────────────────────────────


def load_categorization() -> tuple[list[dict], str]:
    path = latest_file("categorization_*.json")
    if not path:
        print("No categorization_*.json found. Run categorize_losses.py first.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f), os.path.basename(path)


def extract_openings_from_raw(results: list[dict]) -> list[str]:
    """Pull opening names from the PGN ECOUrl tag (chess.com format).

    Falls back to the Opening tag or ECO code when ECOUrl is absent.
    Returns one entry per categorized loss (parallel to results list).
    """
    openings: list[str] = []
    path = latest_file("raw_games_*.json")
    if not path:
        return openings

    with open(path) as f:
        raw = json.load(f)

    LOSS_RESULTS = {"checkmated", "timeout", "resigned", "abandoned", "timevsinsufficient"}

    losses_pgn: list[str] = []
    for g in raw.get("games", []):
        w = g.get("white", {})
        b = g.get("black", {})
        if w.get("username", "").lower() == USERNAME.lower():
            our_result = w.get("result", "")
        else:
            our_result = b.get("result", "")
        if our_result in LOSS_RESULTS:
            losses_pgn.append(g.get("pgn", ""))

    for pgn in losses_pgn:
        # ECOUrl last path segment  e.g. "Sicilian-Defense-Closed-Variation-3.Nc3"
        eco_url = re.search(r'\[ECOUrl "https://www\.chess\.com/openings/([^"]+)"\]', pgn)
        if eco_url:
            raw_name = eco_url.group(1)
            # keep only the first 4 dash-separated tokens (opening family + variation)
            parts = raw_name.split("-")
            name = " ".join(p for p in parts[:5] if not re.match(r'^\d', p))
            openings.append(name.replace("-", " "))
            continue

        opening_tag = re.search(r'\[Opening "([^"]+)"\]', pgn)
        if opening_tag:
            openings.append(opening_tag.group(1))
            continue

        eco_tag = re.search(r'\[ECO "([^"]+)"\]', pgn)
        openings.append(eco_tag.group(1) if eco_tag else "Unknown")

    return openings


# ── Analysis ───────────────────────────────────────────────────────────────


def analyse(results: list[dict], openings: list[str]) -> dict:
    total = len(results)
    by_cat: dict[str, list[dict]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)

    a1 = by_cat.get("A1", [])
    a2 = by_cat.get("A2", [])
    a3 = by_cat.get("A3", [])
    b  = by_cat.get("B",  [])
    c  = by_cat.get("C",  [])
    d  = by_cat.get("D",  [])

    burn_n   = len(a1) + len(a2)
    sunk_n   = len(a3)
    pacing_n = len(b)
    tb_n     = len(c)
    board_n  = len(d)

    # weighted score for prioritisation; C contributes to all three areas
    scores = {
        "burn":   pct(burn_n,   total) + pct(tb_n, total) * 0.3,
        "sunk":   pct(sunk_n,   total),
        "pacing": pct(pacing_n, total) + pct(tb_n, total) * 0.3,
        "board":  pct(board_n,  total) + pct(tb_n, total) * 0.4,
    }
    priorities = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    burn_times = [r["biggest_burn"] for r in a1 + a2 + a3 if r.get("biggest_burn")]
    avg_burn = round(sum(burn_times) / len(burn_times)) if burn_times else 0

    burn_moves = [r["burn_move"] for r in a1 + a2 + a3 if r.get("burn_move")]
    avg_burn_move = round(sum(burn_moves) / len(burn_moves)) if burn_moves else 0

    opening_counts: Counter = Counter(openings)
    problem_openings = opening_counts.most_common(5)

    return {
        "total": total,
        "by_cat": by_cat,
        "a1": a1, "a2": a2, "a3": a3, "b": b, "c": c, "d": d,
        "burn_pct":   pct(burn_n,   total),
        "sunk_pct":   pct(sunk_n,   total),
        "pacing_pct": pct(pacing_n, total),
        "tb_pct":     pct(tb_n,     total),
        "board_pct":  pct(board_n,  total),
        "priorities": priorities,
        "avg_burn": avg_burn,
        "avg_burn_move": avg_burn_move,
        "problem_openings": problem_openings,
    }


# ── Printing ───────────────────────────────────────────────────────────────


def print_recommendations(p: dict, cat_file: str) -> None:
    total = p["total"]

    print()
    print("=" * 70)
    print(f"  STUDY RECOMMENDATIONS — {USERNAME}")
    print(f"  Based on: {cat_file}  ({total} losses)")
    print(f"  Run at:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # ── Loss snapshot ──────────────────────────────────────────────────────
    print(f"\n  LOSS BREAKDOWN\n")
    rows = [
        ("A1+A2  Burn (right instinct / complex)", p["burn_pct"],   len(p["a1"]) + len(p["a2"])),
        ("A3     Sunk cost (already losing)",        p["sunk_pct"],  len(p["a3"])),
        ("B      Slow accumulation (no big burn)",   p["pacing_pct"],len(p["b"])),
        ("C      Time + board (both contributed)",   p["tb_pct"],    len(p["c"])),
        ("D      Outplayed (pure chess mistake)",    p["board_pct"], len(p["d"])),
    ]
    for label, pct_val, n in rows:
        print(f"  {label:<44} {bar(n, total)}  {pct_val:5.1f}%")

    if p["avg_burn"]:
        print(f"\n  Average burn: {p['avg_burn']}s  •  Average move it occurs: move {p['avg_burn_move']}")

    # ── Prioritised study plan ─────────────────────────────────────────────
    print(f"\n{'─' * 70}")
    print(f"  PRIORITISED STUDY PLAN")
    print(f"{'─' * 70}")

    rank = 1
    for area, score in p["priorities"]:
        if score < 5:
            continue

        print()
        if area == "burn":
            print(f"  #{rank}  PATTERN SPEED  ({p['burn_pct']:.0f}% of losses)")
            print(f"      You sense the right ideas but take too long to confirm them.")
            if p["avg_burn"]:
                print(f"      Your average burn is {p['avg_burn']}s.  Target: confident in under 10s.")
            print()
            print(f"      Primary drill  →  burn_quiz/  (generated by make_quiz.py)")
            print(f"        These are the exact positions where your clock died.")
            print(f"        Set a 10-second timer per position.  No peeking at answers.")
            print()
            a1_links = [r["lichess"] for r in p["a1"] if r.get("lichess")][:4]
            a2_links = [r["lichess"] for r in p["a2"] if r.get("lichess")][:3]
            if a1_links:
                print(f"      Your A1 positions (you were winning — find the move):")
                for link in a1_links:
                    print(f"        {link}")
                print()
            if a2_links:
                print(f"      Your A2 positions (complex — just needed more speed):")
                for link in a2_links:
                    print(f"        {link}")
                print()
            print(f"      Supplement  →  Puzzle Rush / Lichess Puzzle Storm")
            print(f"        3 x 5-min sessions per week builds reflexes, not calculation.")

        elif area == "sunk":
            print(f"  #{rank}  POSITION EVALUATION  ({p['sunk_pct']:.0f}% of losses)")
            print(f"      You're burning time in lost positions hunting for magic that isn't there.")
            print(f"      The position was already gone — the real mistake happened earlier.")
            print()
            print(f"      Primary drill  →  blunder_quiz/  (generated by make_blunder_quiz.py)")
            print(f"        Find the actual move that cost you the game, not the burn move.")
            print()
            a3_links = [r["lichess"] for r in p["a3"] if r.get("lichess")][:4]
            if a3_links:
                print(f"      Your A3 burn positions (you were already losing at this point):")
                for link in a3_links:
                    print(f"        {link}")
                print()
            print(f"      Habit to build  →  Before calculating, ask: 'Am I winning or losing?'")
            print(f"        If losing by more than a pawn, simplify — don't complicate.")
            print(f"        Save your clock for positions where complications help you.")

        elif area == "pacing":
            print(f"  #{rank}  TIME MANAGEMENT  ({p['pacing_pct']:.0f}% of losses)")
            print(f"      No single burn — you're spending time evenly across the whole game")
            print(f"      and running out before anything critical even happens.")
            print()
            print(f"      Rule to adopt  →  Never spend more than 15s on a move unless")
            print(f"        you're inside a concrete forcing sequence (checks, captures).")
            print()
            print(f"      Opening fix  →  Learn your opening to move 10+ from memory.")
            print(f"        Free clock time early = more budget for the middlegame crisis.")
            print()
            print(f"      Practice  →  Play 3+0 blitz to force faster habits.")
            print(f"        Then apply the cadence back to your normal 5+0 games.")

        elif area == "board":
            print(f"  #{rank}  CHESS FUNDAMENTALS  ({p['board_pct']:.0f}% of losses)")
            print(f"      You're losing with time left — the clock isn't the issue here.")
            print()
            print(f"      Diagnose  →  Review your D losses and find the pattern:")
            d_samples = [(r["opponent"], r["eval_label"], r.get("lichess"))
                         for r in p["d"] if r.get("eval_label", "?") != "?"][:5]
            for opp, ev, link in d_samples:
                link_str = f"  {link}" if link else ""
                print(f"        vs {opp}: eval at burn moment = {ev}{link_str}")
            if not d_samples:
                print(f"        (run categorize_losses.py with Stockfish to get position evals)")
            print()
            print(f"      Endgame study  →  Silman's Complete Endgame Course")
            print(f"        or Lichess endgame practice.  Most blitz games reach K+P endings.")
            print()
            print(f"      Positional study  →  Identify the type of position you keep losing in.")
            print(f"        Open files? Opposite castling? Pawn structure?  Study that theme.")

        rank += 1

    # ── Opening patterns ───────────────────────────────────────────────────
    if p["problem_openings"]:
        print()
        print(f"{'─' * 70}")
        print(f"  PROBLEM OPENINGS  (most common in your losses)\n")
        for opening, count in p["problem_openings"]:
            print(f"  {bar(count, total, 12)}  {count:3d}x  {pct(count, total):4.1f}%  {opening}")
        print()
        top = p["problem_openings"][0][0]
        print(f"  → '{top}' comes up most often.")
        print(f"    Either study it in depth, or switch to a simpler variation")
        print(f"    until your time management improves in that structure.")

    # ── Bottom line ────────────────────────────────────────────────────────
    top_area, _ = p["priorities"][0]
    area_labels = {
        "burn":   "pattern recognition speed — you sense the right moves but calculate too slowly",
        "sunk":   "position evaluation — you hunt for magic in positions that are already lost",
        "pacing": "time management — you're spending the clock before the crisis even arrives",
        "board":  "chess fundamentals — time isn't the problem, the moves are",
    }
    print()
    print(f"{'─' * 70}")
    print(f"  BOTTOM LINE\n")
    print(f"  Your #1 problem: {area_labels[top_area]}.")
    print()
    if top_area in ("burn", "sunk"):
        print(f"  The drill sets (burn_quiz/ and blunder_quiz/) target this directly.")
        print(f"  Run them regularly — a few positions before each session is enough.")
    elif top_area == "pacing":
        print(f"  No drill set fixes pacing — you need to change your move-making habits.")
        print(f"  Set a clock discipline rule and enforce it for 10 sessions straight.")
    else:
        print(f"  Play fewer games and study more.  An extra rating class of endgame")
        print(f"  knowledge is worth 100 games of grinding.")
    print()
    print("=" * 70)


# ── Main ───────────────────────────────────────────────────────────────────


def main() -> None:
    results, cat_file = load_categorization()
    openings = extract_openings_from_raw(results)
    plan = analyse(results, openings)
    print_recommendations(plan, cat_file)


if __name__ == "__main__":
    main()
