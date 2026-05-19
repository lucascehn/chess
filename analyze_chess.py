#!/usr/bin/env python3
"""
Chess.com Game Analyzer for handsomestbedbug
Fetches stats and recent games, then analyzes losses.
"""

import urllib.request
import json
import re
import os
import sys
from datetime import datetime

USERNAME = "handsomestbedbug"
STATS_URL = f"https://api.chess.com/pub/player/{USERNAME}/stats"

def get_current_month_url():
    now = datetime.now()
    return f"https://api.chess.com/pub/player/{USERNAME}/games/{now.year}/{now.month:02d}"

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "chess-analyzer/1.0"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def parse_clock(clock_str):
    """Convert %clk h:mm:ss(.d) to total seconds."""
    if not clock_str:
        return None
    s = clock_str.split('.')[0]  # drop sub-second decimal
    m = re.match(r'(\d+):(\d+):(\d+)', s)
    if m:
        h, mn, sec = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return h * 3600 + mn * 60 + sec
    return None

def analyze_game(pgn, our_color):
    """Return a dict of flags/patterns for a single game."""
    lines = pgn.strip().split('\n')

    # Extract moves block
    move_text = ' '.join(l for l in lines if not l.startswith('['))

    # Extract all clock times for our color
    # chess.com format: {[%clk 0:02:59.9]}  (no spaces, optional decimal)
    clocks = re.findall(r'\{\[%clk (\d+:\d+:\d+(?:\.\d+)?)\]\}', move_text)

    # Our clocks are every other entry depending on color
    if our_color == 'white':
        our_clocks = clocks[0::2]
    else:
        our_clocks = clocks[1::2]

    our_clock_secs = [parse_clock(c) for c in our_clocks if parse_clock(c) is not None]

    flagged = False
    time_trouble = False
    final_time = None

    if our_clock_secs:
        final_time = our_clock_secs[-1]
        flagged = final_time == 0
        time_trouble = any(t < 10 for t in our_clock_secs[-5:]) if len(our_clock_secs) >= 5 else final_time < 10

    move_count = len(re.findall(r'\d+\.', move_text))

    return {
        "flagged": flagged,
        "time_trouble": time_trouble,
        "final_clock_secs": final_time,
        "move_count": move_count,
    }

def analyze_games(data):
    games = data.get("games", [])
    total = len(games)

    our_wins = 0
    our_losses = 0
    our_draws = 0

    loss_details = []
    flagged_losses = 0
    time_trouble_losses = 0

    ratings_over_time = []

    for g in games:
        white = g.get("white", {})
        black = g.get("black", {})

        if white.get("username", "").lower() == USERNAME.lower():
            our_color = "white"
            our_result = white.get("result", "")
            opp_rating = black.get("rating", "?")
            opp_username = black.get("username", "?")
            our_rating = white.get("rating", None)
        else:
            our_color = "black"
            our_result = black.get("result", "")
            opp_rating = white.get("rating", "?")
            opp_username = white.get("username", "?")
            our_rating = black.get("rating", None)

        if our_rating:
            end_time = g.get("end_time", 0)
            ratings_over_time.append((end_time, our_rating))

        if our_result in ("win",):
            our_wins += 1
        elif our_result in ("checkmated", "timeout", "resigned", "abandoned", "timevsinsufficient"):
            our_losses += 1
            pgn = g.get("pgn", "")
            analysis = analyze_game(pgn, our_color)

            if analysis["flagged"] or our_result == "timeout":
                flagged_losses += 1
            if analysis["time_trouble"]:
                time_trouble_losses += 1

            loss_details.append({
                "opponent": opp_username,
                "opp_rating": opp_rating,
                "result_type": our_result,
                "color": our_color,
                "flagged": analysis["flagged"] or our_result == "timeout",
                "time_trouble": analysis["time_trouble"],
                "final_clock": analysis["final_clock_secs"],
                "moves": analysis["move_count"],
            })
        else:
            our_draws += 1

    # Sort ratings by time
    ratings_over_time.sort(key=lambda x: x[0])
    start_rating = ratings_over_time[0][1] if ratings_over_time else "?"
    end_rating = ratings_over_time[-1][1] if ratings_over_time else "?"

    return {
        "total_games": total,
        "wins": our_wins,
        "losses": our_losses,
        "draws": our_draws,
        "win_rate": round(our_wins / total * 100, 1) if total else 0,
        "flagged_losses": flagged_losses,
        "time_trouble_losses": time_trouble_losses,
        "pct_losses_on_time": round(flagged_losses / our_losses * 100, 1) if our_losses else 0,
        "start_rating": start_rating,
        "end_rating": end_rating,
        "loss_details": loss_details,
    }

