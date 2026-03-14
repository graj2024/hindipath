"""
LanguagePaths — Learn Hindi, French, or Spanish
Flask + SQLite | Python 3.10+
ENV: SECRET_KEY, SARVAM_KEY, STRIPE_SECRET_KEY, STRIPE_PUBLIC_KEY, ADMIN_PASSWORD
"""
import os, json, sqlite3, re, hashlib, secrets, requests
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, g, Response
import base64 as _b64
import stripe

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

_secret = os.environ.get("SECRET_KEY") or hashlib.sha256(b"languagepaths-fallback-2026").hexdigest()
app.secret_key = _secret
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"]   = False
app.config["PERMANENT_SESSION_LIFETIME"] = 86400 * 30

DB_PATH           = os.path.join(os.path.dirname(__file__), "languagepaths.db")
SARVAM_KEY        = os.environ.get("SARVAM_KEY", "")
SARVAM_CHAT       = "https://api.sarvam.ai/v1/chat/completions"
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLIC_KEY = os.environ.get("STRIPE_PUBLIC_KEY", "")
ADMIN_PASSWORD    = os.environ.get("ADMIN_PASSWORD", "admin2026lp")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

FREE_CREDITS = 100
VALID_PLANS  = {700: 1000, 1200: 2000}  # cents -> credits

# ── LANGUAGE CONFIG ─────────────────────────────
LANGUAGES = {
    "hindi": {
        "name": "Hindi",
        "flag": "🇮🇳",
        "color": "#FF9933",
        "tts_code": "hi-IN",
        "tts_speaker": "shubh",
        "script": "Devanagari",
        "tutor_name": "Gurujee",
        "tutor_emoji": "🧑‍🏫",
        "base_lang": "Tamil + English",
    },
    "french": {
        "name": "French",
        "flag": "🇫🇷",
        "color": "#4A90D9",
        "tts_code": "fr-FR",
        "tts_speaker": None,
        "script": "Latin",
        "tutor_name": "Professeur",
        "tutor_emoji": "👨‍🏫",
        "base_lang": "English",
    },
    "spanish": {
        "name": "Spanish",
        "flag": "🇪🇸",
        "color": "#E63946",
        "tts_code": "es-ES",
        "tts_speaker": None,
        "script": "Latin",
        "tutor_name": "Profesor",
        "tutor_emoji": "👩‍🏫",
        "base_lang": "English",
    },
}

TTS_PROVIDERS = {
    "hindi":   "sarvam",   # Sarvam bulbul:v3
    "french":  "gtts",     # Google TTS fallback
    "spanish": "gtts",
}

# ── DB ──────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    target_lang   TEXT DEFAULT 'hindi',
    teach_level   TEXT DEFAULT 'beginner',
    credits       INTEGER DEFAULT 100,
    is_admin      INTEGER DEFAULT 0,
    is_banned     INTEGER DEFAULT 0,
    onboarded     INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    lang       TEXT DEFAULT 'hindi',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS progress (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    lang       TEXT NOT NULL,
    level      TEXT NOT NULL,
    lesson_id  TEXT NOT NULL,
    completed  INTEGER DEFAULT 0,
    words_seen INTEGER DEFAULT 0,
    last_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, lang, lesson_id)
);
CREATE TABLE IF NOT EXISTS achievements (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL,
    badge_id  TEXT NOT NULL,
    lang      TEXT DEFAULT 'hindi',
    earned_at TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, badge_id, lang)
);
CREATE TABLE IF NOT EXISTS word_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL,
    word      TEXT NOT NULL,
    lang      TEXT NOT NULL,
    lesson_id TEXT NOT NULL,
    logged_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS purchases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    credits      INTEGER NOT NULL,
    stripe_id    TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);
