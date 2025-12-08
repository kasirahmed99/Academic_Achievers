import os
import json
import sqlite3
from datetime import datetime

from flask import (
    Flask,
    g,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    send_from_directory,
)
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(__file__)
APP_DB = os.path.join(BASE_DIR, "academic.db")
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.secret_key = "change-this-secret"

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(APP_DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS scholarships (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        country TEXT NOT NULL,
        university TEXT,
        level TEXT,
        field TEXT,
        tags TEXT,
        deadline TEXT,
        link TEXT NOT NULL,
        checklist TEXT,
        min_gpa REAL,
        is_international_only INTEGER DEFAULT 0,
        image_filename TEXT,
        brochure_filename TEXT
    );
    """
    )
    db.commit()


def seed_admin():
    db = get_db()
    cur = db.execute("SELECT COUNT(*) AS c FROM users WHERE is_admin=1")
    if cur.fetchone()["c"] == 0:
        email = "admin@academicachievers.app"
        pwd = "admin123"
        db.execute(
            "INSERT INTO users (email, password_hash, is_admin) VALUES (?, ?, 1)",
            (email, generate_password_hash(pwd)),
        )
        db.commit()
        print(f"[INFO] Admin created: {email} / {pwd} (change this!)")


def seed_scholarships_from_json():
    path = os.path.join(DATA_DIR, "seed_scholarships.json")
    db = get_db()
    cur = db.execute("SELECT COUNT(*) AS c FROM scholarships")
    if cur.fetchone()["c"] > 0:
        return
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for s in data:
        checklist = "\n".join(s.get("checklist", []))
        db.execute(
            """
            INSERT INTO scholarships (name, country, deadline, link, checklist)
            VALUES (?, ?, ?, ?, ?)
            """,
            (s["name"], s["country"], s.get("deadline"), s["link"], checklist),
        )
    db.commit()
    print("[INFO] Seeded scholarships from JSON.")


def current_user():
    if "user_id" in session:
        return {
            "id": session["user_id"],
            "email": session.get("user_email"),
            "is_admin": session.get("is_admin", 0),
        }
    return None


def login_required(f):
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    wrapper.__name__ = f.__name__
    return wrapper


def admin_required(f):
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u or not u["is_admin"]:
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)

    wrapper.__name__ = f.__name__
    return wrapper


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        if len(password) < 6:
            return render_template(
                "signup.html", error="Password must be at least 6 characters."
            )
        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (email, password_hash) VALUES (?, ?)",
                (email, generate_password_hash(password)),
            )
            db.commit()
        except sqlite3.IntegrityError:
            return render_template("signup.html", error="Email already registered.")
        row = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        session["user_id"] = row["id"]
        session["user_email"] = email
        session["is_admin"] = 0
        return redirect(url_for("home"))
    return render_template("signup.html", error=None)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return render_template("login.html", error="Invalid email or password.")
        session["user_id"] = row["id"]
        session["user_email"] = row["email"]
        session["is_admin"] = row["is_admin"]
        return redirect(url_for("home"))
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/api/scholarships")
def api_scholarships():
    db = get_db()
    rows = db.execute("SELECT * FROM scholarships").fetchall()
    items = []
    for r in rows:
        items.append(
            {
                "id": r["id"],
                "name": r["name"],
                "country": r["country"],
                "deadline": r["deadline"],
                "link": r["link"],
                "checklist": (r["checklist"] or "").splitlines()
                if r["checklist"]
                else [],
            }
        )
    return jsonify(items)


@app.route("/scholarships")
def scholarships():
    q = request.args.get("q", "").strip().lower()
    country = request.args.get("country", "").strip().lower()
    sort = request.args.get("sort", "")
    db = get_db()
    rows = db.execute("SELECT * FROM scholarships").fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["checklist"] = (
            (r["checklist"] or "").splitlines() if r["checklist"] else []
        )
        items.append(item)

    if q:
        items = [s for s in items if q in s["name"].lower()]
    if country:
        items = [s for s in items if country in s["country"].lower()]

    def parse_date(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d")
        except Exception:
            return None

    if sort == "deadline_asc":
        items.sort(key=lambda s: (parse_date(s["deadline"]) or datetime.max))
    elif sort == "deadline_desc":
        items.sort(
            key=lambda s: (parse_date(s["deadline"]) or datetime.min), reverse=True
        )

    return render_template("scholarships.html", scholarships=items)


@app.route("/scholarships/<int:sid>")
def scholarship_detail(sid):
    db = get_db()
    r = db.execute("SELECT * FROM scholarships WHERE id=?", (sid,)).fetchone()
    if not r:
        return redirect(url_for("scholarships"))
    s = dict(r)
    s["checklist"] = (r["checklist"] or "").splitlines() if r["checklist"] else []
    return render_template("scholarship_detail.html", s=s)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]
        db = get_db()
        row = db.execute(
            "SELECT * FROM users WHERE email=? AND is_admin=1", (email,)
        ).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return render_template(
                "admin_login.html", error="Invalid admin credentials."
            )
        session["user_id"] = row["id"]
        session["user_email"] = row["email"]
        session["is_admin"] = 1
        return redirect(url_for("admin_dashboard"))
    return render_template("admin_login.html", error=None)


@app.route("/admin")
@admin_required
def admin_dashboard():
    db = get_db()
    rows = db.execute("SELECT * FROM scholarships ORDER BY id DESC").fetchall()
    items = []
    for r in rows:
        item = dict(r)
        item["checklist"] = (
            (r["checklist"] or "").splitlines() if r["checklist"] else []
        )
        items.append(item)
    return render_template("admin_dashboard.html", scholarships=items)


@app.route("/admin/add", methods=["GET", "POST"])
@admin_required
def add_scholarship():
    if request.method == "POST":
        name = request.form["name"].strip()
        country = request.form["country"].strip()
        university = request.form.get("university", "").strip()
        level = request.form.get("level", "").strip()
        field = request.form.get("field", "").strip()
        tags = request.form.get("tags", "").strip()
        deadline = request.form.get("deadline", "").strip() or None
        link = request.form["link"].strip()
        checklist = request.form.get("checklist", "").strip()
        min_gpa_raw = request.form.get("min_gpa", "").strip()
        min_gpa = float(min_gpa_raw) if min_gpa_raw else None
        is_international_only = (
            1 if request.form.get("is_international_only") == "on" else 0
        )

        image_file = request.files.get("image_file")
        brochure_file = request.files.get("brochure_file")

        image_filename = None
        brochure_filename = None

        if image_file and image_file.filename:
            image_filename = f"img_{name.replace(' ', '_')}_{image_file.filename}"
            image_path = os.path.join(app.config["UPLOAD_FOLDER"], image_filename)
            image_file.save(image_path)

        if brochure_file and brochure_file.filename:
            brochure_filename = f"doc_{name.replace(' ', '_')}_{brochure_file.filename}"
            brochure_path = os.path.join(app.config["UPLOAD_FOLDER"], brochure_filename)
            brochure_file.save(brochure_path)

        db = get_db()
        db.execute(
            """
            INSERT INTO scholarships
            (name, country, university, level, field, tags, deadline, link,
             checklist, min_gpa, is_international_only, image_filename, brochure_filename)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                country,
                university,
                level,
                field,
                tags,
                deadline,
                link,
                checklist,
                min_gpa,
                is_international_only,
                image_filename,
                brochure_filename,
            ),
        )
        db.commit()
        return redirect(url_for("admin_dashboard"))

    return render_template("add_edit_scholarship.html", s=None)


