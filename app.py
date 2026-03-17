from flask import Flask, render_template, request, redirect, jsonify, session, flash
import os
from datetime import datetime
from functools import wraps
import hashlib
import qrcode
import io
import base64

# ── Cloud DB: use PostgreSQL if DATABASE_URL is set, else SQLite locally ──
DATABASE_URL = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    import psycopg2
    import psycopg2.extras
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    USE_POSTGRES = True
else:
    import sqlite3
    USE_POSTGRES = False

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "qrtrack_secret_key_2024")

os.makedirs("static/qrcodes", exist_ok=True)

# ──────────────────────────── DATABASE ────────────────────────────

def get_db():
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return conn
    else:
        conn = sqlite3.connect("database.db", timeout=10)
        conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
        return conn

def db_execute(conn, sql, params=()):
    if USE_POSTGRES:
        sql = sql.replace("?", "%s")
    c = conn.cursor()
    c.execute(sql, params)
    return c

def db_fetchone(cursor):
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(row) if USE_POSTGRES else row

def db_fetchall(cursor):
    rows = cursor.fetchall()
    return [dict(r) for r in rows] if USE_POSTGRES else rows

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
            due_date TEXT,
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
            notes TEXT,
            due_date TEXT,
            reminder_status TEXT DEFAULT 'none'
        )''')
        admin_pw = hashlib.sha256("admin123".encode()).hexdigest()
        c.execute("""INSERT INTO users (username, password, role, full_name, department)
                     VALUES (%s,%s,%s,%s,%s) ON CONFLICT (username) DO NOTHING""",
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
            due_date TEXT,
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
            notes TEXT,
            due_date TEXT,
            reminder_status TEXT DEFAULT 'none'
        )''')
        admin_pw = hashlib.sha256("admin123".encode()).hexdigest()
        c.execute("INSERT OR IGNORE INTO users (username, password, role, full_name, department) VALUES (?,?,?,?,?)",
                  ("admin", admin_pw, "admin", "Administrator", "Admin"))
        conn.commit()
        conn.close()

init_db()

# ──────────────────────── QR CODE HELPER ────────────────────────

