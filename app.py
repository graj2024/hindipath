"""
HindiPath – Learn Hindi (Tamil speakers via English)
Flask + SQLite  |  Python 3.10+
Run: pip install flask werkzeug requests python-dotenv && python app.py
ENV: SECRET_KEY, SARVAM_KEY
"""
import os, json, sqlite3, re, hashlib, secrets, requests
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, g, Response
import base64 as _b64

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
DB_PATH    = os.path.join(os.path.dirname(__file__), "hindipath.db")
SARVAM_KEY = os.environ.get("SARVAM_KEY", "sk_9fzeafdc_dpG4zApj9tSaXnocj9nD4we9")
SARVAM_CHAT= "https://api.sarvam.ai/v1/chat/completions"

# ── DB ─────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    my_lang TEXT DEFAULT 'tamil',
    teach_level TEXT DEFAULT 'beginner',
    onboarded INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    level TEXT NOT NULL,
    lesson_id TEXT NOT NULL,
    completed INTEGER DEFAULT 0,
    words_seen INTEGER DEFAULT 0,
    last_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, lesson_id)
);
CREATE TABLE IF NOT EXISTS achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    badge_id TEXT NOT NULL,
    earned_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, badge_id)
);
CREATE TABLE IF NOT EXISTS word_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    word_hi TEXT NOT NULL,
    lesson_id TEXT NOT NULL,
    logged_at TEXT DEFAULT (datetime('now'))
);
"""

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db: db.close()

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)
        for col in ["onboarded INTEGER DEFAULT 0"]:
            try: conn.execute(f"ALTER TABLE users ADD COLUMN {col}"); conn.commit()
            except: pass

# ── AUTH ────────────────────────────────────────
def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session: return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return wrapper

def current_user():
    if "user_id" not in session: return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

# ── BADGES ──────────────────────────────────────
BADGES = {
    "first_word":    {"icon":"🌱","name":"First Word",        "desc":"Learned your very first Hindi word"},
    "five_words":    {"icon":"🔥","name":"Five Words",         "desc":"Learned 5 Hindi words"},
    "ten_words":     {"icon":"⭐","name":"Ten Words",          "desc":"Learned 10 Hindi words"},
    "twenty_five":   {"icon":"🏅","name":"25 Words",           "desc":"Learned 25 Hindi words"},
    "fifty_words":   {"icon":"🥇","name":"50 Words",           "desc":"Learned 50 Hindi words"},
    "first_lesson":  {"icon":"📖","name":"First Lesson",       "desc":"Completed your first lesson topic"},
    "three_lessons": {"icon":"📚","name":"Three Lessons",      "desc":"Completed 3 lesson topics"},
    "intermediate":  {"icon":"🎯","name":"Going Deeper",       "desc":"Started Intermediate level"},
    "advanced":      {"icon":"🚀","name":"Advanced Learner",   "desc":"Started Advanced level"},
    "chat_10":       {"icon":"💬","name":"Chatty",             "desc":"Sent 10 messages to Gurujee"},
    "chat_50":       {"icon":"🗣️","name":"Conversationalist", "desc":"Sent 50 messages to Gurujee"},
}

def award_badge(uid, bid):
    db = get_db()
    if not db.execute("SELECT 1 FROM achievements WHERE user_id=? AND badge_id=?", (uid,bid)).fetchone():
        db.execute("INSERT INTO achievements (user_id,badge_id) VALUES (?,?)", (uid,bid))
        db.commit(); return True
    return False

def check_and_award(uid):
    db = get_db(); new = []
    wc = db.execute("SELECT COUNT(DISTINCT word_hi) FROM word_log WHERE user_id=?", (uid,)).fetchone()[0]
    for t,b in [(1,"first_word"),(5,"five_words"),(10,"ten_words"),(25,"twenty_five"),(50,"fifty_words")]:
        if wc >= t and award_badge(uid, b): new.append(b)
    lc = db.execute("SELECT COUNT(*) FROM progress WHERE user_id=? AND completed=1", (uid,)).fetchone()[0]
    for t,b in [(1,"first_lesson"),(3,"three_lessons")]:
        if lc >= t and award_badge(uid, b): new.append(b)
    cc = db.execute("SELECT COUNT(*) FROM conversations WHERE user_id=? AND role='user'", (uid,)).fetchone()[0]
    for t,b in [(10,"chat_10"),(50,"chat_50")]:
        if cc >= t and award_badge(uid, b): new.append(b)
    return new

# ── ROUTES ──────────────────────────────────────
@app.route("/")
def index():
    return redirect(url_for("landing"))

@app.route("/home")
def landing():
    if "user_id" in session: return redirect(url_for("app_page"))
    return render_template("landing.html")

@app.route("/login")
def login_page():
    if "user_id" in session: return redirect(url_for("app_page"))
    return render_template("auth.html", mode="login")

@app.route("/register")
def register_page():
    if "user_id" in session: return redirect(url_for("app_page"))
    return render_template("auth.html", mode="register")

@app.route("/app")
@login_required
def app_page():
    return render_template("app.html", user=dict(current_user()))

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("landing"))

# ── AUTH API ────────────────────────────────────
@app.route("/api/register", methods=["POST"])
def api_register():
    d = request.json or {}
    un,em,pw = d.get("username","").strip(), d.get("email","").strip().lower(), d.get("password","")
    if not un or not em or not pw: return jsonify(error="All fields required"), 400
    if len(pw) < 6: return jsonify(error="Password must be at least 6 characters"), 400
    if not re.match(r"[^@]+@[^@]+\.[^@]+", em): return jsonify(error="Invalid email"), 400
    db = get_db()
    try:
        db.execute("INSERT INTO users (username,email,password_hash) VALUES (?,?,?)", (un,em,hash_pw(pw)))
        db.commit()
        user = db.execute("SELECT * FROM users WHERE email=?", (em,)).fetchone()
        session["user_id"] = user["id"]
        return jsonify(ok=True)
    except sqlite3.IntegrityError as e:
        return jsonify(error="Username already taken" if "username" in str(e) else "Email already registered"), 409

@app.route("/api/login", methods=["POST"])
def api_login():
    d = request.json or {}
    em,pw = d.get("email","").strip().lower(), d.get("password","")
    user = get_db().execute("SELECT * FROM users WHERE email=?", (em,)).fetchone()
    if not user or user["password_hash"] != hash_pw(pw): return jsonify(error="Invalid email or password"), 401
    session["user_id"] = user["id"]
    return jsonify(ok=True)

@app.route("/api/settings", methods=["POST"])
@login_required
def api_settings():
    d = request.json or {}
    db = get_db(); uid = session["user_id"]
    fields, vals = [], []
    if "my_lang"     in d: fields.append("my_lang=?");     vals.append(d["my_lang"])
    if "teach_level" in d:
        fields.append("teach_level=?"); vals.append(d["teach_level"])
        if d["teach_level"] == "intermediate": award_badge(uid, "intermediate")
        if d["teach_level"] == "advanced":     award_badge(uid, "advanced")
    if "onboarded"   in d: fields.append("onboarded=?");   vals.append(1)
    if fields:
        vals.append(uid)
        db.execute(f"UPDATE users SET {', '.join(fields)} WHERE id=?", vals)
        db.commit()
    return jsonify(ok=True)

# ── CHAT API ────────────────────────────────────
def build_system_prompt(user_row):
    lang  = user_row["my_lang"]    or "tamil"
    level = user_row["teach_level"] or "beginner"
    lang_note = {"tamil":"The student understands Tamil natively. Always include Tamil translation.",
                 "english":"The student prefers English. Include Tamil occasionally.",
                 "both":"The student knows both Tamil and English. Use both freely."}.get(lang,"")
    level_note = {"beginner":"Teach slowly, one concept at a time, very simple sentences.",
                  "intermediate":"Teach sentences, grammar patterns, conversational phrases.",
                  "advanced":"Focus on fluency, complex sentences, idioms, grammar."}.get(level,"")
    return f"""You are "Gurujee" (गुरुजी), a warm patient Hindi tutor for Tamil speakers.

