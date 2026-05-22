#!/usr/bin/env python3
"""
burn_finder.py — Chess Time-Burn Position Extractor
====================================================

Chess.com blitz games with clock annotations tell you exactly which move you
burned the most time on.  That move is almost always the one where you sensed
something — a combination, a sacrifice, a forced win — but couldn't calculate
it fast enough.  Those positions are *exactly* what you need to drill.

This module does three things:

1.  **Find the burn move** — parse every clock time in a PGN and locate the
    single move where your clock dropped the most seconds in one go.

2.  **Extract the board position** — replay the game move-by-move up to (but
    not including) the burn move, so you see the puzzle *before* you played it.

3.  **Render the board to a PNG** — draws a chess board with the position using
    Pillow.  If you run `download_pieces()` first, it will use chess.com-style
    Neo piece images; otherwise it falls back to Unicode glyphs rendered at
    high resolution.

Typical usage (called by make_quiz.py):

    from burn_finder import find_burn, draw_board, download_pieces

    result = find_burn(pgn_text, our_color="white")
    if result:
        download_pieces("pieces")          # one-time download, cached locally
        img = draw_board(result.board,
                         flipped=(our_color == "black"),
                         pieces_dir="pieces")
        img.save("quiz_position.png")
        print(result.fen)                  # paste into lichess.org/analysis

Dependencies:
    pip install Pillow

No python-chess required.  All SAN parsing is handled internally.
"""

import re
import os
import copy
import urllib.request
from collections import namedtuple

# PIL is only required for draw_board() and download_pieces().
# Import lazily so that categorize_losses.py works without Pillow installed.
try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------

BurnResult = namedtuple(
    "BurnResult",
    ["board", "fen", "move_played", "move_num", "burn_seconds",
     "clock_before", "clock_after"]
)
"""
Fields
------
board        : 8×8 list (rank 0 = rank 1 / White's back rank)
fen          : FEN string of the position before the burn move
move_played  : SAN string of the actual move played (the one you thought about)
move_num     : full-game move number (1-based)
burn_seconds : how many seconds were consumed on this move
clock_before : clock reading before the move (seconds)
clock_after  : clock reading after the move (seconds)
"""

# ---------------------------------------------------------------------------
# Piece / colour constants
# ---------------------------------------------------------------------------

EMPTY = 0
W  =  1    # white
BL = -1    # black

P, N, B, R, Q, K = 1, 2, 3, 4, 5, 6

PIECE_UNICODE = {
    (W,  K): "♔", (W,  Q): "♕", (W,  R): "♖",
    (W,  B): "♗", (W,  N): "♘", (W,  P): "♙",
    (BL, K): "♚", (BL, Q): "♛", (BL, R): "♜",
    (BL, B): "♝", (BL, N): "♞", (BL, P): "♟",
}

# chess.com Neo piece filenames (color + piece)
PIECE_FILES = {
    (W,  K): "wk", (W,  Q): "wq", (W,  R): "wr",
    (W,  B): "wb", (W,  N): "wn", (W,  P): "wp",
    (BL, K): "bk", (BL, Q): "bq", (BL, R): "br",
    (BL, B): "bb", (BL, N): "bn", (BL, P): "bp",
}

# ---------------------------------------------------------------------------
# Clock parsing
# ---------------------------------------------------------------------------

# Matches {[%clk 0:02:59.9]} — chess.com embeds these after every move
_CLK_RE = re.compile(r'\{\[%clk (\d+:\d+:\d+(?:\.\d+)?)\]\}')


def _parse_clock(s: str) -> int:
    """Convert clock string like '0:02:59.9' to total seconds (integer)."""
    s = s.split(".")[0]          # drop sub-second decimal
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + int(sec)