def generate_qr_base64(file_id):
    """Returns a base64 data URI — no filesystem needed on the cloud."""
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

    c = db_execute(conn, "SELECT COUNT(*) as cnt FROM files")
    total_files = db_fetchone(c)["cnt"]

    c = db_execute(conn, "SELECT COUNT(*) as cnt FROM movements WHERE out_time IS NULL")
    checked_out = db_fetchone(c)["cnt"]

    c = db_execute(conn, "SELECT COUNT(DISTINCT department) as cnt FROM files")
    departments = db_fetchone(c)["cnt"]

    # Recent activity
    c = db_execute(conn, """SELECT m.*, f.file_name FROM movements m
                             JOIN files f ON m.file_id = f.file_id
                             ORDER BY m.id DESC LIMIT 8""")
    recent = db_fetchall(c)

    # Overdue files — checked out and past due date
    if USE_POSTGRES:
        c = db_execute(conn, """
            SELECT f.file_id, f.file_name, m.person, m.due_date
            FROM movements m
            JOIN files f ON f.file_id = m.file_id
            WHERE m.action = 'checkout' AND m.out_time IS NULL
            AND m.due_date IS NOT NULL
            AND m.due_date::date < CURRENT_DATE
        """)
    else:
        c = db_execute(conn, """
            SELECT f.file_id, f.file_name, m.person, m.due_date
            FROM movements m
            JOIN files f ON f.file_id = m.file_id
            WHERE m.action = 'checkout' AND m.out_time IS NULL
            AND datetime(m.due_date) < datetime('now')
        """)
    overdue = db_fetchall(c)

    conn.close()
    return render_template("index.html",
        total_files=total_files,
        checked_out=checked_out,
        departments=departments,
        recent=recent,
        overdue=overdue
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
        username  = request.form["username"].strip()
        password  = hashlib.sha256(request.form["password"].encode()).hexdigest()
        full_name = request.form["full_name"].strip()
        dept      = request.form["department"].strip()
        conn = get_db()
        try:
            if USE_POSTGRES:
                c = conn.cursor()
                c.execute("INSERT INTO users (username,password,role,full_name,department) VALUES (%s,%s,%s,%s,%s)",
                          (username, password, "viewer", full_name, dept))
            else:
                c = conn.cursor()
                c.execute("INSERT INTO users (username,password,role,full_name,department) VALUES (?,?,?,?,?)",
                          (username, password, "viewer", full_name, dept))
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
        due_date  = request.form.get("due_date", "").strip() or None
        person    = session["full_name"]

        conn = get_db()
        c = db_execute(conn, "SELECT file_id FROM files WHERE file_id=?", (file_id,))
        existing = db_fetchone(c)
        if existing:
            conn.close()
            flash(f"File ID '{file_id}' already exists.", "error")
            return render_template("create.html", qr_b64=None)

        # Generate QR as base64 (works on cloud — no filesystem needed)
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
            c.execute("""
                INSERT INTO files (file_id,file_name,department,created_date,status,stage,priority,description,due_date,qr_base64)
                VALUES (%s,%s,%s,%s,'active','created',%s,%s,%s,%s)
            """, (file_id, file_name, dept, now, priority, desc, due_date, qr_b64))
            c.execute("""
                INSERT INTO movements (file_id,department,person,action,in_time,due_date)
                VALUES (%s,%s,%s,'created',%s,%s)
            """, (file_id, dept, person, now, due_date))
        else:
            c = conn.cursor()
            c.execute("""
                INSERT INTO files (file_id,file_name,department,created_date,status,stage,priority,description,due_date,qr_base64)
                VALUES (?,?,?,?,'active','created',?,?,?,?)
            """, (file_id, file_name, dept, now, priority, desc, due_date, qr_b64))
            c.execute("""
                INSERT INTO movements (file_id,department,person,action,in_time,due_date)
                VALUES (?,?,?,'created',?,?)
            """, (file_id, dept, person, now, due_date))

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
                c.execute("INSERT INTO movements (file_id,department,person,action,in_time,notes) VALUES (%s,%s,%s,'checkin',%s,%s)",
                          (file_id, dept, person, now, notes))
            elif action == "checkout":
                due_date = request.form.get("due_date", "").strip() or None
                c.execute("SELECT id FROM movements WHERE file_id=%s AND out_time IS NULL ORDER BY id DESC LIMIT 1", (file_id,))
                open_row = c.fetchone()
                if open_row:
                    c.execute("UPDATE movements SET out_time=%s WHERE id=%s", (now, dict(open_row)["id"]))
                c.execute("INSERT INTO movements (file_id,department,person,action,in_time,notes,due_date) VALUES (%s,%s,%s,'checkout',%s,%s,%s)",
                          (file_id, dept, person, now, notes, due_date))
                if due_date:
                    c.execute("UPDATE files SET due_date=%s WHERE file_id=%s", (due_date, file_id))
            elif action == "transfer":
                new_dept = request.form.get("new_department", dept)
                c.execute("UPDATE movements SET out_time=%s WHERE file_id=%s AND out_time IS NULL", (now, file_id))
                c.execute("INSERT INTO movements (file_id,department,person,action,in_time,notes) VALUES (%s,%s,%s,'transfer',%s,%s)",
                          (file_id, new_dept, person, now, f"Transferred to {new_dept}. {notes}"))
                c.execute("UPDATE files SET department=%s WHERE file_id=%s", (new_dept, file_id))
            if stage:
                c.execute("UPDATE files SET stage=%s WHERE file_id=%s", (stage, file_id))
        else:
            c = conn.cursor()
            if action == "checkin":
                c.execute("UPDATE movements SET out_time=? WHERE file_id=? AND out_time IS NULL", (now, file_id))
                c.execute("INSERT INTO movements (file_id,department,person,action,in_time,notes) VALUES (?,?,?,'checkin',?,?)",
                          (file_id, dept, person, now, notes))
            elif action == "checkout":
                due_date = request.form.get("due_date", "").strip() or None
                open_row = c.execute("SELECT id FROM movements WHERE file_id=? AND out_time IS NULL ORDER BY id DESC LIMIT 1", (file_id,)).fetchone()
                if open_row:
                    c.execute("UPDATE movements SET out_time=? WHERE id=?", (now, open_row["id"]))
                c.execute("INSERT INTO movements (file_id,department,person,action,in_time,notes,due_date) VALUES (?,?,?,'checkout',?,?,?)",
                          (file_id, dept, person, now, notes, due_date))
                if due_date:
                    c.execute("UPDATE files SET due_date=? WHERE file_id=?", (due_date, file_id))
            elif action == "transfer":
                new_dept = request.form.get("new_department", dept)
                c.execute("UPDATE movements SET out_time=? WHERE file_id=? AND out_time IS NULL", (now, file_id))
                c.execute("INSERT INTO movements (file_id,department,person,action,in_time,notes) VALUES (?,?,?,'transfer',?,?)",
                          (file_id, new_dept, person, now, f"Transferred to {new_dept}. {notes}"))
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
    c = db_execute(conn, """SELECT f.*, m.person, m.action, m.in_time
                             FROM files f
                             LEFT JOIN movements m ON m.id = (
                                 SELECT MAX(id) FROM movements WHERE file_id = f.file_id
                             )
                             ORDER BY f.created_date DESC""")
    files = db_fetchall(c)
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
                c.execute("INSERT INTO users (username,password,role,full_name,department) VALUES (%s,%s,%s,%s,%s)",
                          (username, password, role, full_name, dept))
            else:
                c = conn.cursor()
                c.execute("INSERT INTO users (username,password,role,full_name,department) VALUES (?,?,?,?,?)",
                          (username, password, role, full_name, dept))
            conn.commit()
            flash(f"User '{username}' created!", "success")
        except Exception:
            flash("Username already exists.", "error")
        finally:
            conn.close()
    conn = get_db()
    c = db_execute(conn, "SELECT id, username, role, full_name, department FROM users")
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
        "stage": f.get("stage", "created"),
        "person": m["person"] if m else "Unknown"
    })

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)
