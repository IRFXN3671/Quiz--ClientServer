# 🎮 TCP Multiplayer Quiz Game

A terminal-based multiplayer quiz game built with Python TCP sockets and threading.

## 📁 Files

| File | Description |
|---|---|
| `server.py` | TCP quiz server — handles clients, game loop, scoring |
| `client.py` | TCP quiz client — terminal UI for players |
| `questions.json` | 5 hardcoded quiz questions |

## ⚙️ Requirements

- Python **3.8+** (standard library only — no pip installs needed)

## 🚀 Running the Game

### Step 1 — Start the Server

Open a terminal in this directory and run:

```bash
python server.py
```

The server starts on `127.0.0.1:5000` by default.  
To use a custom host/port:

```bash
python server.py 0.0.0.0 6000
```

Expected output:
```
[SERVER] Listening on 127.0.0.1:5000  (min players to start: 2)
[SERVER] Press Ctrl+C to stop the server.
```

---

### Step 2 — Connect Clients (open separate terminals for each)

**Terminal 2:**
```bash
python client.py
```

**Terminal 3:**
```bash
python client.py
```

*(Repeat for up to 10 clients)*

To connect to a remote server:
```bash
python client.py 192.168.1.100 5000
```

Each client will be prompted for a username.  
The game starts automatically when **2 or more** players have joined.

---

## 🎯 How to Play

1. Enter your username when prompted.
2. Wait for enough players to join.
3. For each of the **5 questions**:
   - Read the question and 4 options (A / B / C / D).
   - Type your answer and press **Enter** within **10 seconds**.
4. After each question you'll see the correct answer, your score, and the leaderboard.
5. After all 5 questions the winner is announced.

### Answer Rules
- Only `A`, `B`, `C`, or `D` are accepted.
- Your **first** valid answer counts; you can't change it.
- No answer within 10 s → **0 points** for that question.

## 📊 Scoring

| Result | Points |
|---|---|
| Correct answer | **+10** |
| Wrong / no answer | **0** |

## 💡 Edge Cases

| Situation | Behaviour |
|---|---|
| Client disconnects mid-game | Removed from tracking; game continues |
| Player joins after game starts | Placed in waiting room; joins next round |
| Duplicate username | Server rejects with an error message |
| Invalid answer input | Client re-prompts (server ignores bad input) |

## 🔌 Text Protocol Reference

```
Client → Server    JOIN <username>
                   ANSWER <A|B|C|D>

Server → Client    WELCOME <message>
                   WAIT <message>
                   QUESTION <id>|<text>|A:<opt>|B:<opt>|C:<opt>|D:<opt>
                   RESULT <correct_option>
                   SCORE <score>
                   LEADERBOARD <json>
                   FINAL <winner>|<score>
                   ERROR <message>
```
