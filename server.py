"""
TCP Multiplayer Quiz Game - Server
===================================
Usage:  python server.py [host] [port] [--players N]
Default: 127.0.0.1 5000  (player count prompted interactively)

Protocol (text lines, UTF-8, newline-terminated):
  Client → Server : JOIN <username>
  Client → Server : ANSWER <A|B|C|D>
  Server → Client : WELCOME <message>
  Server → Client : WAIT <message>
  Server → Client : QUESTION <id>|<text>|A:<opt>|B:<opt>|C:<opt>|D:<opt>
  Server → Client : RESULT <correct_option>
  Server → Client : SCORE <score>
  Server → Client : LEADERBOARD <json>
  Server → Client : FINAL <winner>|<score>
  Server → Client : ERROR <message>
"""

import socket
import threading
import json
import time
import sys
import os
import random

# ─── Configuration ────────────────────────────────────────────────────────────
HOST          = "127.0.0.1"
PORT          = 5000
MIN_PLAYERS      = 2          # game starts when this many players have joined
ANSWER_TIMEOUT   = 10        # seconds to wait for answers per question
QUESTIONS_PER_GAME = 5      # how many questions to pick each game
QUESTIONS_FILE   = os.path.join(os.path.dirname(__file__), "questions.json")

# ─── Shared State ─────────────────────────────────────────────────────────────
lock          = threading.Lock()          # guards clients, scores, game_active
clients       = {}                        # socket → username  (active players)
scores        = {}                        # username → int
waiting_room  = {}                        # socket → username  (late joiners)
game_active   = False                     # True while a round is running

# Per-question answer collection
answers_lock  = threading.Lock()
answers       = {}                        # username → answer string or None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def send(sock: socket.socket, message: str) -> None:
    """Send a newline-terminated UTF-8 message to a single socket."""
    try:
        sock.sendall((message + "\n").encode("utf-8"))
    except Exception:
        pass  # socket already closed; handled by the reader thread


def broadcast(message: str, targets: dict = None) -> None:
    """
    Send *message* to every socket in *targets* (default: active clients).
    Dead sockets are silently skipped — removal is done by each client thread.
    """
    if targets is None:
        targets = clients
    with lock:
        sockets = list(targets.keys())
    for sock in sockets:
        send(sock, message)


def load_questions() -> list:
    """Load the full question pool and return a random sample of QUESTIONS_PER_GAME."""
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        pool = json.load(f)
    count = min(QUESTIONS_PER_GAME, len(pool))
    selected = random.sample(pool, count)
    log(f"Selected {count} questions from pool of {len(pool)}.")
    return selected


def format_leaderboard(scores_snapshot: dict) -> str:
    """Return a JSON leaderboard string sorted by score descending."""
    sorted_scores = sorted(scores_snapshot.items(), key=lambda x: x[1], reverse=True)
    return json.dumps([{"player": p, "score": s} for p, s in sorted_scores])


def remove_client(sock: socket.socket, reason: str = "") -> None:
    """Remove a client from all tracking dicts and close its socket."""
    with lock:
        username = clients.pop(sock, None)
        if username is None:
            username = waiting_room.pop(sock, None)
        if username and username in scores:
            del scores[username]
    try:
        sock.close()
    except Exception:
        pass
    if username:
        log(f"Client '{username}' removed. {reason}")
    # If everyone answered we might need to unblock the answer-collection loop
    _check_all_answered()


def _check_all_answered() -> None:
    """No-op: kept so handle_client callers don't need changing."""
    pass


def log(msg: str) -> None:
    print(f"[SERVER] {msg}", flush=True)


# ─── Client Handler Thread ────────────────────────────────────────────────────

def handle_client(sock: socket.socket, addr) -> None:
    """
    Dedicated thread per connected socket.
    Responsible for:
      1. Receiving JOIN and registering the client.
      2. Feeding incoming ANSWER lines into the shared answers dict.
      3. Cleaning up on disconnect.
    """
    global game_active

    fileobj = sock.makefile("r", encoding="utf-8")
    username = None

    try:
        # ── Step 1: Expect JOIN ──────────────────────────────────────────────
        line = fileobj.readline().strip()
        if not line.startswith("JOIN "):
            send(sock, "ERROR Expected: JOIN <username>")
            return

        username = line[5:].strip()
        if not username:
            send(sock, "ERROR Username cannot be empty")
            return

        with lock:
            # Reject duplicate usernames
            all_names = set(clients.values()) | set(waiting_room.values())
            if username in all_names:
                send(sock, f"ERROR Username '{username}' is already taken")
                return

            if game_active:
                # Late joiner → put in waiting room
                waiting_room[sock] = username
                send(sock, f"WAIT Game already in progress. Please wait for the next game.")
                log(f"Late joiner '{username}' placed in waiting room.")
            else:
                clients[sock] = username
                scores[username] = 0
                player_count = len(clients)
                send(sock, f"WELCOME {username}! You are player #{player_count}. Waiting for more players...")
                log(f"'{username}' joined. Players: {player_count}/{MIN_PLAYERS}")

                # Notify existing players a new player joined
                for s, u in list(clients.items()):
                    if s != sock:
                        send(s, f"WAIT '{username}' joined. Players: {player_count}/{MIN_PLAYERS}")

                # Start game if threshold reached
                if player_count >= MIN_PLAYERS and not game_active:
                    game_active = True
                    t = threading.Thread(target=run_game, daemon=True)
                    t.start()

        # ── Step 2: Read loop — collect ANSWER lines ─────────────────────────
        while True:
            line = fileobj.readline()
            if not line:
                break  # connection closed
            line = line.strip()
            if not line:
                continue

            if line.startswith("ANSWER "):
                option = line[7:].strip().upper()
                if option not in ("A", "B", "C", "D"):
                    send(sock, "ERROR Invalid answer. Use A, B, C, or D.")
                    continue
                with lock:
                    is_active = sock in clients
                if not is_active:
                    send(sock, "WAIT You are in the waiting room; the game has not started for you yet.")
                    continue
                with answers_lock:
                    if username not in answers:          # only first answer counts
                        answers[username] = option
                        log(f"  Answer from '{username}': {option}")
                _check_all_answered()
            else:
                send(sock, f"ERROR Unknown command: {line.split()[0]}")

    except Exception as e:
        log(f"Error in handler for '{username}': {e}")
    finally:
        remove_client(sock, "Connection closed.")


