"""
LanguagePaths — Learn Hindi, French, or Spanish
Flask + Supabase PostgreSQL | Python 3.10+
ENV: SECRET_KEY, DATABASE_URL, SARVAM_KEY, STRIPE_SECRET_KEY, STRIPE_PUBLIC_KEY, ADMIN_PASSWORD
"""
import os, re, hashlib, requests
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, g, Response
import base64 as _b64
try:
    import psycopg2, psycopg2.extras
    HAS_PG = True
except ImportError:
    psycopg2 = None
    HAS_PG = False
    print("[WARNING] psycopg2 not installed. Run: pip install psycopg2-binary")

try:
    import stripe
except ImportError:
    stripe = None

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

DATABASE_URL      = os.environ.get("DATABASE_URL", "")
SARVAM_KEY        = os.environ.get("SARVAM_KEY", "")
SARVAM_CHAT       = "https://api.sarvam.ai/v1/chat/completions"
CLAUDE_KEY        = os.environ.get("CLAUDE_API_KEY", "")
SUPABASE_URL      = os.environ.get("SUPABASE_URL", "")  # e.g. https://xxx.supabase.co
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
APP_URL           = os.environ.get("APP_URL", "https://your-app.railway.app")

# Supabase Auth client (for email confirmation only)
_supabase_client = None
def get_supabase():
    global _supabase_client
    if _supabase_client is None and SUPABASE_URL and SUPABASE_ANON_KEY:
        try:
            from supabase import create_client
            _supabase_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
        except Exception as e:
            print(f"[SUPABASE] init error: {e}")
    return _supabase_client
CLAUDE_API_URL    = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL      = "claude-haiku-4-5-20251001"  # fast + cheap for language tutoring
FROM_EMAIL        = os.environ.get("FROM_EMAIL", "noreply@languagepaths.com")
APP_URL           = os.environ.get("APP_URL", "https://languagepaths.up.railway.app")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PUBLIC_KEY = os.environ.get("STRIPE_PUBLIC_KEY", "")
ADMIN_PASSWORD    = os.environ.get("ADMIN_PASSWORD", "admin2026lp")

if STRIPE_SECRET_KEY and stripe:
    stripe.api_key = STRIPE_SECRET_KEY

FREE_CREDITS = 100
VALID_PLANS  = {700: 1000, 1200: 2000}
CREDITS_PER_CHAT        = 0
CREDITS_PER_Q_TRANSLATE = 2

LANGUAGES = {
    "hindi":   {"name":"Hindi",  "flag":"🇮🇳","color":"#FF9933","tts_code":"hi-IN","tts_speaker":"shubh","script":"Devanagari","tutor_name":"Gurujee",    "tutor_emoji":"🧑\u200d🏫","base_lang":"Tamil + English"},
    "french":  {"name":"French", "flag":"🇫🇷","color":"#4A90D9","tts_code":"fr-FR","tts_speaker":None,  "script":"Latin",      "tutor_name":"Professeur","tutor_emoji":"👨\u200d🏫","base_lang":"English"},
    "spanish": {"name":"Spanish","flag":"🇪🇸","color":"#E63946","tts_code":"es-ES","tts_speaker":None,  "script":"Latin",      "tutor_name":"Profesor",  "tutor_emoji":"👩\u200d🏫","base_lang":"English"},
}

BADGES = {
    "first_word":    {"icon":"🌱","name":"First Word",       "desc":"Learned your very first word"},
    "five_words":    {"icon":"🔥","name":"Five Words",        "desc":"Learned 5 words"},
    "ten_words":     {"icon":"⭐","name":"Ten Words",         "desc":"Learned 10 words"},
    "twenty_five":   {"icon":"🏅","name":"25 Words",          "desc":"Learned 25 words"},
    "fifty_words":   {"icon":"🥇","name":"50 Words",          "desc":"Learned 50 words"},
    "first_lesson":  {"icon":"📖","name":"First Lesson",      "desc":"Completed your first lesson"},
    "three_lessons": {"icon":"📚","name":"Three Lessons",     "desc":"Completed 3 lessons"},
    "chat_10":       {"icon":"💬","name":"Chatty",            "desc":"Sent 10 messages to your tutor"},
    "chat_50":       {"icon":"🗣️","name":"Conversationalist","desc":"Sent 50 messages"},
    "bilingual":     {"icon":"🌍","name":"Bilingual",         "desc":"Learning a second language"},
    "trilingual":    {"icon":"🌐","name":"Trilingual",        "desc":"Learning all 3 languages"},
}