@app.route("/admin/edit/<int:sid>", methods=["GET", "POST"])
@admin_required
def edit_scholarship(sid):
    db = get_db()
    r = db.execute("SELECT * FROM scholarships WHERE id=?", (sid,)).fetchone()
    if not r:
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":
        name = request.form["name"].strip()
        country = request.form["country"].strip()
        university = request.form.get("university", "").strip()
        level = request.form.get("level", "").strip()
        field = request.form.get("field", "").strip()
        tags = request.form.get("tags", "").strip()
        deadline = request.form.get("deadline", "").strip() or None
        link = request.form["link"].strip()
        checklist = request.form.get("checklist", "").strip()
        min_gpa_raw = request.form.get("min_gpa", "").strip()
        min_gpa = float(min_gpa_raw) if min_gpa_raw else None
        is_international_only = (
            1 if request.form.get("is_international_only") == "on" else 0
        )

        image_file = request.files.get("image_file")
        brochure_file = request.files.get("brochure_file")

        image_filename = r["image_filename"]
        brochure_filename = r["brochure_filename"]

        if image_file and image_file.filename:
            image_filename = f"img_{name.replace(' ', '_')}_{image_file.filename}"
            image_path = os.path.join(app.config["UPLOAD_FOLDER"], image_filename)
            image_file.save(image_path)

        if brochure_file and brochure_file.filename:
            brochure_filename = f"doc_{name.replace(' ', '_')}_{brochure_file.filename}"
            brochure_path = os.path.join(app.config["UPLOAD_FOLDER"], brochure_filename)
            brochure_file.save(brochure_path)

        db.execute(
            """
            UPDATE scholarships
            SET name=?, country=?, university=?, level=?, field=?, tags=?,
                deadline=?, link=?, checklist=?, min_gpa=?, is_international_only=?,
                image_filename=?, brochure_filename=?
            WHERE id=?
            """,
            (
                name,
                country,
                university,
                level,
                field,
                tags,
                deadline,
                link,
                checklist,
                min_gpa,
                is_international_only,
                image_filename,
                brochure_filename,
                sid,
            ),
        )
        db.commit()
        return redirect(url_for("admin_dashboard"))

    s = dict(r)
    s["checklist"] = (r["checklist"] or "").splitlines() if r["checklist"] else []
    return render_template("add_edit_scholarship.html", s=s)


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/eligibility", methods=["GET", "POST"])
def eligibility():
    db = get_db()
    countries = [
        row["country"]
        for row in db.execute(
            "SELECT DISTINCT country FROM scholarships WHERE country IS NOT NULL AND country != ''"
        ).fetchall()
    ]
    levels = [
        row["level"]
        for row in db.execute(
            "SELECT DISTINCT level FROM scholarships WHERE level IS NOT NULL AND level != ''"
        ).fetchall()
    ]
    fields = [
        row["field"]
        for row in db.execute(
            "SELECT DISTINCT field FROM scholarships WHERE field IS NOT NULL AND field != ''"
        ).fetchall()
    ]

    results = []

    if request.method == "POST":
        country = request.form.get("country", "").strip()
        level = request.form.get("level", "").strip()
        field = request.form.get("field", "").strip()
        gpa_raw = request.form.get("gpa", "").strip()
        gpa = float(gpa_raw) if gpa_raw else None
        is_international = request.form.get("is_international") == "on"

        query = "SELECT * FROM scholarships WHERE 1=1"
        params = []

        if country:
            query += " AND country = ?"
            params.append(country)
        if level:
            query += " AND level = ?"
            params.append(level)
        if field:
            query += " AND field = ?"
            params.append(field)
        if is_international:
            query += " AND is_international_only = 1"

        rows = db.execute(query, params).fetchall()

        for r in rows:
            if gpa is not None and r["min_gpa"] is not None:
                try:
                    if gpa < float(r["min_gpa"]):
                        continue
                except Exception:
                    pass
            results.append(dict(r))

    return render_template(
        "eligibility.html",
        countries=countries,
        levels=levels,
        fields=fields,
        results=results,
    )


