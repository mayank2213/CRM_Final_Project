import os
import sqlite3
from functools import wraps
from pathlib import Path

from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "crm.sqlite3"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("CRM_SECRET_KEY", "dev-only-change-me")
app.config["DATABASE"] = DATABASE

PIPELINE_STAGES = ["New", "Contacted", "Qualified", "Proposal", "Won", "Lost"]


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('rep', 'manager')) DEFAULT 'rep'
        );
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            industry TEXT,
            website TEXT,
            notes TEXT,
            owner_id INTEGER NOT NULL REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            title TEXT,
            company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
            owner_id INTEGER NOT NULL REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS pipeline_stages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            sort_order INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS deals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            company_id INTEGER REFERENCES companies(id),
            contact_id INTEGER REFERENCES contacts(id),
            value REAL DEFAULT 0,
            stage_id INTEGER REFERENCES pipeline_stages(id),
            owner_id INTEGER NOT NULL REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS deal_stage_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
            from_stage_id INTEGER REFERENCES pipeline_stages(id),
            to_stage_id INTEGER NOT NULL REFERENCES pipeline_stages(id),
            changed_by INTEGER NOT NULL REFERENCES users(id),
            changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS activities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id INTEGER REFERENCES deals(id) ON DELETE CASCADE,
            contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
            activity_type TEXT NOT NULL,
            body TEXT NOT NULL,
            author_id INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.commit()


def seed_db():
    init_db()
    db = get_db()
    if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return
    db.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)", ("Mayank", "mayank@example.com", generate_password_hash("demo123"), "manager"))
    db.execute("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)", ("Uday", "uday@example.com", generate_password_hash("demo123"), "rep"))
    stages = [(name, index) for index, name in enumerate(PIPELINE_STAGES)]
    db.executemany("INSERT INTO pipeline_stages(name,sort_order) VALUES(?,?)", stages)
    owner = db.execute("SELECT id FROM users WHERE email = 'mayank@example.com'").fetchone()[0]
    for company in [("Northstar Labs", "SaaS", "https://northstar.example", "Expansion-ready account"), ("Greenline Retail", "Retail", "https://greenline.example", "Needs quarterly review")]:
        db.execute("INSERT INTO companies(name,industry,website,notes,owner_id) VALUES(?,?,?,?,?)", (*company, owner))
    companies = db.execute("SELECT id,name FROM companies").fetchall()
    db.executemany("INSERT INTO contacts(name,email,phone,title,company_id,owner_id) VALUES(?,?,?,?,?,?)", [
        ("Aarav Mehta", "aarav@northstar.example", "+91 90000 10001", "VP Sales", companies[0][0], owner),
        ("Riya Shah", "riya@greenline.example", "+91 90000 10002", "Operations Lead", companies[1][0], owner),
    ])
    db.commit()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def load_user():
    user_id = session.get("user_id")
    g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone() if user_id else None


@app.context_processor
def inject_theme():
    return {"pipeline_stages": PIPELINE_STAGES}


@app.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (request.form["email"].strip().lower(),)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form["password"]):
            session.clear(); session["user_id"] = user["id"]
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Check your email and password.", "danger")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    db = get_db()
    company_count = db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    contact_count = db.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    return render_template("dashboard.html", company_count=company_count, contact_count=contact_count)


def visible_filter(table):
    if g.user["role"] == "manager":
        return "", ()
    return " WHERE owner_id = ?", (g.user["id"],)


@app.route("/companies")
@login_required
def companies():
    db = get_db(); where, params = visible_filter("companies")
    query = request.args.get("q", "").strip()
    if query:
        where += (" AND " if where else " WHERE ") + "(name LIKE ? OR industry LIKE ?)"; params += (f"%{query}%", f"%{query}%")
    rows = db.execute(f"SELECT * FROM companies{where} ORDER BY name", params).fetchall()
    return render_template("companies.html", companies=rows, query=query)


@app.post("/companies/save")
@login_required
def save_company():
    db = get_db(); company_id = request.form.get("id")
    values = (request.form["name"].strip(), request.form.get("industry", "").strip(), request.form.get("website", "").strip(), request.form.get("notes", "").strip())
    if not values[0]:
        flash("Company name is required.", "danger")
    elif company_id:
        db.execute("UPDATE companies SET name=?,industry=?,website=?,notes=? WHERE id=?", (*values, company_id))
        db.commit(); flash("Company updated.", "success")
    else:
        db.execute("INSERT INTO companies(name,industry,website,notes,owner_id) VALUES(?,?,?,?,?)", (*values, g.user["id"])); db.commit(); flash("Company created.", "success")
    return redirect(url_for("companies"))


@app.route("/contacts")
@login_required
def contacts():
    db = get_db(); where, params = visible_filter("contacts")
    query = request.args.get("q", "").strip()
    if query:
        where += (" AND " if where else " WHERE ") + "(contacts.name LIKE ? OR contacts.email LIKE ? OR companies.name LIKE ?)"; params += (f"%{query}%", f"%{query}%", f"%{query}%")
    rows = db.execute(f"SELECT contacts.*, companies.name AS company_name FROM contacts JOIN companies ON companies.id=contacts.company_id{where} ORDER BY contacts.name", params).fetchall()
    company_where, company_params = visible_filter("companies")
    company_rows = db.execute(f"SELECT * FROM companies{company_where} ORDER BY name", company_params).fetchall()
    return render_template("contacts.html", contacts=rows, companies=company_rows, query=query)


@app.post("/contacts/save")
@login_required
def save_contact():
    db = get_db(); contact_id = request.form.get("id")
    values = (request.form["name"].strip(), request.form.get("email", "").strip(), request.form.get("phone", "").strip(), request.form.get("title", "").strip(), request.form["company_id"])
    if not values[0] or not values[4]:
        flash("Name and company are required.", "danger")
    else:
        company_where, company_params = visible_filter("companies")
        company_query = f"SELECT id FROM companies{company_where} AND id = ?" if company_where else "SELECT id FROM companies WHERE id = ?"
        company = db.execute(company_query, (*company_params, values[4])).fetchone()
        if company is None:
            flash("Choose a company you are allowed to use.", "danger")
        elif contact_id:
            owner_clause = "" if g.user["role"] == "manager" else " AND owner_id = ?"
            owner_params = () if g.user["role"] == "manager" else (g.user["id"],)
            result = db.execute(f"UPDATE contacts SET name=?,email=?,phone=?,title=?,company_id=? WHERE id=?{owner_clause}", (*values, contact_id, *owner_params))
            if result.rowcount == 0:
                flash("Contact not found or access denied.", "danger")
            else:
                db.commit(); flash("Contact updated.", "success")
        else:
            db.execute("INSERT INTO contacts(name,email,phone,title,company_id,owner_id) VALUES(?,?,?,?,?,?)", (*values, g.user["id"])); db.commit(); flash("Contact created.", "success")
    return redirect(url_for("contacts"))


if __name__ == "__main__":
    with app.app_context():
        seed_db()
    app.run(debug=True)
