# 🕉️ HindiPath – Learn Hindi (Tamil speakers via English)
**Powered by Sarvam AI `sarvam-m` model**

---

## ⚡ Quick Start (5 minutes)

### 1. Install dependencies
```bash
pip install flask werkzeug requests
```

### 2. Run the app
```bash
cd hindipath
python app.py
```

### 3. Open in browser
```
http://localhost:5000
```

### 4. Register → Go to ⚙️ Settings → Paste your Sarvam AI key
Get a free key at **https://dashboard.sarvam.ai**

---

## 📁 Project Structure
```
hindipath/
├── app.py               ← Flask backend (all routes + SQLite DB)
├── requirements.txt     ← Python dependencies
├── hindipath.db         ← Auto-created SQLite database
├── templates/
│   ├── auth.html        ← Login / Register page
│   └── app.html         ← Main app (Tutor + Test tabs)
└── README.md
```

---

## 🚀 Deploy to Production (Free options)

### Option A — Railway (Easiest, ~2 min)
1. Push to GitHub
2. Go to https://railway.app → New Project → Deploy from GitHub
3. Add env var: `SECRET_KEY=your-random-secret-here`
4. Done! Railway auto-detects Flask.

### Option B — Render
1. Push to GitHub
2. Go to https://render.com → New Web Service
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app`
5. Add env var: `SECRET_KEY=your-random-secret`

### Option C — PythonAnywhere (Free tier)
1. Upload files to PythonAnywhere
2. Create a new Web App → Flask → Python 3.11
3. Point WSGI to your app.py

### For production — add gunicorn:
```bash
pip install gunicorn
gunicorn -w 2 -b 0.0.0.0:5000 app:app
```

---

## 🧪 Features

| Feature | Details |
|---------|---------|
| 🔐 Auth | Register / Login with hashed passwords (SHA-256) |
| 🧑‍🏫 AI Tutor | Sarvam AI `sarvam-m` — teaches Hindi via Tamil+English |
| 💾 History | Full conversation saved per user in SQLite |
| 🎯 Test Myself | 6 topics × 5 random words per test |
| 🔊 Pronunciation | Web Speech API for voice input + Hindi TTS output |
| 📊 Test History | Scores saved and shown per user |
| 🧠 Smart Topics | Learned topics highlighted based on chat history |

---

## 🔑 Environment Variables
| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Flask session secret | Random (dev only) |
| `PORT` | Server port | 5000 |

---

## 📝 Database Schema
- **users** — id, username, email, password_hash, sarvam_key, my_lang, teach_level
- **conversations** — id, user_id, role, content, created_at
- **test_sessions** — id, user_id, topic, words_json, answers_json, score, completed

---

## 🛠️ Extending the App
- Add more words to `WORD_BANK` in `app.py`
- Add new topics by adding keys to `WORD_BANK` and `TOPIC_META` (in app.html)
- Integrate Sarvam TTS API for server-side Hindi audio
- Add a leaderboard using the `test_sessions` table