@app.route("/api/eligibility", methods=["POST"])
def api_eligibility():
    data = request.get_json() or {}

    country = data.get("country", "").strip()
    level = data.get("level", "").strip()
    field = data.get("field", "").strip()
    gpa = data.get("gpa", None)
    is_international = data.get("is_international", False)

    db = get_db()

    query = "SELECT * FROM scholarships WHERE 1=1"
    params = []

    if country:
        query += " AND country = ?"
        params.append(country)
    if level:
        query += " AND level = ?"
        params.append(level)
    if field:
        query += " AND field = ?"
        params.append(field)
    if is_international:
        query += " AND is_international_only = 1"

    rows = db.execute(query, params).fetchall()

    results = []

    for r in rows:
        min_gpa = r["min_gpa"]
        if gpa is not None and min_gpa is not None:
            try:
                if float(gpa) < float(min_gpa):
                    continue
            except Exception:
                pass

        results.append(
            {
                "id": r["id"],
                "name": r["name"],
                "country": r["country"],
                "university": r["university"],
                "level": r["level"],
                "field": r["field"],
                "tags": r["tags"],
                "deadline": r["deadline"],
                "min_gpa": r["min_gpa"],
                "is_international_only": r["is_international_only"],
                "link": r["link"],
            }
        )

    return jsonify(results)


if __name__ == "__main__":
    with app.app_context():
        init_db()
        seed_admin()
        seed_scholarships_from_json()
    app.run(host="0.0.0.0", port=5000, debug=True)
