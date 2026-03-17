"""
TCP Multiplayer Quiz Game - Client
====================================
Usage:  python client.py [host] [port]
Default: 127.0.0.1 5000

The client runs two threads:
  • Receiver thread  – continuously reads lines from the server and prints them.
  • Main thread      – handles user input (answer entry) during question windows.
"""

import socket
import threading
import sys
import time


# ─── Configuration ────────────────────────────────────────────────────────────
HOST           = "127.0.0.1"
PORT           = 5000
ANSWER_TIMEOUT = 10   # must match server's ANSWER_TIMEOUT

# ─── Globals ──────────────────────────────────────────────────────────────────
answer_window_open = threading.Event()   # set while a question is active
answer_submitted   = threading.Event()   # set once user submits answer this round
current_question   = None               # last QUESTION message received
sock_global        = None
running            = True
countdown_stop     = threading.Event()   # set to cancel the countdown early


# ─── Helpers ──────────────────────────────────────────────────────────────────

def send(sock: socket.socket, message: str) -> None:
    try:
        sock.sendall((message + "\n").encode("utf-8"))
    except Exception:
        pass


def print_separator(char="─", width=60):
    print(char * width)


def print_question(raw: str) -> None:
    """
    Parse and pretty-print a QUESTION message.
    Format: QUESTION <id>|<text>|A:<opt>|B:<opt>|C:<opt>|D:<opt>
    """
    # Strip the "QUESTION " prefix
    parts = raw[len("QUESTION "):].split("|")
    if len(parts) < 6:
        print(f"\n[?] {raw}")
        return
    q_id   = parts[0]
    q_text = parts[1]
    opts   = {p[0]: p[2:] for p in parts[2:]}   # e.g. "A:Berlin" → A → Berlin

    print_separator("═")
    print(f"  Question {q_id}: {q_text}")
    print_separator()
    for letter in ("A", "B", "C", "D"):
        print(f"    [{letter}]  {opts.get(letter, '?')}")
    print_separator()
    print("  ⏱  You have 10 seconds to answer. Type A / B / C / D and press Enter.")
    print_separator()


def countdown_timer(seconds: int = 10) -> None:
    """Ticks down from *seconds* to 0, printing each second. Stops early if
    countdown_stop is set (e.g. when RESULT arrives or client exits)."""
    for remaining in range(seconds, -1, -1):
        if countdown_stop.is_set():
            return
        bar = "█" * remaining + "░" * (seconds - remaining)
        # \r overwrites the same line; flush so it shows immediately
        print(f"\r  ⏳  [{bar}] {remaining:2d}s remaining  ", end="", flush=True)
        if remaining == 0:
            print()   # newline when timer hits 0
            return
        countdown_stop.wait(timeout=1)


def print_leaderboard(raw: str) -> None:
    """
    Parse and pretty-print a LEADERBOARD message.
    Format: LEADERBOARD [{"player":"X","score":N}, ...]
    """
    import json
    try:
        data = json.loads(raw[len("LEADERBOARD "):])
    except Exception:
        print(f"  Leaderboard: {raw}")
        return

    print("\n  🏆 Leaderboard")
    print_separator("-", 40)
    for rank, entry in enumerate(data, 1):
        medal = ["🥇", "🥈", "🥉"][rank - 1] if rank <= 3 else f"  {rank}."
        print(f"  {medal}  {entry['player']:20s}  {entry['score']} pts")
    print_separator("-", 40)


# ─── Receiver Thread ──────────────────────────────────────────────────────────