STUDENT: Tamil speaker | Level: {level} | {lang_note} | {level_note}

RULES:
1. Always show Hindi Devanagari + Roman transliteration.
2. Always give Tamil + English meaning.
3. Give PRONUNCIATION tips relating to Tamil sounds (e.g. "क sounds like க in Tamil").
4. Give MEMORY TIPS connecting to Tamil/English the student knows.
5. Correct mistakes gently.
6. Use "Shabash! (शाबाश!)" for correct answers.
7. Keep it warm and conversational.
8. End with ONE follow-up suggestion.

CRITICAL FORMAT RULES — MUST FOLLOW EXACTLY:
- Every Hindi word MUST be on its own line in this EXACT pipe format:
  नमस्ते | Namaste | Hello | வணக்கம்
- The 4 parts are: Devanagari | Roman | English | Tamil
- NO numbered lists (1. 2. 3.). NO bullet points. NO bold. NO headers.
- Pronunciation tips go in (parentheses on their own line) after the word line.
- Max 3-5 words per response.
- NEVER skip the pipe format for any Hindi word.

EXAMPLE of correct output:
मदद करो | Madad karo | Help me | உதவி செய்யுங்கள்
(க sounds like 'k', 'madad' is similar to Tamil 'உதவி' in feel)
मदद चाहिए | Madad chahiye | I need help | எனக்கு உதவி வேண்டும்
"""

@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    d = request.json or {}
    msg = (d.get("message") or "").strip()
    lesson_id = d.get("lesson_id", "")
    if not msg: return jsonify(error="Empty message"), 400
    if not SARVAM_KEY: return jsonify(error="Service not configured. Please contact admin."), 503

    db = get_db(); uid = session["user_id"]; user = current_user()
    db.execute("INSERT INTO conversations (user_id,role,content) VALUES (?,?,?)", (uid,"user",msg))
    db.commit()

    rows = db.execute("SELECT role,content FROM conversations WHERE user_id=? ORDER BY id DESC LIMIT 20", (uid,)).fetchall()
    history = [{"role":r["role"],"content":r["content"]} for r in reversed(rows)]

    try:
        res = requests.post(SARVAM_CHAT,
            headers={"Content-Type":"application/json","api-subscription-key":SARVAM_KEY},
            json={"model":"sarvam-m","messages":[{"role":"system","content":build_system_prompt(user)},*history],
                  "temperature":0.7,"max_tokens":800},
            timeout=30)
        data = res.json()
        if not res.ok:
            detail = data.get("detail",{})
            return jsonify(error=detail.get("msg",str(detail)) if isinstance(detail,dict) else str(detail)), res.status_code
        reply = data["choices"][0]["message"]["content"]
        db.execute("INSERT INTO conversations (user_id,role,content) VALUES (?,?,?)", (uid,"assistant",reply))
        db.commit()
        # Log words
        lid = lesson_id or "chat"
        for w in re.findall(r'[\u0900-\u097F]+', reply)[:10]:
            if len(w) > 1:
                try: db.execute("INSERT OR IGNORE INTO word_log (user_id,word_hi,lesson_id) VALUES (?,?,?)", (uid,w,lid))
                except: pass
        db.commit()
        if lesson_id:
            if db.execute("SELECT 1 FROM progress WHERE user_id=? AND lesson_id=?", (uid,lesson_id)).fetchone():
                db.execute("UPDATE progress SET words_seen=words_seen+1,last_at=datetime('now') WHERE user_id=? AND lesson_id=?", (uid,lesson_id))
            else:
                db.execute("INSERT INTO progress (user_id,level,lesson_id,words_seen) VALUES (?,?,?,1)",
                           (uid, user["teach_level"] or "beginner", lesson_id))
            db.commit()
        new_badges = check_and_award(uid)
        return jsonify(reply=reply, new_badges=[{"id":b,**BADGES[b]} for b in new_badges if b in BADGES])
    except requests.exceptions.Timeout:
        return jsonify(error="Gurujee is taking too long. Try again."), 504
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route("/api/chat/history")
@login_required
def api_chat_history():
    uid = session["user_id"]
    rows = get_db().execute("SELECT role,content,created_at FROM conversations WHERE user_id=? ORDER BY id ASC", (uid,)).fetchall()
    return jsonify(history=[dict(r) for r in rows])

@app.route("/api/chat/clear", methods=["POST"])
@login_required
def api_chat_clear():
    db = get_db(); db.execute("DELETE FROM conversations WHERE user_id=?", (session["user_id"],)); db.commit()
    return jsonify(ok=True)

# ── PROGRESS API ────────────────────────────────
@app.route("/api/progress")
@login_required
def api_progress():
    db = get_db(); uid = session["user_id"]
    wc = db.execute("SELECT COUNT(DISTINCT word_hi) FROM word_log WHERE user_id=?", (uid,)).fetchone()[0]
    mc = db.execute("SELECT COUNT(*) FROM conversations WHERE user_id=? AND role='user'", (uid,)).fetchone()[0]
    lessons = [dict(r) for r in db.execute("SELECT lesson_id,completed,words_seen,last_at FROM progress WHERE user_id=?", (uid,)).fetchall()]
    badges  = [{"id":b["badge_id"],"earned_at":b["earned_at"],**BADGES[b["badge_id"]]}
               for b in db.execute("SELECT badge_id,earned_at FROM achievements WHERE user_id=? ORDER BY earned_at ASC", (uid,)).fetchall()
               if b["badge_id"] in BADGES]
    return jsonify(word_count=wc, msg_count=mc, lessons=lessons, badges=badges,
                   all_badges=[{"id":k,**v} for k,v in BADGES.items()])

@app.route("/api/progress/complete_lesson", methods=["POST"])
@login_required
def api_complete_lesson():
    d = request.json or {}; lid = d.get("lesson_id","")
    if not lid: return jsonify(error="lesson_id required"), 400
    db = get_db(); uid = session["user_id"]; user = current_user()
    if db.execute("SELECT 1 FROM progress WHERE user_id=? AND lesson_id=?", (uid,lid)).fetchone():
        db.execute("UPDATE progress SET completed=1,last_at=datetime('now') WHERE user_id=? AND lesson_id=?", (uid,lid))
    else:
        db.execute("INSERT INTO progress (user_id,level,lesson_id,completed) VALUES (?,?,?,1)",
                   (uid, user["teach_level"] or "beginner", lid))
    db.commit()
    new_badges = check_and_award(uid)
    return jsonify(ok=True, new_badges=[{"id":b,**BADGES[b]} for b in new_badges if b in BADGES])

# ── TTS API ─────────────────────────────────────
@app.route("/api/tts", methods=["POST"])
@login_required
def api_tts():
    text = ((request.json or {}).get("text") or "").strip()[:500]
    if not text: return jsonify(error="No text"), 400
    if SARVAM_KEY:
        try:
            res = requests.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={"Content-Type":"application/json","api-subscription-key":SARVAM_KEY},
                json={
                    "text": text,
                    "target_language_code": "hi-IN",
                    "speaker": "shubh",
                    "model": "bulbul:v3",
                    "pace": 0.9,
                    "enable_preprocessing": True
                },
                timeout=20
            )
            print(f"[TTS] status={res.status_code} size={len(res.content)}")
            if res.ok:
                audios = res.json().get("audios", [])
                if audios:
                    ab = _b64.b64decode(audios[0])
                    return Response(ab, mimetype="audio/wav",
                                    headers={"Content-Length": str(len(ab))})
            print(f"[TTS] Sarvam error body: {res.text[:300]}")
        except Exception as e:
            print(f"[TTS] Sarvam exception: {e}")
    return jsonify(error="TTS unavailable"), 502

if __name__ == "__main__":
    init_db()
    print("\n🚀  HindiPath at http://localhost:5000\n")
    if not SARVAM_KEY: print("⚠️  SARVAM_KEY not set in .env — chat disabled\n")
    app.run(debug=True, port=5000)