SCHEMA_STMTS = [
    """CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL, target_lang TEXT DEFAULT 'hindi',
        teach_level TEXT DEFAULT 'beginner', credits INTEGER DEFAULT 100,
        is_admin INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0,
        onboarded INTEGER DEFAULT 0, email_verified INTEGER DEFAULT 0,
        verify_token TEXT, created_at TIMESTAMP DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS conversations (
        id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, role TEXT NOT NULL,
        content TEXT NOT NULL, lang TEXT DEFAULT 'hindi', created_at TIMESTAMP DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS progress (
        id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, lang TEXT NOT NULL,
        level TEXT NOT NULL, lesson_id TEXT NOT NULL, completed INTEGER DEFAULT 0,
        words_seen INTEGER DEFAULT 0, last_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(user_id, lang, lesson_id))""",
    """CREATE TABLE IF NOT EXISTS achievements (
        id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, badge_id TEXT NOT NULL,
        lang TEXT DEFAULT 'hindi', earned_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(user_id, badge_id, lang))""",
    """CREATE TABLE IF NOT EXISTS word_log (
        id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, word TEXT NOT NULL,
        lang TEXT NOT NULL, lesson_id TEXT NOT NULL, logged_at TIMESTAMP DEFAULT NOW(),
        UNIQUE(user_id, word, lang, lesson_id))""",
    """CREATE TABLE IF NOT EXISTS purchases (
        id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
        credits INTEGER NOT NULL, stripe_id TEXT, created_at TIMESTAMP DEFAULT NOW())""",
]

def get_db():
    if "db" not in g:
        if not HAS_PG:
            raise RuntimeError("psycopg2 not installed — add psycopg2-binary to requirements.txt")
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL not set — add your Supabase connection string to Railway env vars")
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False
        g.db = conn
    return g.db

def q(sql, params=(), one=False, many=False, commit=False):
    conn = get_db(); cur = conn.cursor()
    cur.execute(sql, params)
    res = None
    if one:  res = cur.fetchone()
    if many: res = cur.fetchall()
    if commit: conn.commit()
    cur.close()
    return res

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        try: db.close()
        except: pass

def init_db():
    if not DATABASE_URL: return
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur  = conn.cursor()
    for stmt in SCHEMA_STMTS:
        try: cur.execute(stmt)
        except Exception as e: print(f"[SCHEMA] {e}"); conn.rollback()
    for m in [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS target_lang TEXT DEFAULT 'hindi'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS credits INTEGER DEFAULT 100",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_banned INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS verify_token TEXT",
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS lang TEXT DEFAULT 'hindi'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_confirmed INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS confirm_token TEXT",
    ]:
        try: cur.execute(m)
        except: pass
    try:
        cur.execute("UPDATE users SET credits=100 WHERE credits IS NULL")
        conn.commit()
    except: conn.rollback()
    cur.close(); conn.close()
    print("[DB] Supabase ready")

def hash_pw(pw): return hashlib.sha256(pw.encode()).hexdigest()

import secrets as _secrets

def send_verification_email(email, username, token):
    """Send confirmation email via Resend."""
    if not RESEND_API_KEY:
        print(f"[EMAIL] RESEND_API_KEY not set — skipping email for {email}")
        return False
    verify_url = f"{APP_URL}/verify-email?token={token}"
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#0A0A0F;color:#F0EEE8;border-radius:16px">
      <h1 style="font-size:24px;margin-bottom:8px">Welcome to LanguagePaths! 🌍</h1>
      <p style="color:#8A8899;margin-bottom:24px">Hi {username}, confirm your email to start learning.</p>
      <a href="{verify_url}" style="display:inline-block;background:#FF9933;color:#000;font-weight:700;padding:14px 28px;border-radius:10px;text-decoration:none;font-size:16px">
        Confirm Email →
      </a>
      <p style="color:#8A8899;font-size:12px;margin-top:24px">Link expires in 24 hours. If you didn't sign up, ignore this email.</p>
    </div>"""
    try:
        r = requests.post("https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": email,
                  "subject": "Confirm your LanguagePaths account",
                  "html": html},
            timeout=10)
        if r.ok:
            print(f"[EMAIL] Sent to {email}")
            return True
        print(f"[EMAIL] Failed: {r.status_code} {r.text[:200]}")
        return False
    except Exception as e:
        print(f"[EMAIL] Error: {e}")
        return False

def login_required(f):
    @wraps(f)
    def w(*a,**k):
        if "user_id" not in session:
            if request.path.startswith("/api/"): return jsonify(error="SESSION_EXPIRED"), 401
            return redirect(url_for("login_page"))
        user = current_user()
        if user and user.get("is_banned"): session.clear(); return redirect(url_for("login_page"))
        return f(*a,**k)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a,**k):
        if "admin" not in session: return redirect(url_for("admin_login"))
        return f(*a,**k)
    return w

def current_user():
    if "user_id" not in session: return None
    return q("SELECT * FROM users WHERE id=%s", (session["user_id"],), one=True)

def safe_credits(user):
    try: c = user["credits"]; return c if c is not None else 100
    except: return 100

def build_system_prompt(user_row):
    lang  = (user_row or {}).get("target_lang") or "hindi"
    level = (user_row or {}).get("teach_level") or "beginner"
    ln = {"beginner":"Teach very slowly, simple vocabulary.","intermediate":"Teach sentences and conversation.","advanced":"Focus on fluency and idioms."}.get(level,"")
    if lang == "hindi":
        return f"""You are Gurujee, a warm Hindi tutor for Tamil and English speakers.
