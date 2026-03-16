from flask import Flask, render_template, request, redirect, jsonify, session, url_for, flash
import os
from datetime import datetime
from functools import wraps
import hashlib
import qrcode
import io
import base64

# ── Cloud DB: use psycopg2 if DATABASE_URL is set, else fall back to SQLite ──
DATABASE_URL = os.environ.get("DATABASE_URL")  # Set this on Render/Railway/etc.

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras
    # Render gives postgres:// but psycopg2 needs postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    USE_POSTGRES = True
else:
    import sqlite3
    USE_POSTGRES = False

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "qrtrack_secret_key_2024")

# QR codes: on cloud we store as base64 in DB instead of files
# On local we still write to disk for backward compat
os.makedirs("static/qrcodes", exist_ok=True)

# ──────────────────────────── DATABASE ────────────────────────────

def get_db():
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return conn
    else:
        conn = sqlite3.connect("database.db", timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

def db_execute(conn, sql, params=()):
    """Execute a query. Postgres uses %s placeholders, SQLite uses ?."""
    if USE_POSTGRES:
        sql = sql.replace("?", "%s")
        # Postgres doesn't support INSERT OR IGNORE; replace for compatibility
        sql = sql.replace("INSERT OR IGNORE", "INSERT")
    c = conn.cursor()
    c.execute(sql, params)
    return c

def db_fetchone(cursor):
    row = cursor.fetchone()
    if row is None:
        return None
    if USE_POSTGRES:
        return dict(row)  # psycopg2 RealDictRow → plain dict
    return row  # sqlite3.Row (supports both dict and index access)

def db_fetchall(cursor):
    rows = cursor.fetchall()
    if USE_POSTGRES:
        return [dict(r) for r in rows]
    return rows

def init_db():
    conn = get_db()

    if USE_POSTGRES:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT DEFAULT 'staff',
            full_name TEXT,
            department TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS files (
            file_id TEXT PRIMARY KEY,
            file_name TEXT,
            department TEXT,
            created_date TEXT,
            status TEXT DEFAULT 'active',
            stage TEXT DEFAULT 'created',
            priority TEXT DEFAULT 'normal',
            description TEXT,
            qr_base64 TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS movements (
            id SERIAL PRIMARY KEY,
            file_id TEXT,
            department TEXT,
            person TEXT,
            action TEXT,
            in_time TEXT,
            out_time TEXT,
            notes TEXT
        )''')
        admin_pw = hashlib.sha256("admin123".encode()).hexdigest()
        c.execute("""INSERT INTO users (username, password, role, full_name, department)
                     VALUES (%s, %s, %s, %s, %s)
                     ON CONFLICT (username) DO NOTHING""",
                  ("admin", admin_pw, "admin", "Administrator", "Admin"))
        conn.commit()
        conn.close()
    else:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT DEFAULT 'staff',
            full_name TEXT,
            department TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS files (
            file_id TEXT PRIMARY KEY,
            file_name TEXT,
            department TEXT,
            created_date TEXT,
            status TEXT DEFAULT 'active',
            stage TEXT DEFAULT 'created',
            priority TEXT DEFAULT 'normal',
            description TEXT,
            qr_base64 TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT,
            department TEXT,
            person TEXT,
            action TEXT,
            in_time TEXT,
            out_time TEXT,
            notes TEXT
        )''')
        admin_pw = hashlib.sha256("admin123".encode()).hexdigest()
        c.execute("INSERT OR IGNORE INTO users (username, password, role, full_name, department) VALUES (?, ?, ?, ?, ?)",
                  ("admin", admin_pw, "admin", "Administrator", "Admin"))
        conn.commit()
        conn.close()

init_db()

# ──────────────────────── QR CODE HELPER ────────────────────────

def generate_qr_base64(file_id):
    """Generate a QR code and return it as a base64 data URI."""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(file_id)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0f172a", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"

# ──────────────────────────── AUTH ────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect("/login")
        if session.get("role") != "admin":
            flash("Admin access required.", "error")
            return redirect("/")
        return f(*args, **kwargs)
    return decorated

# ──────────────────────────── ROUTES ────────────────────────────

@app.route("/")
@login_required
def index():
    conn = get_db()

    if USE_POSTGRES:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM files")
        total_files = c.fetchone()["cnt"]
        c.execute("SELECT COUNT(*) as cnt FROM movements WHERE out_time IS NULL")
        checked_out = c.fetchone()["cnt"]
        c.execute("SELECT COUNT(DISTINCT department) as cnt FROM files")
        departments = c.fetchone()["cnt"]
        c.execute("""SELECT m.*, f.file_name FROM movements m
                     JOIN files f ON m.file_id = f.file_id
                     ORDER BY m.id DESC LIMIT 8""")
        recent = db_fetchall(c)
    else:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM files")
        total_files = c.fetchone()["cnt"]
        c.execute("SELECT COUNT(*) as cnt FROM movements WHERE out_time IS NULL")
        checked_out = c.fetchone()["cnt"]
        c.execute("SELECT COUNT(DISTINCT department) as cnt FROM files")
        departments = c.fetchone()["cnt"]
        c.execute("""SELECT m.*, f.file_name FROM movements m
                     JOIN files f ON m.file_id = f.file_id
                     ORDER BY m.id DESC LIMIT 8""")
        recent = c.fetchall()

    conn.close()
    return render_template("index.html",
        total_files=total_files,
        checked_out=checked_out,
        departments=departments,
        recent=recent
    )

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = hashlib.sha256(request.form["password"].encode()).hexdigest()
        conn = get_db()
        c = db_execute(conn, "SELECT * FROM users WHERE username=? AND password=?", (username, password))
        user = db_fetchone(c)
        conn.close()
        if user:
            session["user"] = user["username"]
            session["role"] = user["role"]
            session["full_name"] = user["full_name"]
            session["department"] = user["department"]
            return redirect("/")
        flash("Invalid username or password.", "error")
    return render_template("login.html")

@app.route("/signup", methods=["GET","POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = hashlib.sha256(request.form["password"].encode()).hexdigest()
        full_name = request.form["full_name"].strip()
        dept = request.form["department"].strip()

        conn = get_db()
        try:
            if USE_POSTGRES:
                c = conn.cursor()
                c.execute(
                    "INSERT INTO users (username, password, role, full_name, department) VALUES (%s,%s,%s,%s,%s)",
                    (username, password, "viewer", full_name, dept)
                )
            else:
                c = conn.cursor()
                c.execute(
                    "INSERT INTO users (username, password, role, full_name, department) VALUES (?,?,?,?,?)",
                    (username, password, "viewer", full_name, dept)
                )
            conn.commit()
            flash("Account created successfully. Please login.", "success")
            return redirect("/login")
        except Exception:
            flash("Username already exists.", "error")
        finally:
            conn.close()

    return render_template("signup.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/create", methods=["GET","POST"])
@login_required
def create():
    if request.method == "POST":
        file_id   = request.form["file_id"].strip().upper()
        file_name = request.form["file_name"].strip()
        dept      = request.form["department"].strip()
        priority  = request.form.get("priority", "normal")
        desc      = request.form.get("description", "").strip()
        person    = session["full_name"]

        conn = get_db()
        c = db_execute(conn, "SELECT file_id FROM files WHERE file_id=?", (file_id,))
        existing = db_fetchone(c)
        if existing:
            conn.close()
            flash(f"File ID '{file_id}' already exists.", "error")
            return render_template("create.html", qr_path=None)

        # Generate QR as base64 (works on cloud with no filesystem)
        qr_b64 = generate_qr_base64(file_id)

        # Also save to disk locally for backward compat
        try:
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(file_id)
            qr.make(fit=True)
            img = qr.make_image(fill_color="#0f172a", back_color="white")
            img.save(f"static/qrcodes/{file_id}.png")
        except Exception:
            pass

        now = str(datetime.now())
        if USE_POSTGRES:
            c = conn.cursor()
            c.execute(
                "INSERT INTO files (file_id, file_name, department, created_date, status, stage, priority, description, qr_base64) VALUES (%s,%s,%s,%s,'active','created',%s,%s,%s)",
                (file_id, file_name, dept, now, priority, desc, qr_b64)
            )
            c.execute(
                "INSERT INTO movements (file_id, department, person, action, in_time, out_time) VALUES (%s,%s,%s,'created',%s,NULL)",
                (file_id, dept, person, now)
            )
        else:
            c = conn.cursor()
            c.execute(
                "INSERT INTO files VALUES (?, ?, ?, ?, 'active', 'created', ?, ?, ?)",
                (file_id, file_name, dept, now, priority, desc, qr_b64)
            )
            c.execute(
                "INSERT INTO movements (file_id, department, person, action, in_time, out_time) VALUES (?, ?, ?, 'created', ?, NULL)",
                (file_id, dept, person, now)
            )
        conn.commit()
        conn.close()

        flash(f"File '{file_name}' created successfully!", "success")
        return render_template("create.html", qr_b64=qr_b64, file_id=file_id, file_name=file_name)

    return render_template("create.html", qr_b64=None)

@app.route("/scan", methods=["GET","POST"])
@login_required
def scan():
    if request.method == "POST":
        file_id = request.form["file_id"].strip().upper()
        action  = request.form["action"]
        stage   = request.form.get("stage")
        notes   = request.form.get("notes", "")
        person  = session["full_name"]
        dept    = session["department"]

        conn = get_db()
        c = db_execute(conn, "SELECT * FROM files WHERE file_id=?", (file_id,))
        file_row = db_fetchone(c)
        if not file_row:
            conn.close()
            flash(f"File ID '{file_id}' not found.", "error")
            return render_template("scan.html")

        now = str(datetime.now())
        if USE_POSTGRES:
            c = conn.cursor()
            if action == "checkin":
                c.execute("UPDATE movements SET out_time=%s WHERE file_id=%s AND out_time IS NULL", (now, file_id))
                c.execute("INSERT INTO movements (file_id, department, person, action, in_time, notes) VALUES (%s,%s,%s,'checkin',%s,%s)", (file_id, dept, person, now, notes))
            elif action == "checkout":
                c.execute("SELECT id FROM movements WHERE file_id=%s AND out_time IS NULL ORDER BY id DESC LIMIT 1", (file_id,))
                open_row = c.fetchone()
                if open_row:
                    c.execute("UPDATE movements SET out_time=%s WHERE id=%s", (now, open_row["id"]))
                c.execute("INSERT INTO movements (file_id, department, person, action, in_time, notes) VALUES (%s,%s,%s,'checkout',%s,%s)", (file_id, dept, person, now, notes))
            elif action == "transfer":
                new_dept = request.form.get("new_department", dept)
                c.execute("UPDATE movements SET out_time=%s WHERE file_id=%s AND out_time IS NULL", (now, file_id))
                c.execute("INSERT INTO movements (file_id, department, person, action, in_time, notes) VALUES (%s,%s,%s,'transfer',%s,%s)", (file_id, new_dept, person, now, f"Transferred to {new_dept}. {notes}"))
                c.execute("UPDATE files SET department=%s WHERE file_id=%s", (new_dept, file_id))
            if stage:
                c.execute("UPDATE files SET stage=%s WHERE file_id=%s", (stage, file_id))
        else:
            c = conn.cursor()
            if action == "checkin":
                c.execute("UPDATE movements SET out_time=? WHERE file_id=? AND out_time IS NULL", (now, file_id))
                c.execute("INSERT INTO movements (file_id, department, person, action, in_time, notes) VALUES (?, ?, ?, 'checkin', ?, ?)", (file_id, dept, person, now, notes))
            elif action == "checkout":
                open_row = c.execute("SELECT id FROM movements WHERE file_id=? AND out_time IS NULL ORDER BY id DESC LIMIT 1", (file_id,)).fetchone()
                if open_row:
                    c.execute("UPDATE movements SET out_time=? WHERE id=?", (now, open_row["id"]))
                c.execute("INSERT INTO movements (file_id, department, person, action, in_time, notes) VALUES (?, ?, ?, 'checkout', ?, ?)", (file_id, dept, person, now, notes))
            elif action == "transfer":
                new_dept = request.form.get("new_department", dept)
                c.execute("UPDATE movements SET out_time=? WHERE file_id=? AND out_time IS NULL", (now, file_id))
                c.execute("INSERT INTO movements (file_id, department, person, action, in_time, notes) VALUES (?, ?, ?, 'transfer', ?, ?)", (file_id, new_dept, person, now, f"Transferred to {new_dept}. {notes}"))
                c.execute("UPDATE files SET department=? WHERE file_id=?", (new_dept, file_id))
            if stage:
                c.execute("UPDATE files SET stage=? WHERE file_id=?", (stage, file_id))

        conn.commit()
        conn.close()
        flash(f"Action '{action}' recorded for file {file_id}.", "success")
        return redirect("/")
    return render_template("scan.html")

@app.route("/history", methods=["GET","POST"])
@login_required
def history():
    records = None
    file_info = None
    file_id = request.form.get("file_id", "").strip().upper() if request.method == "POST" else ""
    if file_id:
        conn = get_db()
        c = db_execute(conn, "SELECT * FROM files WHERE file_id=?", (file_id,))
        file_info = db_fetchone(c)
        c = db_execute(conn, "SELECT * FROM movements WHERE file_id=? ORDER BY id DESC", (file_id,))
        records = db_fetchall(c)
        conn.close()
    return render_template("history.html", records=records, file_info=file_info, searched_id=file_id)

@app.route("/files")
@login_required
def all_files():
    conn = get_db()
    if USE_POSTGRES:
        c = conn.cursor()
        c.execute("""SELECT f.*, m.person, m.action, m.in_time
                     FROM files f
                     LEFT JOIN movements m ON m.id = (
                         SELECT MAX(id) FROM movements WHERE file_id = f.file_id
                     )
                     ORDER BY f.created_date DESC""")
        files = db_fetchall(c)
    else:
        c = conn.cursor()
        files = c.execute("""SELECT f.*, m.person, m.action, m.in_time
                             FROM files f
                             LEFT JOIN movements m ON m.id = (
                                 SELECT MAX(id) FROM movements WHERE file_id = f.file_id
                             )
                             ORDER BY f.created_date DESC""").fetchall()
    conn.close()
    return render_template("files.html", files=files)

@app.route("/users", methods=["GET","POST"])
@admin_required
def users():
    if request.method == "POST":
        username  = request.form["username"].strip()
        password  = hashlib.sha256(request.form["password"].encode()).hexdigest()
        role      = request.form["role"]
        full_name = request.form["full_name"].strip()
        dept      = request.form["department"].strip()
        conn = get_db()
        try:
            if USE_POSTGRES:
                c = conn.cursor()
                c.execute("INSERT INTO users (username, password, role, full_name, department) VALUES (%s,%s,%s,%s,%s)",
                          (username, password, role, full_name, dept))
            else:
                c = conn.cursor()
                c.execute("INSERT INTO users (username, password, role, full_name, department) VALUES (?,?,?,?,?)",
                          (username, password, role, full_name, dept))
            conn.commit()
            flash(f"User '{username}' created!", "success")
        except Exception:
            flash("Username already exists.", "error")
        finally:
            conn.close()

    conn = get_db()
    c = db_execute(conn, "SELECT id, username, role, full_name, department FROM users", ())
    all_users = db_fetchall(c)
    conn.close()
    return render_template("users.html", users=all_users)

@app.route("/fileinfo/<file_id>")
@login_required
def fileinfo(file_id):
    conn = get_db()
    c = db_execute(conn, "SELECT * FROM files WHERE file_id=?", (file_id.upper(),))
    f = db_fetchone(c)
    c = db_execute(conn, "SELECT * FROM movements WHERE file_id=? ORDER BY id DESC LIMIT 1", (file_id.upper(),))
    m = db_fetchone(c)
    conn.close()
    if not f:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "file_name": f["file_name"],
        "department": f["department"],
        "priority": f["priority"],
        "status": f["status"],
        "person": m["person"] if m else "Unknown"
    })

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