def print_report(stats_data, game_analysis):
    blitz = stats_data.get("chess_blitz", {})
    best = blitz.get("best", {}).get("rating", "N/A")
    last = blitz.get("last", {}).get("rating", "N/A")
    record = blitz.get("record", {})

    print("=" * 60)
    print(f"  CHESS.COM ANALYSIS — {USERNAME}")
    print(f"  Run at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    print("\n📊 BLITZ STATS (ALL TIME)")
    print(f"  Current rating : {last}")
    print(f"  Peak rating    : {best}")
    print(f"  All-time record: {record.get('win','?')}W / {record.get('loss','?')}L / {record.get('draw','?')}D")

    a = game_analysis
    print(f"\n📅 THIS MONTH")
    print(f"  Games played   : {a['total_games']}")
    print(f"  Record         : {a['wins']}W / {a['losses']}L / {a['draws']}D  ({a['win_rate']}% win rate)")
    print(f"  Rating change  : {a['start_rating']} → {a['end_rating']}")

    print(f"\n⏱️  TIME MANAGEMENT")
    print(f"  Losses on flag/timeout : {a['flagged_losses']} / {a['losses']}  ({a['pct_losses_on_time']}% of losses)")
    print(f"  Losses with time trouble: {a['time_trouble_losses']} / {a['losses']}")

    print(f"\n❌ LOSS BREAKDOWN")
    for i, l in enumerate(a['loss_details'], 1):
        flag_tag = " ⏰ FLAGGED" if l['flagged'] else (" ⚠️ time trouble" if l['time_trouble'] else "")
        clock_str = f"{l['final_clock']}s left" if l['final_clock'] is not None else "?"
        print(f"  {i:2}. vs {l['opponent']} ({l['opp_rating']}) "
              f"[{l['color']}] — {l['result_type']} — {l['moves']} moves — {clock_str}{flag_tag}")

    print("\n💡 KEY INSIGHT")
    if a['pct_losses_on_time'] >= 40:
        print(f"  ⚠️  {a['pct_losses_on_time']}% of your losses are on time.")
        print("  Fixing time management alone could add 100+ rating points.")
    else:
        print(f"  Time losses are {a['pct_losses_on_time']}% — other patterns dominating losses.")

    print("\n" + "=" * 60)

def main():
    games_url = get_current_month_url()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # Output directory: same folder as this script
    out_dir = os.path.dirname(os.path.abspath(__file__))
    raw_stats_file   = os.path.join(out_dir, f"raw_stats_{timestamp}.json")
    raw_games_file   = os.path.join(out_dir, f"raw_games_{timestamp}.json")
    report_file      = os.path.join(out_dir, f"report_{timestamp}.txt")

    print(f"Fetching stats from {STATS_URL} ...")
    stats_data = fetch(STATS_URL)
    with open(raw_stats_file, 'w') as f:
        json.dump(stats_data, f, indent=2)
    print(f"  → saved to {raw_stats_file}")

    print(f"Fetching games from {games_url} ...")
    games_data = fetch(games_url)
    with open(raw_games_file, 'w') as f:
        json.dump(games_data, f, indent=2)
    print(f"  → saved to {raw_games_file}")

    analysis = analyze_games(games_data)

    # Capture report output to both terminal and file
    import io
    buffer = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buffer
    print_report(stats_data, analysis)
    sys.stdout = old_stdout
    report_text = buffer.getvalue()

    print(report_text)
    with open(report_file, 'w') as f:
        f.write(report_text)
    print(f"Report saved to {report_file}")

if __name__ == "__main__":
    main()