Level: {level}. {ln}
CRITICAL FORMAT - every Hindi word on its own line:
नमस्ते | Namaste | Hello | வணக்கம்
(pronunciation tip)
Parts: Devanagari | Roman | English | Tamil. NO lists. NO bold. Max 3-5 words."""
    elif lang == "french":
        return f"""You are Professeur, a warm French tutor for English speakers. Level: {level}. {ln}
YOU MUST USE PIPE FORMAT FOR EVERY WORD. Example:
bonjour | bohn-ZHOOR | Hello
merci | mehr-SEE | Thank you
Each word on its own line: French | Phonetic | English
Next line: (pronunciation tip in parentheses)
NEVER write word and meaning on separate lines without pipes.
NEVER use numbered lists or bullet points. Max 4 words."""
    elif lang == "spanish":
        return f"""You are Profesor, a warm Spanish tutor for English speakers. Level: {level}. {ln}
YOU MUST USE PIPE FORMAT FOR EVERY WORD. Example:
hola | OH-lah | Hello
gracias | GRAH-see-ahs | Thank you
Each word on its own line: Spanish | Phonetic | English
Next line: (pronunciation tip in parentheses)
NEVER write word and meaning on separate lines without pipes.
NEVER use numbered lists or bullet points. Max 4 words."""
    return ""

import secrets as _secrets

def is_q(msg): return bool(re.match(r'^[Qq]\s*:\s*.+', msg.strip()))
def extract_q(msg):
    m = re.match(r'^[Qq]\s*:\s*["\']?(.+?)["\']?\s*$', msg.strip())
    return m.group(1).strip() if m else msg

def deduct_credit(uid, amount=1):
    row = q("SELECT credits FROM users WHERE id=%s", (uid,), one=True)
    if not row: return (True, 999)
    current = row["credits"] or 0
    if current < amount: return (False, current)
    q("UPDATE users SET credits=credits-%s WHERE id=%s", (amount, uid), commit=True)
    remaining = (q("SELECT credits FROM users WHERE id=%s", (uid,), one=True) or {}).get("credits", 0)
    return (True, remaining)

@app.route("/health")
def health():
    """Railway health check — no DB needed."""
    return "OK", 200

@app.route("/")
def index():
    if "user_id" in session: return redirect(url_for("app_page"))
    return render_template("landing.html", languages=LANGUAGES)

@app.route("/app")
@login_required
def app_page():
    user = current_user(); u = dict(user)
    u["credits"] = safe_credits(user)
    u["email_confirmed"] = u.get("email_confirmed", 0)
    return render_template("app.html", user=u, languages=LANGUAGES,
                           lang_config=LANGUAGES.get(u.get("target_lang","hindi"), LANGUAGES["hindi"]))

@app.route("/verify-email")
def verify_email():
    token = request.args.get("token","")
    if not token:
        return render_template("verify.html", status="invalid")
    user = q("SELECT id, username, email_verified FROM users WHERE verify_token=%s", (token,), one=True)
    if not user:
        return render_template("verify.html", status="invalid")
    if user["email_verified"]:
        return render_template("verify.html", status="already")
    q("UPDATE users SET email_verified=1, verify_token=NULL WHERE id=%s", (user["id"],), commit=True)
    return render_template("verify.html", status="success", username=user["username"])

@app.route("/api/resend-verification", methods=["POST"])
@login_required
def api_resend_verification():
    user = current_user()
    if user.get("email_verified"):
        return jsonify(error="Email already verified"), 400
    token = _secrets.token_urlsafe(32)
    q("UPDATE users SET verify_token=%s WHERE id=%s", (token, user["id"]), commit=True)
    sent = send_verification_email(user["email"], user["username"], token)
    return jsonify(ok=sent, message="Verification email sent!" if sent else "Failed to send email.")

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
    u = {"username":user["username"],"email":user["email"],"credits":safe_credits(user)}
    return render_template("buy_credits.html", user=u, stripe_pk=STRIPE_PUBLIC_KEY)

@app.route("/api/register", methods=["POST"])
def api_register():
    d = request.json or {}
    un, em, pw, lng = d.get("username","").strip(), d.get("email","").strip().lower(), d.get("password",""), d.get("target_lang","hindi")
    if not un or not em or not pw: return jsonify(error="All fields required"), 400
    if len(pw) < 6: return jsonify(error="Password must be at least 6 characters"), 400
    if not re.match(r"[^@]+@[^@]+\.[^@]+", em): return jsonify(error="Invalid email"), 400
    if lng not in LANGUAGES: lng = "hindi"
    try:
        # Create user in our DB first
        user = q("INSERT INTO users (username,email,password_hash,target_lang,credits,email_confirmed) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                 (un,em,hash_pw(pw),lng,FREE_CREDITS,0), one=True, commit=True)
        session["user_id"] = user["id"]

        # Register in Supabase Auth for email confirmation
        sb = get_supabase()
        if sb:
            try:
                sb.auth.sign_up({
                    "email": em,
                    "password": pw,
                    "options": {
                        "email_redirect_to": f"{APP_URL}/confirm",
                        "data": {"username": un, "lp_user_id": str(user["id"])}
                    }
                })
                print(f"[AUTH] Supabase signup sent confirmation to {em}")
            except Exception as e:
                print(f"[AUTH] Supabase signup error (non-fatal): {e}")

        return jsonify(ok=True, email_sent=bool(sb))
    except psycopg2.errors.UniqueViolation:
        get_db().rollback(); return jsonify(error="Username or email already registered"), 409
    except Exception as e:
        try: get_db().rollback()
        except: pass
        return jsonify(error=str(e)), 500

@app.route("/api/login", methods=["POST"])
def api_login():
    d = request.json or {}
    em, pw = d.get("email","").strip().lower(), d.get("password","")
    user = q("SELECT * FROM users WHERE email=%s", (em,), one=True)
    if not user or user["password_hash"] != hash_pw(pw): return jsonify(error="Invalid email or password"), 401
    if user.get("is_banned"): return jsonify(error="Account suspended."), 403
    session["user_id"] = user["id"]
    return jsonify(ok=True)

@app.route("/api/settings", methods=["POST"])
@login_required
def api_settings():
    d = request.json or {}; uid = session["user_id"]
    fields, vals = [], []
    if "target_lang" in d and d["target_lang"] in LANGUAGES: fields.append("target_lang=%s"); vals.append(d["target_lang"])
    if "teach_level" in d: fields.append("teach_level=%s"); vals.append(d["teach_level"])
    if "onboarded" in d: fields.append("onboarded=%s"); vals.append(1)
    if fields: vals.append(uid); q(f"UPDATE users SET {','.join(fields)} WHERE id=%s", vals, commit=True)
    return jsonify(ok=True)

@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    d = request.json or {}
    msg = (d.get("message") or "").strip()
    lid = d.get("lesson_id","")
    if not msg: return jsonify(error="Empty message"), 400
    if not SARVAM_KEY: return jsonify(error="Service not configured."), 503
    uid  = session["user_id"]; user = current_user()
    lang = user.get("target_lang") or "hindi"
    q_mode = is_q(msg)
    credits_needed = CREDITS_PER_Q_TRANSLATE if q_mode else CREDITS_PER_CHAT
    user_credits = (q("SELECT credits FROM users WHERE id=%s",(uid,),one=True) or {}).get("credits",100) or 100
    if credits_needed > 0 and user_credits < credits_needed: return jsonify(error="NO_CREDITS"), 402
    ai_msg = f'Translate and teach using ONLY the pipe format: "{extract_q(msg)}"' if q_mode else msg
    q("INSERT INTO conversations (user_id,role,content,lang) VALUES (%s,%s,%s,%s)", (uid,"user",msg,lang), commit=True)
    rows = q("SELECT role,content FROM conversations WHERE user_id=%s AND lang=%s ORDER BY id DESC LIMIT 20", (uid,lang), many=True) or []
    history = [{"role":r["role"],"content":r["content"]} for r in reversed(rows)]
    if q_mode and history: history[-1]["content"] = ai_msg
    try:
        system_prompt = build_system_prompt(user)
        # Route: Hindi → Sarvam, French/Spanish → Claude (better format compliance)
        def call_sarvam():
            """Call Sarvam API, return (reply, error) tuple."""
            r = requests.post(SARVAM_CHAT,
                headers={"Content-Type":"application/json","api-subscription-key":SARVAM_KEY},
                json={"model":"sarvam-m",
                      "messages":[{"role":"system","content":system_prompt},*history],
                      "temperature":0.7,"max_tokens":1000},timeout=30)
            d = r.json()
            if not r.ok:
                detail = d.get("detail",{})
                msg = detail.get("msg","") if isinstance(detail,dict) else str(detail)
                return None, msg or f"Sarvam error {r.status_code}"
            text = d.get("choices",[{}])[0].get("message",{}).get("content","").strip()
            if not text:
                return None, "Empty response from Sarvam"
            return text, None

        def call_claude():
            """Call Claude API, return (reply, error) tuple."""
            r = requests.post(CLAUDE_API_URL,
                headers={"Content-Type":"application/json",
                         "x-api-key":CLAUDE_KEY,
                         "anthropic-version":"2023-06-01"},
                json={"model":CLAUDE_MODEL,
                      "system":system_prompt,
                      "messages":history,
                      "max_tokens":1000,
                      "temperature":0.7},
                timeout=30)
            d = r.json()
            if not r.ok:
                return None, d.get("error",{}).get("message","Claude API error")
            text = (d.get("content",[{}])[0].get("text","")).strip()
            if not text:
                return None, "Empty response from Claude"
            return text, None

        reply = None
        if CLAUDE_KEY:
            # Use Claude for all languages — reliable format compliance
            reply, err = call_claude()
            if not reply:
                # Fall back to Sarvam if Claude fails
                if SARVAM_KEY:
                    print(f"[CHAT] Claude failed ({err}), falling back to Sarvam")
                    reply, err = call_sarvam()
                if not reply:
                    return jsonify(error=err or "AI service unavailable"), 503
        elif SARVAM_KEY:
            # No Claude key — use Sarvam
            reply, err = call_sarvam()
            if not reply:
                return jsonify(error=err or "Sarvam unavailable"), 503
        else:
            return jsonify(error="No AI service configured."), 503
        q("INSERT INTO conversations (user_id,role,content,lang) VALUES (%s,%s,%s,%s)", (uid,"assistant",reply,lang), commit=True)
        pattern = r'[\u0900-\u097F]+' if lang=="hindi" else r'\b[a-zA-ZÀ-ÿ]{3,}\b'
        for w in re.findall(pattern, reply)[:10]:
            if len(w) > 1:
                try: q("INSERT INTO word_log (user_id,word,lang,lesson_id) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (uid,w,lang,lid or "chat"), commit=True)
                except: pass
        if lid:
            if q("SELECT 1 FROM progress WHERE user_id=%s AND lang=%s AND lesson_id=%s",(uid,lang,lid),one=True):
                q("UPDATE progress SET words_seen=words_seen+1,last_at=NOW() WHERE user_id=%s AND lang=%s AND lesson_id=%s",(uid,lang,lid),commit=True)
            else:
                q("INSERT INTO progress (user_id,lang,level,lesson_id,words_seen) VALUES (%s,%s,%s,%s,1)",(uid,lang,user.get("teach_level","beginner"),lid),commit=True)
        if credits_needed > 0:
            try: deduct_credit(uid, credits_needed)
            except: pass
        # Token logging handled inside call functions
        new_badges = []
        try: new_badges = check_and_award(uid, lang)
        except: pass
        remaining = (q("SELECT credits FROM users WHERE id=%s",(uid,),one=True) or {}).get("credits",0)
        return jsonify(reply=reply, new_badges=[{"id":b,**BADGES[b]} for b in new_badges if b in BADGES],
                       lang=lang, q_mode=q_mode, credits_used=credits_needed, credits_remaining=remaining)
    except requests.exceptions.Timeout: return jsonify(error="Tutor is taking too long. Try again."), 504
    except Exception as e: return jsonify(error=str(e)), 500

@app.route("/api/chat/history")
@login_required
def api_chat_history():
    uid = session["user_id"]; user = current_user()
    lang = request.args.get("lang", user.get("target_lang","hindi"))
    rows = q("SELECT role,content,created_at::text FROM conversations WHERE user_id=%s AND lang=%s ORDER BY id ASC",(uid,lang),many=True) or []
    return jsonify(history=[dict(r) for r in rows])

@app.route("/api/chat/clear", methods=["POST"])
@login_required
def api_chat_clear():
    uid = session["user_id"]; user = current_user()
    lang = (request.json or {}).get("lang", user.get("target_lang","hindi"))
    q("DELETE FROM conversations WHERE user_id=%s AND lang=%s",(uid,lang),commit=True)
    return jsonify(ok=True)

@app.route("/api/tts", methods=["POST"])
@login_required
def api_tts():
    d = request.json or {}
    text = (d.get("text") or "").strip()[:500]
    lang = d.get("lang","hindi")
    if not text: return jsonify(error="No text"), 400
    uid = session["user_id"]
    try:
        ok, _ = deduct_credit(uid, 1)
        if not ok: return jsonify(error="NO_CREDITS"), 402
    except Exception as e: print(f"[CREDITS] {e}")
    if lang == "hindi" and SARVAM_KEY:
        try:
            res = requests.post("https://api.sarvam.ai/text-to-speech",
                headers={"Content-Type":"application/json","api-subscription-key":SARVAM_KEY},
                json={"text":text,"target_language_code":"hi-IN","speaker":"shubh","model":"bulbul:v3","pace":0.9,"enable_preprocessing":True},
                timeout=20)
            if res.ok:
                audios = res.json().get("audios",[])
                if audios:
                    ab = _b64.b64decode(audios[0])
                    return Response(ab, mimetype="audio/wav", headers={"Content-Length":str(len(ab))})
        except Exception as e: print(f"[TTS Hindi] {e}")
    if lang in ("french","spanish"):
        import urllib.parse
        tts_lang = "fr" if lang=="french" else "es"
        url = f"https://translate.googleapis.com/translate_tts?ie=UTF-8&q={urllib.parse.quote(text)}&tl={tts_lang}&client=gtx&ttsspeed=0.85"
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
            if r.ok and len(r.content) > 100:
                return Response(r.content, mimetype="audio/mpeg", headers={"Content-Length":str(len(r.content))})
        except Exception as e: print(f"[TTS {lang}] {e}")
    return jsonify(error="TTS unavailable"), 502

@app.route("/api/credits")
@login_required
def api_credits():
    uid = session["user_id"]
    row = q("SELECT credits FROM users WHERE id=%s",(uid,),one=True)
    return jsonify(credits=(row or {}).get("credits",100))

@app.route("/api/progress")
@login_required
def api_progress():
    uid = session["user_id"]; user = current_user()
    lang = request.args.get("lang", user.get("target_lang","hindi"))
    wc = (q("SELECT COUNT(DISTINCT word) FROM word_log WHERE user_id=%s AND lang=%s",(uid,lang),one=True) or {}).get("count",0)
    mc = (q("SELECT COUNT(*) FROM conversations WHERE user_id=%s AND lang=%s AND role='user'",(uid,lang),one=True) or {}).get("count",0)
    lessons = q("SELECT lesson_id,completed,words_seen,last_at::text FROM progress WHERE user_id=%s AND lang=%s",(uid,lang),many=True) or []
    badges  = q("SELECT badge_id,earned_at::text FROM achievements WHERE user_id=%s AND (lang=%s OR lang='all') ORDER BY earned_at ASC",(uid,lang),many=True) or []
    return jsonify(word_count=wc, msg_count=mc, lessons=[dict(l) for l in lessons],
                   badges=[{"id":b["badge_id"],"earned_at":b["earned_at"],**BADGES[b["badge_id"]]} for b in badges if b["badge_id"] in BADGES],
                   all_badges=[{"id":k,**v} for k,v in BADGES.items()])

@app.route("/api/progress/complete_lesson", methods=["POST"])
@login_required
def api_complete_lesson():
    d = request.json or {}; lid = d.get("lesson_id","")
    if not lid: return jsonify(error="lesson_id required"), 400
    uid = session["user_id"]; user = current_user(); lang = user.get("target_lang","hindi")
    if q("SELECT 1 FROM progress WHERE user_id=%s AND lang=%s AND lesson_id=%s",(uid,lang,lid),one=True):
        q("UPDATE progress SET completed=1,last_at=NOW() WHERE user_id=%s AND lang=%s AND lesson_id=%s",(uid,lang,lid),commit=True)
    else:
        q("INSERT INTO progress (user_id,lang,level,lesson_id,completed) VALUES (%s,%s,%s,%s,1)",(uid,lang,user.get("teach_level","beginner"),lid),commit=True)
    new_badges = []
    try: new_badges = check_and_award(uid, lang)
    except: pass
    return jsonify(ok=True, new_badges=[{"id":b,**BADGES[b]} for b in new_badges if b in BADGES])

@app.route("/api/buy_credits", methods=["POST"])
@login_required
def api_buy_credits():
    if not stripe: return jsonify(error="Stripe not installed."), 503
    if not STRIPE_SECRET_KEY: return jsonify(error="Stripe not configured."), 503
    d = request.json or {}; pm = d.get("payment_method_id")
    if not pm: return jsonify(error="Payment method required"), 400
    uid = session["user_id"]; user = current_user()
    try:
        amount_cents = int(d.get("amount_cents",700))
        if amount_cents not in VALID_PLANS: return jsonify(error="Invalid plan"), 400
        credits_to_add = VALID_PLANS[amount_cents]
        intent = stripe.PaymentIntent.create(amount=amount_cents, currency="usd", payment_method=pm, confirm=True,
            automatic_payment_methods={"enabled":True,"allow_redirects":"never"},
            metadata={"user_id":str(uid),"username":user["username"]})
        if intent.status == "succeeded":
            q("UPDATE users SET credits=credits+%s WHERE id=%s",(credits_to_add,uid),commit=True)
            q("INSERT INTO purchases (user_id,amount_cents,credits,stripe_id) VALUES (%s,%s,%s,%s)",(uid,amount_cents,credits_to_add,intent.id),commit=True)
            new_credits = (q("SELECT credits FROM users WHERE id=%s",(uid,),one=True) or {}).get("credits",0)
            return jsonify(ok=True, credits=new_credits, message=f"Payment successful! {credits_to_add} credits added.")
        return jsonify(error=f"Payment status: {intent.status}"), 402
    except Exception as e: return jsonify(error=str(e)), 500

def check_and_award(uid, lang):
    new = []
    wc = (q("SELECT COUNT(DISTINCT word) FROM word_log WHERE user_id=%s AND lang=%s",(uid,lang),one=True) or {}).get("count",0)
    for t,b in [(1,"first_word"),(5,"five_words"),(10,"ten_words"),(25,"twenty_five"),(50,"fifty_words")]:
        if wc >= t and not q("SELECT 1 FROM achievements WHERE user_id=%s AND badge_id=%s AND lang=%s",(uid,b,lang),one=True):
            try: q("INSERT INTO achievements (user_id,badge_id,lang) VALUES (%s,%s,%s)",(uid,b,lang),commit=True); new.append(b)
            except: pass
    lc = (q("SELECT COUNT(*) FROM progress WHERE user_id=%s AND lang=%s AND completed=1",(uid,lang),one=True) or {}).get("count",0)
    for t,b in [(1,"first_lesson"),(3,"three_lessons")]:
        if lc >= t and not q("SELECT 1 FROM achievements WHERE user_id=%s AND badge_id=%s AND lang=%s",(uid,b,lang),one=True):
            try: q("INSERT INTO achievements (user_id,badge_id,lang) VALUES (%s,%s,%s)",(uid,b,lang),commit=True); new.append(b)
            except: pass
    cc = (q("SELECT COUNT(*) FROM conversations WHERE user_id=%s AND lang=%s AND role='user'",(uid,lang),one=True) or {}).get("count",0)
    for t,b in [(10,"chat_10"),(50,"chat_50")]:
        if cc >= t and not q("SELECT 1 FROM achievements WHERE user_id=%s AND badge_id=%s AND lang=%s",(uid,b,lang),one=True):
            try: q("INSERT INTO achievements (user_id,badge_id,lang) VALUES (%s,%s,%s)",(uid,b,lang),commit=True); new.append(b)
            except: pass
    lcount = (q("SELECT COUNT(DISTINCT lang) as cnt FROM conversations WHERE user_id=%s",(uid,),one=True) or {}).get("cnt",0)
    if lcount >= 2 and not q("SELECT 1 FROM achievements WHERE user_id=%s AND badge_id='bilingual'",(uid,),one=True):
        try: q("INSERT INTO achievements (user_id,badge_id,lang) VALUES (%s,'bilingual','all')",(uid,),commit=True); new.append("bilingual")
        except: pass
    if lcount >= 3 and not q("SELECT 1 FROM achievements WHERE user_id=%s AND badge_id='trilingual'",(uid,),one=True):
        try: q("INSERT INTO achievements (user_id,badge_id,lang) VALUES (%s,'trilingual','all')",(uid,),commit=True); new.append("trilingual")
        except: pass
    return new

@app.route("/confirm")
def confirm_email():
    """Supabase redirects here after user clicks confirmation link."""
    # Supabase adds token_hash and type params on redirect
    token_hash = request.args.get("token_hash", "")
    token      = request.args.get("token", "")
    email      = request.args.get("email", "")
    t_type     = request.args.get("type", "email")

    # Try to verify via Supabase Auth
    sb = get_supabase()
    if sb and (token_hash or token):
        try:
            if token_hash:
                sb.auth.verify_otp({"token_hash": token_hash, "type": t_type})
            elif token and email:
                sb.auth.verify_otp({"email": email, "token": token, "type": "email"})
        except Exception as e:
            print(f"[CONFIRM] Supabase verify error: {e}")

    # Mark confirmed in our DB by email
    if email:
        user = q("SELECT * FROM users WHERE email=%s", (email.lower(),), one=True)
        if user:
            q("UPDATE users SET email_confirmed=1 WHERE id=%s", (user["id"],), commit=True)
            return render_template("confirm.html", status="success", username=user["username"])
        return render_template("confirm.html", status="error", msg="Account not found.")

    # Fallback — mark by session if logged in
    if "user_id" in session:
        q("UPDATE users SET email_confirmed=1 WHERE id=%s", (session["user_id"],), commit=True)
        user = current_user()
        return render_template("confirm.html", status="success", username=user["username"] if user else "")

    return render_template("confirm.html", status="error", msg="Could not verify — link may have expired.")

@app.route("/api/resend_confirmation", methods=["POST"])
@login_required
def api_resend_confirmation():
    uid  = session["user_id"]
    user = current_user()
    if user.get("email_confirmed"):
        return jsonify(ok=True, msg="Already confirmed")
    sb = get_supabase()
    if sb:
        try:
            sb.auth.resend({"type":"signup","email":user["email"],
                            "options":{"email_redirect_to":f"{APP_URL}/confirm"}})
            return jsonify(ok=True, msg="Confirmation email resent")
        except Exception as e:
            return jsonify(ok=False, error=str(e)), 500
    return jsonify(ok=False, error="Email service not configured"), 503

@app.route("/admin")
def admin_login():
    if "admin" in session: return redirect(url_for("admin_dashboard"))
    return render_template("admin.html", view="login")

@app.route("/admin/auth", methods=["POST"])
def admin_auth():
    pw = (request.json or {}).get("password","")
    if pw == ADMIN_PASSWORD: session["admin"] = True; return jsonify(ok=True)
    return jsonify(error="Wrong password"), 401

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    return render_template("admin.html", view="dashboard")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin",None); return redirect(url_for("admin_login"))

@app.route("/api/admin/users")
@admin_required
def api_admin_users():
    users = q("""SELECT u.id,u.username,u.email,u.target_lang,u.teach_level,u.credits,u.is_banned,u.created_at::text,
               COUNT(DISTINCT c.id) as msg_count, COALESCE(SUM(p.amount_cents),0) as total_spent_cents
               FROM users u LEFT JOIN conversations c ON c.user_id=u.id LEFT JOIN purchases p ON p.user_id=u.id
               GROUP BY u.id ORDER BY u.created_at DESC""",many=True) or []
    return jsonify(users=[dict(u) for u in users])

@app.route("/api/admin/stats")
@admin_required
def api_admin_stats():
    tu  = (q("SELECT COUNT(*) FROM users",one=True) or {}).get("count",0)
    at  = (q("SELECT COUNT(DISTINCT user_id) FROM conversations WHERE created_at >= NOW() - INTERVAL '1 day'",one=True) or {}).get("count",0)
    tr  = (q("SELECT COALESCE(SUM(amount_cents),0) FROM purchases",one=True) or {}).get("coalesce",0)
    tm  = (q("SELECT COUNT(*) FROM conversations WHERE role='user'",one=True) or {}).get("count",0)
    lb  = q("SELECT lang, COUNT(*) as cnt FROM conversations WHERE role='user' GROUP BY lang",many=True) or []
    return jsonify(total_users=tu,active_today=at,total_revenue_cents=tr,total_messages=tm,lang_breakdown=[dict(r) for r in lb])

@app.route("/api/admin/credits", methods=["POST"])
@admin_required
def api_admin_credits():
    d = request.json or {}; uid = d.get("user_id"); amt = int(d.get("amount",0))
    if not uid: return jsonify(error="user_id required"), 400
    q("UPDATE users SET credits=credits+%s WHERE id=%s",(amt,uid),commit=True)
    new = (q("SELECT credits FROM users WHERE id=%s",(uid,),one=True) or {}).get("credits",0)
    return jsonify(ok=True, credits=new)

@app.route("/api/admin/ban", methods=["POST"])
@admin_required
def api_admin_ban():
    d = request.json or {}; uid = d.get("user_id"); banned = int(d.get("banned",1))
    if not uid: return jsonify(error="user_id required"), 400
    q("UPDATE users SET is_banned=%s WHERE id=%s",(banned,uid),commit=True)
    return jsonify(ok=True)

with app.app_context():
    try: init_db()
    except Exception as e: print(f"[DB INIT] {e}")

if __name__ == "__main__":
    print("\n🚀  LanguagePaths at http://localhost:5000\n")
    app.run(debug=True, port=5000)