# ─── Game Loop ────────────────────────────────────────────────────────────────

def run_game() -> None:
    """
    Main game loop. Runs in its own thread.
    Broadcasts questions, collects answers with a timeout, scores, repeats.
    """
    global game_active, answers, clients, waiting_room, scores

    log("─── Game starting! ───")
    broadcast("WAIT Game is starting NOW! Get ready...")
    time.sleep(1)

    questions = load_questions()

    for idx, q in enumerate(questions):
        q_id = idx + 1

        # Format: QUESTION <id>|<text>|A:<opt>|B:<opt>|C:<opt>|D:<opt>
        opts = q["options"]
        q_msg = (
            f"QUESTION {q_id}|{q['question']}"
            f"|A:{opts['A']}|B:{opts['B']}|C:{opts['C']}|D:{opts['D']}"
        )

        # Reset answer state for new question
        with answers_lock:
            answers = {}

        log(f"Broadcasting Q{q_id}: {q['question']}")
        broadcast(q_msg)

        # Always wait the full timer — question window is fixed at ANSWER_TIMEOUT
        log(f"  Timer started ({ANSWER_TIMEOUT}s)...")
        time.sleep(ANSWER_TIMEOUT)
        log(f"  Timer expired — collecting answers.")

        correct = q["answer"]
        log(f"  Correct answer: {correct}")

        # Score and send result + individual score to each active client
        with lock:
            current_clients = dict(clients)

        for sock, uname in current_clients.items():
            with answers_lock:
                submitted = answers.get(uname)
            if submitted == correct:
                with lock:
                    if uname in scores:
                        scores[uname] += 10
            result_msg = f"RESULT {correct}"
            send(sock, result_msg)
            with lock:
                player_score = scores.get(uname, 0)
            send(sock, f"SCORE {player_score}")

        # Broadcast leaderboard after every question
        with lock:
            scores_snapshot = dict(scores)
        lb = format_leaderboard(scores_snapshot)
        broadcast(f"LEADERBOARD {lb}")
        log(f"  Leaderboard: {lb}")

        time.sleep(3)  # brief pause so clients can read leaderboard

    # ── End of game ──────────────────────────────────────────────────────────
    with lock:
        scores_snapshot = dict(scores)

    if scores_snapshot:
        winner, top_score = max(scores_snapshot.items(), key=lambda x: x[1])
    else:
        winner, top_score = "Nobody", 0

    log(f"─── Game over! Winner: {winner} with {top_score} pts ───")
    broadcast(f"FINAL {winner}|{top_score}")

    # ── Close all connections and stop ───────────────────────────────────────
    time.sleep(2)   # let FINAL reach clients before we close
    log("Closing all client connections. Game session ended.")
    with lock:
        all_sockets = list(clients.keys()) + list(waiting_room.keys())
    for sock in all_sockets:
        try:
            sock.close()
        except Exception:
            pass
    with lock:
        clients.clear()
        waiting_room.clear()
        scores.clear()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global HOST, PORT, MIN_PLAYERS

    # ── Parse positional args: [host] [port] [--players N] ───────────────────
    args = sys.argv[1:]
    players_from_cli = None
    i = 0
    positional = []
    while i < len(args):
        if args[i] == "--players" and i + 1 < len(args):
            players_from_cli = int(args[i + 1])
            i += 2
        else:
            positional.append(args[i])
            i += 1
    if len(positional) >= 1:
        HOST = positional[0]
    if len(positional) >= 2:
        PORT = int(positional[1])

    # ── Ask how many players are needed to start ──────────────────────────────
    if players_from_cli is not None:
        MIN_PLAYERS = players_from_cli
    else:
        print("=" * 50)
        print("  TCP Multiplayer Quiz Game — Server Setup")
        print("=" * 50)
        while True:
            try:
                raw = input(f"  How many players needed to start the game? [default: {MIN_PLAYERS}]: ").strip()
                if raw == "":
                    break          # keep default
                value = int(raw)
                if value < 1:
                    print("  Please enter a number >= 1.")
                    continue
                MIN_PLAYERS = value
                break
            except ValueError:
                print("  Invalid input — please enter a whole number.")

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(16)
    log(f"Listening on {HOST}:{PORT}  (waiting for {MIN_PLAYERS} player(s) to start)")
    log("Press Ctrl+C to stop the server.")

    try:
        while True:
            conn, addr = server_sock.accept()
            log(f"New connection from {addr}")
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        log("Server shutting down.")
    finally:
        server_sock.close()


if __name__ == "__main__":
    main()
