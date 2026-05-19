# chess-burn-tracker

> ⚠️ **This project was built entirely with AI (Claude).** Prompts used are listed at the bottom.

A personal chess improvement toolkit for chess.com blitz players who keep flagging.

The core observation: most clock losses aren't from playing out lost positions — they're from spending 20–50 seconds in the middlegame sensing a combination you can't quite calculate.  This tool finds those exact moments, evaluates whether you were actually onto something, and builds a drill set from the positions that mattered.

---

## The Idea

When you burn a lot of time on a single move in a blitz game, one of three things is happening:

| What you were doing | What it means | Where it goes |
|---|---|---|
| Sensing a real win (engine agrees) | Right instinct, wrong speed | `burn_quiz/` — drill it |
| Calculating a genuinely complex position | Justified, just need to be faster | `burn_quiz/` — drill it |
| Hunting magic in an already-lost position | Sunk cost — you blundered earlier | `blunder_quiz/` — find the real mistake |

> **On the terminology:** the chess community doesn't have one canonical word for this. Players and coaches use "time trouble position," "critical moment," "critical juncture," or just "the position where you thought the longest." Some engines and databases use "think time" or "long think." The closest formal term is probably **"critical moment"** — the move where the most clock was consumed. We call it a *burn move* throughout the code.

---

## Scripts

### When to run each script

```
After every session (or whenever you want fresh data):
    1. analyze_chess.py        → fetch your latest games from chess.com
    2. categorize_losses.py    → classify every loss and see the breakdown
    3. make_quiz.py            → regenerate burn_quiz/ (A1+A2 positions only)
    4. make_blunder_quiz.py    → regenerate blunder_quiz/ (A3 positions only)

First-time setup only:
    brew install stockfish
    pip3 install Pillow --break-system-packages
```

---

### `analyze_chess.py` — Fetch games and print a loss report

```bash
python3 analyze_chess.py
```

Hits the chess.com public API, saves raw JSON locally, and prints a summary:
win rate, timeout percentage, loss breakdown.  Edit `USERNAME` at the top to
use your own account.  Run this first whenever you want fresh data.

**Saves:** `raw_games_TIMESTAMP.json`, `raw_stats_TIMESTAMP.json`, `report_TIMESTAMP.txt`

---

### `categorize_losses.py` — Classify every loss by root cause

```bash
python3 categorize_losses.py
```

For each loss, finds the statistically significant burn moments, evaluates
the position at each one with Stockfish, and classifies the game:

| Category | Meaning |
|---|---|
| **A1** Sensing a Real Win | Engine says you were winning when you burned. Right instinct. |
| **A2** Complex / Double-Edged | Roughly equal — justified calculation, just need more speed. |
| **A3** Sunk Cost Search | You were already clearly losing. Burning time here wastes your clock. |
| **B** Slow Accumulation | Timed out with no single standout burn — general pacing problem. |
| **C** Time + Board Loss | In time trouble AND outplayed on the board. |
| **D** Outplayed | Lost with plenty of clock — pure chess mistake, not time. |

A1 + A2 together are "the problem" and make up the `burn_quiz/` drill set.
A3 is a separate problem: you're hunting magic in a dead position. The
`blunder_quiz/` shows where things actually went wrong in those games.

**Requires:** `brew install stockfish`

---

### `make_quiz.py` — Build the burn drill set

```bash
python3 make_quiz.py
```

Runs burn detection + Stockfish evaluation on every loss.  Generates board
PNGs **only** for A1 and A2 positions — the moments where your instincts were
right and where the position was genuinely complex.

Multiple statistically significant burn positions per game are included when
they exist.  Burns are detected using game-relative statistics: a move only
counts as a burn if its time drop is an outlier within *that game's own* pace,
not just above a fixed threshold.

**Output:** `burn_quiz/quiz_NN_Opponent.png` + `burn_quiz/quiz_answers.json`

**Requires:** `brew install stockfish`, `pip3 install Pillow --break-system-packages`

Options:
- `--no-download` — skip downloading chess.com Neo piece images (Unicode fallback)
- `--no-eval` — skip Stockfish, keep all burn positions regardless of eval

---

### `make_blunder_quiz.py` — Build the blunder drill set

```bash
python3 make_blunder_quiz.py
```

Handles A3 losses — games where you were already losing when you burned the
clock.  Instead of the burn position (which was lost anyway), this finds the
earlier move that *caused* the position to become lost.