"""

MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN target_lang TEXT DEFAULT 'hindi'",
    "ALTER TABLE users ADD COLUMN credits INTEGER DEFAULT 100",
    "ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0",
    "ALTER TABLE conversations ADD COLUMN lang TEXT DEFAULT 'hindi'",
]

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
        for m in MIGRATIONS:
            try: conn.execute(m); conn.commit()
            except: pass
        conn.execute("UPDATE users SET credits=100 WHERE credits IS NULL")
        conn.commit()

# ── AUTH ────────────────────────────────────────
def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify(error="SESSION_EXPIRED"), 401
            return redirect(url_for("login_page"))
        user = current_user()
        if user and user["is_banned"]:
            session.clear()
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return wrapper

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "admin" not in session:
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapper

def current_user():
    if "user_id" not in session: return None
    return get_db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()

def safe_credits(user):
    try:
        c = user["credits"]
        return c if c is not None else 100
    except: return 100

# ── SYSTEM PROMPTS ──────────────────────────────
def build_system_prompt(user_row):
    lang  = user_row["target_lang"] or "hindi"
    level = user_row["teach_level"] or "beginner"
    lc    = LANGUAGES.get(lang, LANGUAGES["hindi"])

    level_note = {
        "beginner":     "Teach very slowly, one concept at a time, simple vocabulary.",
        "intermediate": "Teach sentences, grammar patterns, conversational phrases.",
        "advanced":     "Focus on fluency, complex sentences, idioms and nuance.",
    }.get(level, "")

    if lang == "hindi":
        return f"""You are "Gurujee" (गुरुजी), a warm patient Hindi tutor for Tamil and English speakers.

STUDENT: Tamil + English speaker | Level: {level} | {level_note}

RULES:
1. Always show Hindi Devanagari + Roman transliteration.
2. Always give BOTH Tamil AND English meaning.
3. Give PRONUNCIATION tips relating to Tamil sounds.
4. Give memory tips connecting to Tamil/English.
5. Correct mistakes gently. Use "Shabash! (शाबाश!)" for correct answers.
6. End with ONE follow-up suggestion.

CRITICAL FORMAT — every Hindi word MUST use this exact pipe format:
नमस्ते | Namaste | Hello | வணக்கம்
(pronunciation tip in parentheses on its own line)

4 parts: Devanagari | Roman | English | Tamil
NO numbered lists. NO bullet points. NO bold. Max 3-5 words per response."""

    elif lang == "french":
        return f"""You are "Professeur", a warm and encouraging French tutor for English speakers.

STUDENT: English speaker | Level: {level} | {level_note}

RULES:
1. Always show French word + English meaning.
2. Show pronunciation guide (IPA or simple phonetic).
3. Give memory tips connecting to English cognates where possible.
4. Note any tricky pronunciation (silent letters, nasal sounds, liaisons).
5. Correct mistakes gently. Use "Très bien! (Very good!)" for correct answers.
6. End with ONE follow-up suggestion.

CRITICAL FORMAT — every French word MUST use this exact pipe format:
bonjour | bohn-ZHOOR | Hello | (the 'r' is guttural, from the back of throat)

3 parts: French | Phonetic | English
Then pronunciation note in (parentheses on its own line).
NO numbered lists. NO bullet points. NO bold. Max 3-5 words per response."""

    elif lang == "spanish":
        return f"""You are "Profesor", a warm and encouraging Spanish tutor for English speakers.

STUDENT: English speaker | Level: {level} | {level_note}

RULES:
1. Always show Spanish word + English meaning.
2. Show pronunciation guide (simple phonetic).
3. Give memory tips — Spanish has many English cognates, highlight them!
4. Note gender (el/la) for nouns, and any irregular verbs.
5. Correct mistakes gently. Use "¡Muy bien! (Very good!)" for correct answers.
6. End with ONE follow-up suggestion.

CRITICAL FORMAT — every Spanish word MUST use this exact pipe format:
hola | OH-lah | Hello | (the 'h' is always silent in Spanish)