def receiver(sock: socket.socket) -> None:
    """Reads lines from the server and reacts to each message type."""
    global running, answer_window_open, answer_submitted

    fileobj = sock.makefile("r", encoding="utf-8")
    try:
        while running:
            line = fileobj.readline()
            if not line:
                print("\n[!] Connection closed by server.")
                running = False
                answer_window_open.set()   # unblock main thread if waiting
                answer_submitted.set()
                break
            line = line.strip()
            if not line:
                continue

            # ── Route each message type ──────────────────────────────────────
            if line.startswith("WELCOME "):
                print(f"\n✅  {line[8:]}\n")

            elif line.startswith("WAIT "):
                print(f"\n⏳  {line[5:]}")

            elif line.startswith("QUESTION "):
                # Cancel any previous countdown, start a fresh one
                countdown_stop.set()
                time.sleep(0.05)          # let previous timer thread exit
                countdown_stop.clear()
                answer_submitted.clear()
                answer_window_open.set()
                print_question(line)
                threading.Thread(
                    target=countdown_timer,
                    args=(ANSWER_TIMEOUT,),
                    daemon=True
                ).start()

            elif line.startswith("RESULT "):
                correct = line[7:].strip()
                countdown_stop.set()          # stop the countdown display
                answer_window_open.clear()    # close answer window
                answer_submitted.set()        # unblock input loop if waiting
                print(f"\n  ✔  Correct answer: [{correct}]")

            elif line.startswith("SCORE "):
                score = line[6:].strip()
                print(f"  📊  Your score: {score} pts")

            elif line.startswith("LEADERBOARD "):
                print_leaderboard(line)

            elif line.startswith("FINAL "):
                payload = line[6:].strip()
                winner, score = payload.rsplit("|", 1) if "|" in payload else (payload, "?")
                print_separator("═")
                print(f"\n  🎉  GAME OVER!")
                print(f"  🏆  Winner: {winner}  |  Score: {score} pts")
                print_separator("═")
                print("  Thanks for playing! See you next time. 👋")
                running = False
                answer_window_open.set()   # unblock input loop so it can exit
                answer_submitted.set()
                break

            elif line.startswith("ERROR "):
                print(f"\n  ❌  Server error: {line[6:]}")

            else:
                print(f"\n  [Server] {line}")

    except Exception as e:
        if running:
            print(f"\n[!] Receiver error: {e}")
        running = False
        answer_window_open.set()
        answer_submitted.set()


# ─── Input Loop ───────────────────────────────────────────────────────────────

def input_loop(sock: socket.socket) -> None:
    """
    Waits for the answer window to open (QUESTION received), then prompts
    the user for a valid A/B/C/D input and sends it exactly once.
    """
    global running

    while running:
        # Block until a question arrives
        answer_window_open.wait()
        if not running:
            break

        if not answer_window_open.is_set():
            # Window was already closed (result came in before we checked)
            continue

        # Keep prompting until a valid answer is submitted or window closes
        answered = False
        while answer_window_open.is_set() and running:
            try:
                raw = input("  Your answer: ").strip().upper()
            except (EOFError, KeyboardInterrupt):
                running = False
                break

            if raw not in ("A", "B", "C", "D"):
                print("  ⚠  Invalid input. Please enter A, B, C, or D.")
                continue

            if not answer_window_open.is_set():
                print("  ⚠  Time's up! Answer not submitted.")
                break

            send(sock, f"ANSWER {raw}")
            print(f"  ✅  Answer [{raw}] submitted. Waiting for result...")
            answered = True
            break

        # Wait until result comes back before next iteration
        answer_submitted.wait()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global HOST, PORT, sock_global, running

    if len(sys.argv) >= 2:
        HOST = sys.argv[1]
    if len(sys.argv) >= 3:
        PORT = int(sys.argv[2])

    print_separator("═")
    print("  🎮  TCP Multiplayer Quiz Game – Client")
    print_separator("═")

    # Get username
    username = ""
    while not username.strip():
        username = input("  Enter your username: ").strip()
        if not username:
            print("  Username cannot be empty.")

    # Connect
    print(f"\n  Connecting to {HOST}:{PORT} ...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((HOST, PORT))
        sock_global = sock
    except ConnectionRefusedError:
        print(f"  ❌  Could not connect to {HOST}:{PORT}. Is the server running?")
        sys.exit(1)

    print(f"  Connected! Sending JOIN request as '{username}'...")

    # Send JOIN
    send(sock, f"JOIN {username}")

    # Start receiver thread
    recv_thread = threading.Thread(target=receiver, args=(sock,), daemon=True)
    recv_thread.start()

    # Run input loop in main thread
    try:
        input_loop(sock)
    except KeyboardInterrupt:
        pass

    running = False
    print("\n  Goodbye! 👋")
    try:
        sock.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
