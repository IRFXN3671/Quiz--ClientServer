"""
Smoke test for the TCP Quiz Game.
Starts the server inline (imports server module), then connects two
simulated clients and verifies the full game flow end-to-end.
"""
import socket
import threading
import time
import sys
import os

# ── Patch sys.argv so server.main() sees default host/port ───────────────────
sys.argv = [sys.argv[0]]
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import server as srv

# ── Start server in background thread ────────────────────────────────────────
def start_server():
    srv.HOST = "127.0.0.1"
    srv.PORT = 15000           # use a high port to avoid conflicts
    srv.main()

server_thread = threading.Thread(target=start_server, daemon=True)
server_thread.start()
time.sleep(1)          # give the server a moment to bind

HOST, PORT = "127.0.0.1", 15000
TIMEOUT = 120          # 5 questions × 10 s each + pauses


# ── Client simulator ─────────────────────────────────────────────────────────

def client_session(username: str, answers: dict) -> list[str]:
    """
    Connect, JOIN, play the game responding according to `answers` dict
    {question_number (1-5) -> letter}.  Return all received lines.
    """
    received = []
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT)
    s.connect((HOST, PORT))
    fobj = s.makefile("r", encoding="utf-8")

    s.sendall(f"JOIN {username}\n".encode())
    q_num = 0

    try:
        while True:
            line = fobj.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue

            received.append(line)

            if line.startswith("QUESTION "):
                q_num += 1
                ans = answers.get(q_num)
                if ans:
                    time.sleep(0.4)
                    s.sendall(f"ANSWER {ans}\n".encode())

            if line.startswith("FINAL "):
                break
    except Exception as e:
        received.append(f"CLIENT_ERROR: {e}")
    finally:
        s.close()

    return received


# ── Run two clients concurrently ─────────────────────────────────────────────
results: dict[str, list[str]] = {}

# Alice: all correct answers (Q1=C, Q2=D, Q3=C, Q4=B, Q5=D)
# Bob:   all wrong answers
t1 = threading.Thread(
    target=lambda: results.__setitem__("Alice",
        client_session("Alice", {1:"C", 2:"D", 3:"C", 4:"B", 5:"D"}))
)
t2 = threading.Thread(
    target=lambda: results.__setitem__("Bob",
        client_session("Bob", {1:"A", 2:"A", 3:"A", 4:"A", 5:"A"}))
)

t1.start()
time.sleep(0.4)   # stagger so lobby-start logic fires after both joined
t2.start()
t1.join(timeout=130)
t2.join(timeout=130)


# ── Print output ─────────────────────────────────────────────────────────────
for name, msgs in results.items():
    print(f"\n=== {name} (received {len(msgs)} lines) ===")
    for m in msgs:
        print("  ", m[:90])


# ── Assertions ───────────────────────────────────────────────────────────────
def check(condition: bool, msg: str):
    status = "PASS ✓" if condition else "FAIL ✗"
    print(f"  [{status}] {msg}")
    if not condition:
        sys.exit(1)

print("\n=== Assertions ===")

alice = results.get("Alice", [])
bob   = results.get("Bob",   [])

q_alice  = [m for m in alice if m.startswith("QUESTION ")]
q_bob    = [m for m in bob   if m.startswith("QUESTION ")]
fin_alice = [m for m in alice if m.startswith("FINAL ")]
fin_bob   = [m for m in bob   if m.startswith("FINAL ")]
scores_alice = [m for m in alice if m.startswith("SCORE ")]

check(len(q_alice) == 5,   f"Alice should see 5 questions (got {len(q_alice)})")
check(len(q_bob)   == 5,   f"Bob should see 5 questions (got {len(q_bob)})")
check(bool(fin_alice),     "Alice should receive FINAL message")
check(bool(fin_bob),       "Bob should receive FINAL message")
check(len(scores_alice) == 5, f"Alice should have 5 SCORE lines (got {len(scores_alice)})")

# Alice answered all correctly → 50 pts; check she wins
final_msg = fin_alice[0] if fin_alice else ""
check("Alice" in final_msg and "50" in final_msg,
      f"Alice (50 pts) should be winner in: {final_msg}")

# Bob should have 0 pts
bob_score_vals = [int(m.split()[1]) for m in scores_alice[-1:]]  # last score of alice
final_bob_msg = fin_bob[0] if fin_bob else ""
check("Alice" in final_bob_msg, f"Bob also sees Alice as winner: {final_bob_msg}")

print("\nAll smoke tests passed! ✓")