def _extract_our_clocks(pgn: str, our_color: str) -> list[int]:
    """
    Return a list of our clock readings (in seconds) for every move we made,
    in game order.

    Clock annotation order in PGN: white_move_1, black_move_1, white_move_2, …
    So white clocks are at even indices (0, 2, 4, …) and black at odd (1, 3, …).
    """
    all_clocks = [_parse_clock(m) for m in _CLK_RE.findall(pgn)]
    if our_color == "white":
        return all_clocks[0::2]
    else:
        return all_clocks[1::2]


# ---------------------------------------------------------------------------
# Main public function: find the burn move
# ---------------------------------------------------------------------------

def find_burn(pgn: str, our_color: str) -> BurnResult | None:
    """
    Return the single biggest time burn in the game.
    Convenience wrapper around find_all_burns(); returns the top result or None.
    """
    burns = find_all_burns(pgn, our_color)
    return burns[0] if burns else None


def find_all_burns(pgn: str, our_color: str,
                   abs_floor_s: int = 8) -> list[BurnResult]:
    """
    Return ALL statistically significant burn moments in a game, sorted by
    burn size (largest first).

    A move counts as a burn only if its time drop is a statistical outlier
    *within this game* — specifically, greater than mean + 1.5 × std_dev of
    all per-move drops.  An absolute floor (``abs_floor_s``) is also applied
    so that truly tiny drops in very fast games don't sneak in.

    This means the threshold adapts to each game's own pace: a 12-second burn
    in a blitz game where everyone thinks for 2–3 seconds per move stands out
    just as much as a 30-second burn in a slower game.

    Only returns positions within the middlegame window (moves MIDDLEGAME_START
    to MIDDLEGAME_END) to avoid flagging opening-theory pauses or dead endgame
    moves.
    """
    import statistics

    our_clocks = _extract_our_clocks(pgn, our_color)
    if len(our_clocks) < 3:
        return []

    drops = [our_clocks[i] - our_clocks[i + 1]
             for i in range(len(our_clocks) - 1)]

    mean = statistics.mean(drops)
    std  = statistics.pstdev(drops)          # population stdev — no Bessel bias for small N

    # Threshold = game-relative outlier AND absolute floor
    threshold = max(mean + 1.5 * std, abs_floor_s)

    results = []
    for idx, drop in enumerate(drops):
        move_num = idx + 1    # 1-based
        if drop < threshold:
            continue
        if not (MIDDLEGAME_START <= move_num <= MIDDLEGAME_END):
            continue

        board, fen, move_played, _ = _get_position_at_move(pgn, move_num, our_color)
        if board is None:
            continue

        results.append(BurnResult(
            board=board,
            fen=fen,
            move_played=move_played,
            move_num=move_num,
            burn_seconds=drop,
            clock_before=our_clocks[idx],
            clock_after=our_clocks[idx + 1],
        ))

    return sorted(results, key=lambda r: r.burn_seconds, reverse=True)


# ---------------------------------------------------------------------------
# Stockfish evaluator  (UCI protocol, no extra packages — just subprocess)
# ---------------------------------------------------------------------------

import subprocess
import shutil
import json as _json

# Centipawn thresholds for classification (from your perspective, positive = winning)
WINNING_CP  =  150   # ≥ +1.5 pawns → you're winning
LOSING_CP   = -150   # ≤ -1.5 pawns → you're losing

# Move range considered the middlegame
MIDDLEGAME_START = 8
MIDDLEGAME_END   = 40

# Minimum time burn (seconds) to flag a position as a burn moment
BURN_THRESHOLD = 15


_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval_cache.json")


