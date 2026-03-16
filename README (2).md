# QRTrack – Cloud Deployment Guide

## What Changed (Local → Cloud)

| Feature | Local (before) | Cloud (after) |
|---|---|---|
| Database | SQLite `database.db` file | PostgreSQL (via `DATABASE_URL` env var) |
| QR images | Saved to `static/qrcodes/*.png` | Stored as base64 in the `files` table |
| Secret key | Hardcoded string | `SECRET_KEY` environment variable |
| Server | Flask dev server | Gunicorn (production WSGI) |
| Config | None | `render.yaml` / `Procfile` |

---

## Deploy to Render.com (Free tier, recommended)

### Step 1 – Push to GitHub
```bash
git init
git add .
git commit -m "initial commit"
# Create a repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/qrtrack.git
git push -u origin main
```

### Step 2 – Create a Render account
Go to https://render.com and sign up (free).

### Step 3 – New Web Service
1. Click **New → Web Service**
2. Connect your GitHub repo
3. Render auto-detects Python. Set:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
   - **Instance Type:** Free

### Step 4 – Add PostgreSQL database
1. Click **New → PostgreSQL**
2. Name it `qrtrack-db`
3. Render gives you a **Connection String** (starts with `postgresql://`)
4. Copy it

### Step 5 – Set environment variables
In your Web Service → **Environment**:
```
DATABASE_URL = <paste the PostgreSQL connection string>
SECRET_KEY   = <any long random string, e.g. use https://randomkeygen.com>
```

### Step 6 – Deploy
Click **Deploy**. Render installs dependencies, starts Gunicorn, and gives you a URL like:
`https://qrtrack-xxxx.onrender.com`

---

## Deploy to Railway.app (alternative)

1. Go to https://railway.app → New Project → Deploy from GitHub
2. Add a **PostgreSQL** plugin — Railway auto-sets `DATABASE_URL`
3. Add env var `SECRET_KEY`
4. Railway auto-detects `Procfile` and deploys

---

## Run locally (unchanged)

```bash
pip install -r requirements.txt
python app.py
```
Without `DATABASE_URL`, the app uses SQLite automatically.

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | On cloud | PostgreSQL connection string |
| `SECRET_KEY` | On cloud | Flask session secret (generate a random one) |