3 parts: Spanish | Phonetic | English
Then pronunciation note in (parentheses on its own line).
NO numbered lists. NO bullet points. NO bold. Max 3-5 words per response."""

    return ""

# ── BADGES ──────────────────────────────────────
BADGES = {
    "first_word":    {"icon":"🌱","name":"First Word",       "desc":"Learned your very first word"},
    "five_words":    {"icon":"🔥","name":"Five Words",        "desc":"Learned 5 words"},
    "ten_words":     {"icon":"⭐","name":"Ten Words",         "desc":"Learned 10 words"},
    "twenty_five":   {"icon":"🏅","name":"25 Words",          "desc":"Learned 25 words"},
    "fifty_words":   {"icon":"🥇","name":"50 Words",          "desc":"Learned 50 words"},
    "first_lesson":  {"icon":"📖","name":"First Lesson",      "desc":"Completed your first lesson"},
    "three_lessons": {"icon":"📚","name":"Three Lessons",     "desc":"Completed 3 lessons"},
    "chat_10":       {"icon":"💬","name":"Chatty",            "desc":"Sent 10 messages to your tutor"},
    "chat_50":       {"icon":"🗣️","name":"Conversationalist","desc":"Sent 50 messages to your tutor"},
    "bilingual":     {"icon":"🌍","name":"Bilingual",         "desc":"Started learning a second language"},
    "trilingual":    {"icon":"🌐","name":"Trilingual",        "desc":"Started learning all 3 languages"},
}

def award_badge(uid, bid, lang="hindi"):
    db = get_db()
    if not db.execute("SELECT 1 FROM achievements WHERE user_id=? AND badge_id=? AND lang=?", (uid,bid,lang)).fetchone():
        db.execute("INSERT INTO achievements (user_id,badge_id,lang) VALUES (?,?,?)", (uid,bid,lang))
        db.commit(); return True
    return False

def check_and_award(uid, lang):
    db = get_db(); new = []
    wc = db.execute("SELECT COUNT(DISTINCT word) FROM word_log WHERE user_id=? AND lang=?", (uid,lang)).fetchone()[0]
    for t,b in [(1,"first_word"),(5,"five_words"),(10,"ten_words"),(25,"twenty_five"),(50,"fifty_words")]:
        if wc >= t and award_badge(uid, b, lang): new.append(b)
    lc = db.execute("SELECT COUNT(*) FROM progress WHERE user_id=? AND lang=? AND completed=1", (uid,lang)).fetchone()[0]
    for t,b in [(1,"first_lesson"),(3,"three_lessons")]:
        if lc >= t and award_badge(uid, b, lang): new.append(b)
    cc = db.execute("SELECT COUNT(*) FROM conversations WHERE user_id=? AND lang=? AND role='user'", (uid,lang)).fetchone()[0]
    for t,b in [(10,"chat_10"),(50,"chat_50")]:
        if cc >= t and award_badge(uid, b, lang): new.append(b)
    # Multi-language badges
    langs_used = db.execute("SELECT DISTINCT lang FROM conversations WHERE user_id=?", (uid,)).fetchall()
    if len(langs_used) >= 2: award_badge(uid, "bilingual", "all")
    if len(langs_used) >= 3: award_badge(uid, "trilingual", "all")
    return new

# ── PAGE ROUTES ─────────────────────────────────
@app.route("/")
def index():
    if "user_id" in session: return redirect(url_for("app_page"))
    return render_template("landing.html", languages=LANGUAGES)

@app.route("/app")
@login_required
def app_page():
    user = current_user()
    u = dict(user)
    u["credits"] = safe_credits(user)
    return render_template("app.html", user=u, languages=LANGUAGES,
                           lang_config=LANGUAGES.get(u.get("target_lang","hindi"), LANGUAGES["hindi"]))

@app.route("/login")
def login_page():
    if "user_id" in session: return redirect(url_for("app_page"))
    return render_template("auth.html", mode="login")

@app.route("/register")
def register_page():
    if "user_id" in session: return redirect(url_for("app_page"))
    return render_template("auth.html", mode="register")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("index"))

@app.route("/buy")
@login_required
def buy_page():
    user = current_user()
    u = {"username": user["username"], "email": user["email"], "credits": safe_credits(user)}
    return render_template("buy_credits.html", user=u, stripe_pk=STRIPE_PUBLIC_KEY)

# ── AUTH API ────────────────────────────────────
@app.route("/api/register", methods=["POST"])
def api_register():
    d = request.json or {}
    un  = d.get("username","").strip()
    em  = d.get("email","").strip().lower()
    pw  = d.get("password","")
    lng = d.get("target_lang","hindi")
    if not un or not em or not pw: return jsonify(error="All fields required"), 400
    if len(pw) < 6: return jsonify(error="Password must be at least 6 characters"), 400
    if not re.match(r"[^@]+@[^@]+\.[^@]+", em): return jsonify(error="Invalid email"), 400
    if lng not in LANGUAGES: lng = "hindi"
    db = get_db()
    try:
        db.execute("INSERT INTO users (username,email,password_hash,target_lang,credits) VALUES (?,?,?,?,?)",
                   (un, em, hash_pw(pw), lng, FREE_CREDITS))
        db.commit()
        user = db.execute("SELECT * FROM users WHERE email=?", (em,)).fetchone()
        session["user_id"] = user["id"]
        return jsonify(ok=True)
    except sqlite3.IntegrityError as e:
        return jsonify(error="Username already taken" if "username" in str(e) else "Email already registered"), 409

@app.route("/api/login", methods=["POST"])
def api_login():
    d = request.json or {}
    em, pw = d.get("email","").strip().lower(), d.get("password","")
    user = get_db().execute("SELECT * FROM users WHERE email=?", (em,)).fetchone()
    if not user or user["password_hash"] != hash_pw(pw):
        return jsonify(error="Invalid email or password"), 401
    if user["is_banned"]:
        return jsonify(error="This account has been suspended."), 403
    session["user_id"] = user["id"]
    return jsonify(ok=True)

@app.route("/api/settings", methods=["POST"])
@login_required
def api_settings():
    d = request.json or {}
    db = get_db(); uid = session["user_id"]
    fields, vals = [], []
    if "target_lang"  in d and d["target_lang"] in LANGUAGES:
        fields.append("target_lang=?"); vals.append(d["target_lang"])
    if "teach_level"  in d:
        fields.append("teach_level=?"); vals.append(d["teach_level"])
    if "onboarded"    in d:
        fields.append("onboarded=?");   vals.append(1)
    if fields:
        vals.append(uid)
        db.execute(f"UPDATE users SET {', '.join(fields)} WHERE id=?", vals)
        db.commit()
    return jsonify(ok=True)

# ── CHAT API ────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    d   = request.json or {}
    msg = (d.get("message") or "").strip()
    lid = d.get("lesson_id", "")
    if not msg: return jsonify(error="Empty message"), 400
    if not SARVAM_KEY: return jsonify(error="Service not configured. Please contact admin."), 503

    uid  = session["user_id"]
    user = current_user()
    lang = user["target_lang"] or "hindi"

    # Credits check
    row = get_db().execute("SELECT credits FROM users WHERE id=?", (uid,)).fetchone()
    user_credits = row["credits"] if row and row["credits"] is not None else 100
    if user_credits <= 0:
        return jsonify(error="NO_CREDITS"), 402

    db = get_db()
    db.execute("INSERT INTO conversations (user_id,role,content,lang) VALUES (?,?,?,?)", (uid,"user",msg,lang))
    db.commit()

    rows = db.execute(
        "SELECT role,content FROM conversations WHERE user_id=? AND lang=? ORDER BY id DESC LIMIT 20",
        (uid, lang)).fetchall()
    history = [{"role":r["role"],"content":r["content"]} for r in reversed(rows)]

    try:
        res = requests.post(SARVAM_CHAT,
            headers={"Content-Type":"application/json","api-subscription-key":SARVAM_KEY},
            json={"model":"sarvam-m",
                  "messages":[{"role":"system","content":build_system_prompt(user)},*history],
                  "temperature":0.7,"max_tokens":800},
            timeout=30)
        data = res.json()
        if not res.ok:
            detail = data.get("detail",{})
            return jsonify(error=detail.get("msg",str(detail)) if isinstance(detail,dict) else str(detail)), res.status_code
        reply = data["choices"][0]["message"]["content"]
        db.execute("INSERT INTO conversations (user_id,role,content,lang) VALUES (?,?,?,?)", (uid,"assistant",reply,lang))
        db.commit()
        # Log words
        lid2 = lid or "chat"
        pattern = r'[\u0900-\u097F]+' if lang=="hindi" else r'\b[a-zA-ZÀ-ÿ]{3,}\b'
        for w in re.findall(pattern, reply)[:10]:
            if len(w) > 1:
                try: db.execute("INSERT OR IGNORE INTO word_log (user_id,word,lang,lesson_id) VALUES (?,?,?,?)", (uid,w,lang,lid2))
                except: pass
        db.commit()
        if lid:
            if db.execute("SELECT 1 FROM progress WHERE user_id=? AND lang=? AND lesson_id=?", (uid,lang,lid)).fetchone():
                db.execute("UPDATE progress SET words_seen=words_seen+1,last_at=datetime('now') WHERE user_id=? AND lang=? AND lesson_id=?", (uid,lang,lid))
            else:
                db.execute("INSERT INTO progress (user_id,lang,level,lesson_id,words_seen) VALUES (?,?,?,?,1)",
                           (uid,lang,user["teach_level"] or "beginner",lid))
            db.commit()
        new_badges = check_and_award(uid, lang)
        return jsonify(reply=reply, new_badges=[{"id":b,**BADGES[b]} for b in new_badges if b in BADGES], lang=lang)
    except requests.exceptions.Timeout:
        return jsonify(error="Tutor is taking too long. Try again."), 504
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route("/api/chat/history")
@login_required
def api_chat_history():
    uid  = session["user_id"]
    user = current_user()
    lang = request.args.get("lang", user["target_lang"] or "hindi")
    rows = get_db().execute(
        "SELECT role,content,created_at FROM conversations WHERE user_id=? AND lang=? ORDER BY id ASC",
        (uid, lang)).fetchall()
    return jsonify(history=[dict(r) for r in rows])

@app.route("/api/chat/clear", methods=["POST"])
@login_required
def api_chat_clear():
    uid  = session["user_id"]
    user = current_user()
    lang = (request.json or {}).get("lang", user["target_lang"] or "hindi")
    db   = get_db()
    db.execute("DELETE FROM conversations WHERE user_id=? AND lang=?", (uid, lang))
    db.commit()
    return jsonify(ok=True)

# ── TTS API ─────────────────────────────────────
@app.route("/api/tts", methods=["POST"])
@login_required
def api_tts():
    d    = request.json or {}
    text = (d.get("text") or "").strip()[:500]
    lang = d.get("lang", "hindi")
    if not text: return jsonify(error="No text"), 400

    uid = session["user_id"]
    try:
        ok, _ = deduct_credit(uid)
        if not ok: return jsonify(error="NO_CREDITS"), 402
    except Exception as e:
        print(f"[CREDITS] {e}")  # fail open

    if lang == "hindi" and SARVAM_KEY:
        try:
            res = requests.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={"Content-Type":"application/json","api-subscription-key":SARVAM_KEY},
                json={"text":text,"target_language_code":"hi-IN","speaker":"shubh",
                      "model":"bulbul:v3","pace":0.9,"enable_preprocessing":True},
                timeout=20)
            if res.ok:
                audios = res.json().get("audios",[])
                if audios:
                    ab = _b64.b64decode(audios[0])
                    return Response(ab, mimetype="audio/wav", headers={"Content-Length":str(len(ab))})
        except Exception as e:
            print(f"[TTS Hindi] {e}")

    # French & Spanish — use Google TTS (works locally, may need fallback on cloud)
    if lang in ("french","spanish"):
        import urllib.parse
        tts_lang = "fr" if lang=="french" else "es"
        url = f"https://translate.googleapis.com/translate_tts?ie=UTF-8&q={urllib.parse.quote(text)}&tl={tts_lang}&client=gtx&ttsspeed=0.85"
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
            if r.ok and len(r.content) > 100:
                return Response(r.content, mimetype="audio/mpeg", headers={"Content-Length":str(len(r.content))})
        except Exception as e:
            print(f"[TTS {lang}] {e}")

    return jsonify(error="TTS unavailable"), 502

# ── PROGRESS API ────────────────────────────────
@app.route("/api/progress")
@login_required
def api_progress():
    uid  = session["user_id"]
    user = current_user()
    lang = request.args.get("lang", user["target_lang"] or "hindi")
    db   = get_db()
    wc   = db.execute("SELECT COUNT(DISTINCT word) FROM word_log WHERE user_id=? AND lang=?", (uid,lang)).fetchone()[0]
    mc   = db.execute("SELECT COUNT(*) FROM conversations WHERE user_id=? AND lang=? AND role='user'", (uid,lang)).fetchone()[0]
    lessons = [dict(r) for r in db.execute(
        "SELECT lesson_id,completed,words_seen,last_at FROM progress WHERE user_id=? AND lang=?", (uid,lang)).fetchall()]
    badges  = [{"id":b["badge_id"],"earned_at":b["earned_at"],**BADGES[b["badge_id"]]}
               for b in db.execute(
                   "SELECT badge_id,earned_at FROM achievements WHERE user_id=? AND (lang=? OR lang='all') ORDER BY earned_at ASC",
                   (uid,lang)).fetchall() if b["badge_id"] in BADGES]
    return jsonify(word_count=wc, msg_count=mc, lessons=lessons, badges=badges,
                   all_badges=[{"id":k,**v} for k,v in BADGES.items()])

@app.route("/api/progress/complete_lesson", methods=["POST"])
@login_required
def api_complete_lesson():
    d   = request.json or {}
    lid = d.get("lesson_id","")
    if not lid: return jsonify(error="lesson_id required"), 400
    uid  = session["user_id"]
    user = current_user()
    lang = user["target_lang"] or "hindi"
    db   = get_db()
    if db.execute("SELECT 1 FROM progress WHERE user_id=? AND lang=? AND lesson_id=?", (uid,lang,lid)).fetchone():
        db.execute("UPDATE progress SET completed=1,last_at=datetime('now') WHERE user_id=? AND lang=? AND lesson_id=?", (uid,lang,lid))
    else:
        db.execute("INSERT INTO progress (user_id,lang,level,lesson_id,completed) VALUES (?,?,?,?,1)",
                   (uid,lang,user["teach_level"] or "beginner",lid))
    db.commit()
    new_badges = check_and_award(uid, lang)
    return jsonify(ok=True, new_badges=[{"id":b,**BADGES[b]} for b in new_badges if b in BADGES])

# ── CREDITS ─────────────────────────────────────
def deduct_credit(uid):
    db  = get_db()
    row = db.execute("SELECT credits FROM users WHERE id=?", (uid,)).fetchone()
    if not row: return (True, 999)
    current = row["credits"]
    if current is None:
        db.execute("UPDATE users SET credits=100 WHERE id=?", (uid,))
        db.commit(); current = 100
    if current <= 0: return (False, 0)
    db.execute("UPDATE users SET credits=credits-1 WHERE id=?", (uid,))
    db.commit()
    remaining = db.execute("SELECT credits FROM users WHERE id=?", (uid,)).fetchone()["credits"] or 0
    return (True, remaining)

@app.route("/api/credits")
@login_required
def api_credits():
    uid = session["user_id"]
    row = get_db().execute("SELECT credits FROM users WHERE id=?", (uid,)).fetchone()
    return jsonify(credits=row["credits"] if row and row["credits"] is not None else 100)

@app.route("/api/buy_credits", methods=["POST"])
@login_required
def api_buy_credits():
    if not STRIPE_SECRET_KEY:
        return jsonify(error="Stripe not configured. Set STRIPE_SECRET_KEY env var."), 503
    d  = request.json or {}
    pm = d.get("payment_method_id")
    if not pm: return jsonify(error="Payment method required"), 400
    uid  = session["user_id"]
    user = current_user()
    try:
        amount_cents = int(d.get("amount_cents", 700))
        if amount_cents not in VALID_PLANS:
            return jsonify(error="Invalid plan selected"), 400
        credits_to_add = VALID_PLANS[amount_cents]
        intent = stripe.PaymentIntent.create(
            amount=amount_cents, currency="usd",
            payment_method=pm, confirm=True,
            automatic_payment_methods={"enabled":True,"allow_redirects":"never"},
            metadata={"user_id":str(uid),"username":user["username"]})
        if intent.status == "succeeded":
            db = get_db()
            db.execute("UPDATE users SET credits=credits+? WHERE id=?", (credits_to_add,uid))
            db.execute("INSERT INTO purchases (user_id,amount_cents,credits,stripe_id) VALUES (?,?,?,?)",
                       (uid,amount_cents,credits_to_add,intent.id))
            db.commit()
            new_credits = db.execute("SELECT credits FROM users WHERE id=?", (uid,)).fetchone()["credits"]
            return jsonify(ok=True, credits=new_credits,
                           message=f"Payment successful! {credits_to_add} credits added.")
        return jsonify(error=f"Payment status: {intent.status}"), 402
    except stripe.CardError as e:
        return jsonify(error=str(e.user_message)), 402
    except Exception as e:
        return jsonify(error=str(e)), 500

# ── ADMIN ────────────────────────────────────────
@app.route("/admin")
def admin_login():
    if "admin" in session: return redirect(url_for("admin_dashboard"))
    return render_template("admin.html", view="login")

@app.route("/admin/auth", methods=["POST"])
def admin_auth():
    pw = (request.json or {}).get("password","")
    if pw == ADMIN_PASSWORD:
        session["admin"] = True
        return jsonify(ok=True)
    return jsonify(error="Wrong password"), 401

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    return render_template("admin.html", view="dashboard")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login"))

@app.route("/api/admin/users")
@admin_required
def api_admin_users():
    db = get_db()
    users = db.execute("""
        SELECT u.id, u.username, u.email, u.target_lang, u.teach_level,
               u.credits, u.is_banned, u.created_at,
               COUNT(DISTINCT c.id) as msg_count,
               COALESCE(SUM(p.amount_cents),0) as total_spent_cents
        FROM users u
        LEFT JOIN conversations c ON c.user_id = u.id
        LEFT JOIN purchases p ON p.user_id = u.id
        GROUP BY u.id
        ORDER BY u.created_at DESC
    """).fetchall()
    return jsonify(users=[dict(u) for u in users])

@app.route("/api/admin/stats")
@admin_required
def api_admin_stats():
    db = get_db()
    total_users    = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active_today   = db.execute("SELECT COUNT(DISTINCT user_id) FROM conversations WHERE created_at >= date('now')").fetchone()[0]
    total_revenue  = db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM purchases").fetchone()[0]
    total_messages = db.execute("SELECT COUNT(*) FROM conversations WHERE role='user'").fetchone()[0]
    lang_breakdown = db.execute("SELECT lang, COUNT(*) as cnt FROM conversations WHERE role='user' GROUP BY lang").fetchall()
    return jsonify(
        total_users=total_users,
        active_today=active_today,
        total_revenue_cents=total_revenue,
        total_messages=total_messages,
        lang_breakdown=[dict(r) for r in lang_breakdown]
    )

@app.route("/api/admin/credits", methods=["POST"])
@admin_required
def api_admin_credits():
    d   = request.json or {}
    uid = d.get("user_id")
    amt = int(d.get("amount", 0))
    if not uid: return jsonify(error="user_id required"), 400
    db  = get_db()
    db.execute("UPDATE users SET credits=credits+? WHERE id=?", (amt, uid))
    db.commit()
    new = db.execute("SELECT credits FROM users WHERE id=?", (uid,)).fetchone()["credits"]
    return jsonify(ok=True, credits=new)

@app.route("/api/admin/ban", methods=["POST"])
@admin_required
def api_admin_ban():
    d      = request.json or {}
    uid    = d.get("user_id")
    banned = int(d.get("banned", 1))
    if not uid: return jsonify(error="user_id required"), 400
    get_db().execute("UPDATE users SET is_banned=? WHERE id=?", (banned, uid))
    get_db().commit()
    return jsonify(ok=True)

if __name__ == "__main__":
    init_db()
    print("\n🚀  LanguagePaths at http://localhost:5000\n")
    if not SARVAM_KEY: print("⚠️  SARVAM_KEY not set\n")
    app.run(debug=True, port=5000)