class EvalCache:
    """
    Persistent disk cache for Stockfish evaluations.

    Keyed by ``"{fen}|{our_color}"``.  Loaded from disk on first use, saved
    back on close().  Silently survives a corrupted or missing cache file.

    Typical usage (handled automatically inside StockfishEvaluator):

        cache = EvalCache()
        result = cache.get(fen, our_color)   # None on miss
        if result is None:
            result = <stockfish eval>
            cache.set(fen, our_color, result)
        cache.save()
    """

    def __init__(self, path: str = _CACHE_PATH):
        self._path  = path
        self._data: dict = {}
        self._hits  = 0
        self._misses = 0
        self._dirty = False
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    self._data = _json.load(f)
            except Exception:
                self._data = {}

    def _key(self, fen: str, our_color: str) -> str:
        return f"{fen}|{our_color}"

    def get(self, fen: str, our_color: str) -> "dict | None":
        result = self._data.get(self._key(fen, our_color))
        if result is not None:
            self._hits += 1
        else:
            self._misses += 1
        return result

    def set(self, fen: str, our_color: str, result: dict):
        self._data[self._key(fen, our_color)] = result
        self._dirty = True

    def save(self):
        """Write cache to disk only if it changed."""
        if self._dirty:
            with open(self._path, "w") as f:
                _json.dump(self._data, f)
            self._dirty = False

    def stats(self) -> str:
        total = self._hits + self._misses
        pct   = f"{100 * self._hits // total}%" if total else "—"
        return (f"{self._hits}/{total} cache hits ({pct})  "
                f"— {len(self._data)} positions stored in {os.path.basename(self._path)}")


def find_stockfish() -> str | None:
    """Return path to the stockfish binary, or None if not found."""
    sf = shutil.which("stockfish")
    if sf:
        return sf
    for path in ["/opt/homebrew/bin/stockfish",   # Apple Silicon brew
                 "/usr/local/bin/stockfish",        # Intel brew
                 "/usr/bin/stockfish"]:             # Linux
        if os.path.exists(path):
            return path
    return None