Uses Stockfish to evaluate every position in the game, then identifies moves
whose eval drop was a statistical outlier within the game's own variance.
Multiple blunders per game are included when they exist.

**Output:** `blunder_quiz/blunder_NN_Opponent.png` + `blunder_quiz/blunder_answers.json`

**Requires:** `brew install stockfish`, `pip3 install Pillow --break-system-packages`

---

## How burn detection works

```
1. Extract all {[%clk h:mm:ss]} annotations from the PGN (chess.com format)
2. Separate our clock readings from the opponent's (alternating entries)
3. Compute drop[i] = clock[i] − clock[i+1] for each of our moves
4. Threshold = max(mean + 1.5 × std_dev, 8s absolute floor)
   → adapts to the game's own pace: a 12s burn in a fast game is flagged
     just like a 30s burn in a slower one
5. Replay the game to the burn move → extract board state as PNG + FEN
6. Evaluate with Stockfish (300ms/position) to classify A1/A2/A3
```

---

## How blunder detection works

```
1. Replay the game, evaluating every position after our moves
   (eval_after[N] reused as eval_before[N+1] — one Stockfish call per position)
2. Compute per-move eval drops (our perspective, negative = we dropped)
3. Threshold = max(mean + 1.5 × std_dev of drops, 80cp absolute floor)
   → game-relative: only flags moves that stand out vs. normal fluctuation
4. Filter: only positions that were survivable BEFORE the move (eval ≥ −1.0)
5. Return board states before each qualifying blunder, sorted by severity
```

---

## Dependencies

```bash
brew install stockfish                           # position evaluation
pip3 install Pillow --break-system-packages      # board image rendering
```

No `python-chess` required.  Board tracking and SAN parsing are implemented
from scratch in `burn_finder.py`.

---

## File structure

```
Chess/
├── analyze_chess.py        # 1. fetch games + print report
├── categorize_losses.py    # 2. classify losses (requires stockfish)
├── make_quiz.py            # 3. generate burn_quiz/ (A1+A2 only)
├── make_blunder_quiz.py    # 4. generate blunder_quiz/ (A3 only)
├── burn_finder.py          # core library: clock parsing, board tracking, rendering, stockfish
├── blunder_finder.py       # eval-replay engine: finds moves that lost the game
├── .gitignore
└── README.md

# Generated (gitignored):
├── raw_games_*.json        # chess.com API snapshots
├── raw_stats_*.json
├── report_*.txt
├── categorization_*.json
├── pieces/                 # cached chess.com Neo piece PNGs
├── burn_quiz/              # drill set for A1+A2 (right instinct, wrong speed)
│   ├── quiz_NN_Opponent.png
│   └── quiz_answers.json
└── blunder_quiz/           # drill set for A3 (find the real mistake)
    ├── blunder_NN_Opponent.png
    └── blunder_answers.json
```

---

## Prompts used to build this (AI-generated)

Built through a conversation with Claude (Anthropic). Key prompts in order:

1. *"Pull up all my chess games at chess.com/member/handsomestbedbug and give me reasons why I lost."*
2. *"Create a script that curls the stats and games API endpoints and analyzes them repeatedly. Save it to a Chess folder."*
3. *"Edit the script so it outputs the API results to a file so I can upload them for proper analysis."*
4. *"I flag a lot because I'm trying to find a beautiful move. I think in most positions where I'm flagging I sense a win but can't find it. Can you see if this is the case?"*
5. *"What percentage of my losses are this? Do a quantitative analysis across all my games."*
6. *"Can you find the burn time spot for me across all my games so I can practice the positions? I want to precache the solutions."*
7. *"Save the script. Make the code clean, I'm gonna commit it to GitHub. Gitignore the PNGs and txt files."*
8. *"The board orientation looks wrong — I should be on my own side. Also make the pieces clearer like chess.com default."*
9. *"Write documentation so people know what it's used for."*
10. *"Write a script to categorize how many of my losses were due to this problem."*
11. *"Are all my timeouts really attack hunting? You also have to categorize the position and see if I was winning or had interesting/complex ideas."*
12. *"A3 is a separate problem altogether — I need a blunder finder. Make a separate quiz folder called blunder_quiz and rename the existing quiz folder to burn_quiz."*
13. *"There may be multiple blunders and multiple burn moves per game — take that into account."*
14. *"These should only be the moves that statistically stand out — if we look too granularly then every move is a blunder and a burn move."*
15. *"Update the README based on all the new prompts. Tell me when to run each script."*