class StockfishEvaluator:
    """
    Persistent Stockfish process for batch position evaluation.

    Reuses a single process across many FENs — much faster than spawning
    per-position.  Use as a context manager:

        with StockfishEvaluator() as sf:
            result = sf.evaluate(fen, our_color="white")

    If Stockfish is not installed, pass path=None and evaluate() returns None.

    evaluate() returns a dict:
        cp       : centipawns from YOUR side (positive = you winning)
        mate_in  : moves to forced mate; positive = you win, None if no mate
        depth    : search depth reached
    """

    def __init__(self, path: str | None = None, movetime_ms: int = 300,
                 cache: "EvalCache | None" = None):
        self._path       = path or find_stockfish()
        self._movetime   = movetime_ms
        self._proc       = None
        self._cache      = cache if cache is not None else EvalCache()
        if self._path:
            self._proc = subprocess.Popen(
                [self._path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
            )
            self._send("uci");           self._read_until("uciok")
            self._send("setoption name Hash value 32")
            self._send("isready");       self._read_until("readyok")

    def available(self) -> bool:
        return self._proc is not None

    def _send(self, cmd: str):
        self._proc.stdin.write(cmd + "\n")
        self._proc.stdin.flush()

    def _read_until(self, keyword: str) -> list[str]:
        lines = []
        while True:
            line = self._proc.stdout.readline().strip()
            lines.append(line)
            if keyword in line:
                return lines

    def evaluate(self, fen: str, our_color: str) -> dict | None:
        """
        Evaluate one position.

        Checks the on-disk cache first — if a prior eval exists for this
        (FEN, color) pair, returns it immediately without touching Stockfish.
        Otherwise runs Stockfish and stores the result for next time.

        Returns None if Stockfish is not available.
        """
        if not self._proc:
            return None

        cached = self._cache.get(fen, our_color)
        if cached is not None:
            return cached

        self._send(f"position fen {fen}")
        self._send(f"go movetime {self._movetime}")
        lines = self._read_until("bestmove")

        cp, mate, depth = None, None, 0
        for line in lines:
            m = re.search(r"\bdepth (\d+)\b", line)
            if m:
                depth = int(m.group(1))
            if "score cp" in line:
                m = re.search(r"score cp (-?\d+)", line)
                if m:
                    cp, mate = int(m.group(1)), None
            elif "score mate" in line:
                m = re.search(r"score mate (-?\d+)", line)
                if m:
                    mate, cp = int(m.group(1)), None

        if cp is None and mate is None:
            return None

        if mate is not None:
            our_mate = mate if our_color == "white" else -mate
            result = {"cp": 3000 if our_mate > 0 else -3000,
                      "depth": depth, "mate_in": our_mate}
        else:
            our_cp = cp if our_color == "white" else -cp
            result = {"cp": our_cp, "depth": depth, "mate_in": None}

        self._cache.set(fen, our_color, result)
        return result

    def close(self):
        if self._proc:
            try:
                self._send("quit"); self._proc.wait(timeout=3)
            except Exception:
                self._proc.kill()
        self._cache.save()
        print(f"  eval cache: {self._cache.stats()}")

    def __enter__(self):  return self
    def __exit__(self, *_): self.close()


def classify_burn(burn: BurnResult | None, eval_result: dict | None,
                  our_color: str, result: str) -> tuple[str, str]:
    """
    Given a burn analysis + engine eval, return (category, label).

    Categories
    ----------
    A1  Sensing a Real Win      — big middlegame burn, engine says you were winning
    A2  Complex / Double-Edged  — big middlegame burn, engine says roughly equal
    A3  Sunk Cost Search        — big middlegame burn, but you were already losing
    A?  Burn / No Eval          — big burn detected, no engine available
    B   Slow Accumulation       — timed out, no single large burn
    C   Time + Board Loss       — in time trouble at end, lost on board
    D   Outplayed               — lost on board with time to spare
    """
    flagged      = result == "timeout"
    time_trouble = burn is not None and burn.clock_after < 30

    has_burn = (burn is not None
                and burn.burn_seconds >= BURN_THRESHOLD
                and MIDDLEGAME_START <= burn.move_num <= MIDDLEGAME_END)

    if has_burn:
        cp = eval_result["cp"] if eval_result else None
        if   cp is None:          return "A?", "Burn / No Eval"
        elif cp >= WINNING_CP:    return "A1", "Sensing a Real Win"
        elif cp >= LOSING_CP:     return "A2", "Complex / Double-Edged"
        else:                     return "A3", "Sunk Cost Search"
    elif flagged:                 return "B",  "Slow Accumulation"
    elif time_trouble:            return "C",  "Time + Board Loss"
    else:                         return "D",  "Outplayed"


# ---------------------------------------------------------------------------
# Board helpers
# ---------------------------------------------------------------------------

def _init_board():
    """Return the standard starting position as an 8×8 list (rank 0 = rank 1)."""
    board = [[None] * 8 for _ in range(8)]
    back = [R, N, B, Q, K, B, N, R]
    for col in range(8):
        board[0][col] = (W,  back[col])
        board[1][col] = (W,  P)
        board[6][col] = (BL, P)
        board[7][col] = (BL, back[col])
    return board


def board_to_fen(board, active="w") -> str:
    """Serialize board state to a FEN position string (castling/ep fields omitted)."""
    rows = []
    for rank in range(7, -1, -1):
        empty, row = 0, ""
        for file in range(8):
            piece = board[rank][file]
            if piece is None:
                empty += 1
            else:
                if empty:
                    row += str(empty)
                    empty = 0
                sym = {P: "p", N: "n", B: "b", R: "r", Q: "q", K: "k"}[piece[1]]
                row += sym.upper() if piece[0] == W else sym
        if empty:
            row += str(empty)
        rows.append(row)
    return "/".join(rows) + f" {active} - - 0 1"


# ---------------------------------------------------------------------------
# Move geometry
# ---------------------------------------------------------------------------

def _path_clear(board, fr, fc, tr, tc) -> bool:
    dr = 0 if tr == fr else (1 if tr > fr else -1)
    dc = 0 if tc == fc else (1 if tc > fc else -1)
    r, c = fr + dr, fc + dc
    while (r, c) != (tr, tc):
        if board[r][c]:
            return False
        r += dr; c += dc
    return True


def _target_ok(board, color, tr, tc) -> bool:
    t = board[tr][tc]
    return t is None or t[0] != color


def _can_reach(board, color, pt, fr, fc, tr, tc) -> bool:
    """Return True if the piece at (fr,fc) can legally reach (tr,tc)."""
    dr, dc = tr - fr, tc - fc

    if pt == P:
        direction  = 1 if color == W else -1
        start_rank = 1 if color == W else 6
        if dc != 0:   # diagonal capture
            return (abs(dc) == 1 and dr == direction
                    and board[tr][tc] is not None
                    and board[tr][tc][0] != color)
        if dr == direction and board[tr][tc] is None:
            return True
        if (dr == 2 * direction and fr == start_rank
                and board[tr][tc] is None
                and board[fr + direction][fc] is None):
            return True
        return False

    if pt == N:
        return sorted([abs(dr), abs(dc)]) == [1, 2] and _target_ok(board, color, tr, tc)

    if pt == B:
        return (abs(dr) == abs(dc) and dr != 0
                and _path_clear(board, fr, fc, tr, tc)
                and _target_ok(board, color, tr, tc))

    if pt == R:
        return ((dr == 0 or dc == 0) and not (dr == 0 and dc == 0)
                and _path_clear(board, fr, fc, tr, tc)
                and _target_ok(board, color, tr, tc))

    if pt == Q:
        return (_can_reach(board, color, B, fr, fc, tr, tc)
                or _can_reach(board, color, R, fr, fc, tr, tc))

    if pt == K:
        return max(abs(dr), abs(dc)) == 1 and _target_ok(board, color, tr, tc)

    return False


def _find_piece(board, color, pt, tr, tc, hint_col=None, hint_row=None):
    candidates = []
    for r in range(8):
        for c in range(8):
            piece = board[r][c]
            if piece and piece[0] == color and piece[1] == pt:
                if hint_col is not None and c != hint_col:
                    continue
                if hint_row is not None and r != hint_row:
                    continue
                if _can_reach(board, color, pt, r, c, tr, tc):
                    candidates.append((r, c))
    return candidates


# ---------------------------------------------------------------------------
# Move application (SAN)
# ---------------------------------------------------------------------------

def _apply_move(board, color, san):
    """Apply one SAN move to board in-place and return it."""
    san = san.strip().rstrip("+#").replace("!", "").replace("?", "")

    # Castling
    if san in ("O-O", "0-0"):
        rank = 0 if color == W else 7
        board[rank][4] = None; board[rank][7] = None
        board[rank][6] = (color, K); board[rank][5] = (color, R)
        return board
    if san in ("O-O-O", "0-0-0"):
        rank = 0 if color == W else 7
        board[rank][4] = None; board[rank][0] = None
        board[rank][2] = (color, K); board[rank][3] = (color, R)
        return board

    # Promotion  e.g. e8=Q
    promo = None
    m = re.search(r"=([QRBN])", san)
    if m:
        promo = {"Q": Q, "R": R, "B": B, "N": N}[m.group(1)]
        san = san[:m.start()]

    # Piece type
    piece_map = {"N": N, "B": B, "R": R, "Q": Q, "K": K}
    if san and san[0].isupper() and san[0] in piece_map:
        pt, rest = piece_map[san[0]], san[1:]
    else:
        pt, rest = P, san

    rest = rest.replace("x", "")
    dest = rest[-2:]
    tc = ord(dest[0]) - ord("a")
    tr = int(dest[1]) - 1
    hint_str = rest[:-2]
    hint_col = hint_row = None
    for ch in hint_str:
        if ch.isalpha():   hint_col = ord(ch) - ord("a")
        elif ch.isdigit(): hint_row = int(ch) - 1

    candidates = _find_piece(board, color, pt, tr, tc, hint_col, hint_row)
    if not candidates:
        return board  # unrecognised — skip gracefully
    fr, fc = candidates[0]

    # En-passant
    if pt == P and fc != tc and board[tr][tc] is None:
        board[fr][tc] = None

    board[tr][tc] = (color, promo if promo else pt)
    board[fr][fc] = None
    return board


# ---------------------------------------------------------------------------
# PGN replayer
# ---------------------------------------------------------------------------

def _get_position_at_move(pgn: str, target_move: int, target_color: str):
    """
    Replay ``pgn`` up to (but NOT including) move ``target_move`` for
    ``target_color``, then return (board, fen, move_san, move_num).

    This gives you the position the player was staring at when they burned
    all their time.
    """
    # Strip headers and clock/eval annotations
    moves_raw = " ".join(l for l in pgn.split("\n")
                         if not l.startswith("[") and l.strip())
    moves_raw = re.sub(r"\{[^}]*\}", "", moves_raw)
    moves_raw = re.sub(r"\$\d+", "", moves_raw)

    tokens = moves_raw.split()
    board  = _init_board()
    color  = W
    move_n = 1
    target_side = W if target_color == "white" else BL

    for tok in tokens:
        if re.match(r"^\d+\.$", tok):
            move_n = int(tok[:-1]); continue
        if re.match(r"^\d+\.\.\.$", tok):
            continue
        if tok in ("1-0", "0-1", "1/2-1/2", "*"):
            break
        if not re.match(r"^[NBRQK]?[a-h]?[1-8]?x?[a-h][1-8]|O-O|0-0", tok):
            continue

        # Stop BEFORE the burn move and hand back the board
        if move_n == target_move and color == target_side:
            fen = board_to_fen(board, "w" if color == W else "b")
            return board, fen, tok, move_n

        board = _apply_move(board, color, tok)
        color = BL if color == W else W

    return None, None, None, None


# ---------------------------------------------------------------------------
# Piece image download (chess.com Neo set)
# ---------------------------------------------------------------------------

_PIECE_CODES = ["bb", "bk", "bn", "bp", "bq", "br",
                "wb", "wk", "wn", "wp", "wq", "wr"]
_PIECE_URL   = "https://www.chess.com/chess-themes/pieces/neo/150/{}.png"


def download_pieces(dest_dir: str = "pieces") -> int:
    """
    Download the chess.com Neo piece PNGs into ``dest_dir`` (created if needed).
    Skips files already present.  Returns the number of pieces available.

    These are 150×150 PNG images used by draw_board() when ``pieces_dir`` is set.
    Only needs to run once; files are cached on disk.
    """
    os.makedirs(dest_dir, exist_ok=True)
    n = 0
    for pc in _PIECE_CODES:
        dest = os.path.join(dest_dir, f"{pc}.png")
        if os.path.exists(dest):
            n += 1
            continue
        try:
            req = urllib.request.Request(
                _PIECE_URL.format(pc),
                headers={"User-Agent": "chess-quiz/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            with open(dest, "wb") as f:
                f.write(data)
            print(f"  ↓ {pc}.png")
            n += 1
        except Exception as e:
            print(f"  ✗ {pc}.png — {e}")
    return n


def _load_piece_images(pieces_dir: str, sq_px: int) -> "dict | None":
    """
    Load piece PNG files into a dict keyed by (color, piece_type).
    Returns None if the directory doesn't exist or is missing files.
    """
    if not pieces_dir or not os.path.isdir(pieces_dir):
        return None
    images = {}
    for (color, pt), code in PIECE_FILES.items():
        path = os.path.join(pieces_dir, f"{code}.png")
        if not os.path.exists(path):
            return None
        img = Image.open(path).convert("RGBA").resize((sq_px, sq_px), Image.LANCZOS)
        images[(color, pt)] = img
    return images


# ---------------------------------------------------------------------------
# Board renderer
# ---------------------------------------------------------------------------

LIGHT_SQ = "#f0d9b5"    # chess.com board colours
DARK_SQ  = "#b58863"
BG_COLOR = (40, 40, 40, 255)

_FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    # Linux / sandbox
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _best_font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def draw_board(board, flipped: bool = False, size: int = 480,
               pieces_dir: str | None = None):
    """
    Render the board to a PIL RGBA Image.

    Parameters
    ----------
    board      : 8×8 list from find_burn() or _init_board()
    flipped    : True = Black's perspective (Black pieces at bottom)
    size       : pixel width/height of the inner board grid
    pieces_dir : path to a folder with Neo piece PNGs (from download_pieces()).
                 If None or missing, falls back to Unicode glyphs.

    Returns
    -------
    PIL.Image (RGBA)
    """
    if not _PIL_AVAILABLE:
        raise ImportError(
            "Pillow is required for draw_board(). Install it with:\n"
            "    pip3 install Pillow"
        )

    SCALE   = 3            # internal supersampling factor for crisp text
    sq      = (size // 8) * SCALE
    margin  = 28 * SCALE
    total   = size * SCALE + 2 * margin

    hi_img  = Image.new("RGBA", (total, total), BG_COLOR)
    hi_draw = ImageDraw.Draw(hi_img)

    piece_imgs = _load_piece_images(pieces_dir, sq) if pieces_dir else None
    piece_font = _best_font(int(sq * 0.68))
    label_font = _best_font(int(11 * SCALE))

    files = "abcdefgh" if not flipped else "hgfedcba"
    ranks = "12345678" if not flipped else "87654321"

    for row in range(8):
        for col in range(8):
            # Map display grid position → board array indices
            board_rank = (7 - row) if not flipped else row
            board_file = col       if not flipped else (7 - col)

            x = margin + col * sq
            y = margin + row * sq

            sq_color = LIGHT_SQ if (board_rank + board_file) % 2 == 0 else DARK_SQ
            hi_draw.rectangle([x, y, x + sq, y + sq], fill=sq_color)

            piece = board[board_rank][board_file]
            if piece is None:
                continue

            if piece_imgs:
                # Paste the downloaded PNG directly onto the square
                hi_img.paste(piece_imgs[piece], (x, y), piece_imgs[piece])
            else:
                # Unicode fallback — draw piece glyph on a coloured disc
                sym = PIECE_UNICODE[piece]
                is_white_piece = piece[0] == W

                # Draw disc background for contrast
                pad   = int(sq * 0.06)
                disc_color = (240, 240, 220, 220) if is_white_piece else (30, 30, 30, 200)
                hi_draw.ellipse(
                    [x + pad, y + pad, x + sq - pad, y + sq - pad],
                    fill=disc_color
                )

                # Render the glyph
                bbox = piece_font.getbbox(sym)
                pw, ph = bbox[2] - bbox[0], bbox[3] - bbox[1]
                tx = x + (sq - pw) // 2 - bbox[0]
                ty = y + (sq - ph) // 2 - bbox[1]
                shadow_color = (0, 0, 0, 140) if is_white_piece else (255, 255, 255, 80)
                hi_draw.text((tx + 2, ty + 2), sym, font=piece_font, fill=shadow_color)
                fg = (20, 20, 20, 255) if is_white_piece else (230, 230, 230, 255)
                hi_draw.text((tx, ty), sym, font=piece_font, fill=fg)

    # File labels (below the board)
    for i, ch in enumerate(files):
        hi_draw.text(
            (margin + i * sq + sq // 2 - int(6 * SCALE),
             total - margin + int(5 * SCALE)),
            ch, font=label_font, fill=(200, 200, 200, 255),
        )
    # Rank labels (left of the board)
    for i, ch in enumerate(reversed(ranks)):
        hi_draw.text(
            (int(5 * SCALE),
             margin + i * sq + sq // 2 - int(7 * SCALE)),
            ch, font=label_font, fill=(200, 200, 200, 255),
        )

    # Downsample for antialiasing
    out_size = (total // SCALE, total // SCALE)
    return hi_img.resize(out_size, Image.LANCZOS)
