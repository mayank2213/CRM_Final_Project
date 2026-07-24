import os
import re
from functools import wraps
from pathlib import Path

import db as db_module
from flask import Flask, abort, flash, g, has_app_context, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "crm.sqlite3"
PIPELINE_STAGES = ["New", "Contacted", "Qualified", "Proposal", "Won", "Lost"]

app = Flask(__name__)
app.config.update(SECRET_KEY=os.environ.get("CRM_SECRET_KEY", "dev-only-change-me"), DATABASE=DATABASE)


class DbRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class CursorResult:
    def __init__(self, rows, rowcount=None):
        self._rows = [DbRow(row) if isinstance(row, dict) else row for row in rows]
        self._index = 0
        self._rowcount = rowcount

    def __iter__(self):
        return iter(self._rows)

    def fetchone(self):
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self):
        rows = self._rows[self._index:]
        self._index = len(self._rows)
        return rows

    @property
    def rowcount(self):
        return self._rowcount if self._rowcount is not None else len(self._rows)


class SQLDbConnection:
    def execute(self, sql, params=()):
        normalized_sql = _normalize_sql(sql)
        params = tuple(params or ())
        if normalized_sql.lstrip().upper().startswith("SELECT"):
            rows = db_module.select(normalized_sql, params)
            return CursorResult(rows)
        rowcount = db_module.query(normalized_sql, params)
        return CursorResult([], rowcount=rowcount)

    def executemany(self, sql, seq_of_params):
        for params in seq_of_params:
            self.execute(sql, params)
        return self

    def commit(self):
        db_module.commit()

    def rollback(self):
        db_module.rollback()

    def close(self):
        return None


def _normalize_sql(sql):
    if db_module.backend_name() == "sqlite":
        return sql.strip()
    normalized = sql.strip()
    normalized = normalized.replace("?", "%s")
    normalized = normalized.replace("CURRENT_TIMESTAMP", "GETDATE()")
    if re.search(r"\bSELECT\b.*?\bLIMIT\s+(\d+)\b", normalized, re.IGNORECASE | re.DOTALL):
        normalized = re.sub(
            r"\bSELECT\b(.*?)(?:\s+LIMIT\s+(\d+))\b",
            lambda m: f"SELECT TOP {m.group(2)} {m.group(1).strip()}",
            normalized,
            flags=re.IGNORECASE | re.DOTALL,
        )
    if re.match(r"^SELECT(?:\s+TOP\s+\d+)?\s+1\b", normalized, re.IGNORECASE):
        normalized = re.sub(r"^SELECT(?:\s+TOP\s+\d+)?\s+1\b", "SELECT 1 AS exists_flag", normalized, count=1, flags=re.IGNORECASE)
    return normalized


def get_db():
    if has_app_context():
        if "db" not in g:
            g.db = SQLDbConnection()
        return g.db

    if not hasattr(app, "_db"):
        app._db = SQLDbConnection()
    return app._db


@app.teardown_appcontext
def close_db(_error=None):
    if has_app_context():
        g.pop("db", None)


def init_db():
    if db_module.backend_name() == "sqlite":
        statements = [
            """CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('rep','manager')) DEFAULT 'rep')""",
            """CREATE TABLE IF NOT EXISTS companies (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, industry TEXT, website TEXT, notes TEXT, owner_id INTEGER NOT NULL REFERENCES users(id))""",
            """CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT, phone TEXT, title TEXT, company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE, owner_id INTEGER NOT NULL REFERENCES users(id))""",
            """CREATE TABLE IF NOT EXISTS pipeline_stages (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, sort_order INTEGER NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS deals (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, company_id INTEGER REFERENCES companies(id), contact_id INTEGER REFERENCES contacts(id), value REAL NOT NULL DEFAULT 0, stage_id INTEGER NOT NULL REFERENCES pipeline_stages(id), owner_id INTEGER NOT NULL REFERENCES users(id))""",
            """CREATE TABLE IF NOT EXISTS deal_stage_history (id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE, from_stage_id INTEGER REFERENCES pipeline_stages(id), to_stage_id INTEGER NOT NULL REFERENCES pipeline_stages(id), changed_by INTEGER NOT NULL REFERENCES users(id), changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS activities (id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id INTEGER REFERENCES deals(id) ON DELETE CASCADE, contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE, activity_type TEXT NOT NULL CHECK(activity_type IN ('call','email','note','meeting')), body TEXT NOT NULL, author_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
        ]
    else:
        statements = [
        """IF OBJECT_ID(N'dbo.users', N'U') IS NULL
        BEGIN
            CREATE TABLE users (
                id INT IDENTITY(1,1) PRIMARY KEY,
                name NVARCHAR(255) NOT NULL,
                email NVARCHAR(255) NOT NULL UNIQUE,
                password_hash NVARCHAR(255) NOT NULL,
                role NVARCHAR(20) NOT NULL DEFAULT 'rep' CHECK (role IN ('rep','manager'))
            )
        END""",
        """IF OBJECT_ID(N'dbo.companies', N'U') IS NULL
        BEGIN
            CREATE TABLE companies (
                id INT IDENTITY(1,1) PRIMARY KEY,
                name NVARCHAR(255) NOT NULL,
                industry NVARCHAR(255),
                website NVARCHAR(255),
                notes NVARCHAR(MAX),
                owner_id INT NOT NULL CONSTRAINT FK_companies_owner FOREIGN KEY REFERENCES users(id)
            )
        END""",
        """IF OBJECT_ID(N'dbo.contacts', N'U') IS NULL
        BEGIN
            CREATE TABLE contacts (
                id INT IDENTITY(1,1) PRIMARY KEY,
                name NVARCHAR(255) NOT NULL,
                email NVARCHAR(255),
                phone NVARCHAR(50),
                title NVARCHAR(255),
                company_id INT NOT NULL CONSTRAINT FK_contacts_company FOREIGN KEY REFERENCES companies(id) ON DELETE CASCADE,
                owner_id INT NOT NULL CONSTRAINT FK_contacts_owner FOREIGN KEY REFERENCES users(id)
            )
        END""",
        """IF OBJECT_ID(N'dbo.pipeline_stages', N'U') IS NULL
        BEGIN
            CREATE TABLE pipeline_stages (
                id INT IDENTITY(1,1) PRIMARY KEY,
                name NVARCHAR(100) NOT NULL UNIQUE,
                sort_order INT NOT NULL
            )
        END""",
        """IF OBJECT_ID(N'dbo.deals', N'U') IS NULL
        BEGIN
            CREATE TABLE deals (
                id INT IDENTITY(1,1) PRIMARY KEY,
                title NVARCHAR(255) NOT NULL,
                company_id INT CONSTRAINT FK_deals_company FOREIGN KEY REFERENCES companies(id),
                contact_id INT CONSTRAINT FK_deals_contact FOREIGN KEY REFERENCES contacts(id),
                value DECIMAL(12,2) NOT NULL DEFAULT 0,
                stage_id INT NOT NULL CONSTRAINT FK_deals_stage FOREIGN KEY REFERENCES pipeline_stages(id),
                owner_id INT NOT NULL CONSTRAINT FK_deals_owner FOREIGN KEY REFERENCES users(id)
            )
        END""",
        """IF OBJECT_ID(N'dbo.deal_stage_history', N'U') IS NULL
        BEGIN
            CREATE TABLE deal_stage_history (
                id INT IDENTITY(1,1) PRIMARY KEY,
                deal_id INT NOT NULL CONSTRAINT FK_history_deal FOREIGN KEY REFERENCES deals(id) ON DELETE CASCADE,
                from_stage_id INT CONSTRAINT FK_history_from_stage FOREIGN KEY REFERENCES pipeline_stages(id),
                to_stage_id INT NOT NULL CONSTRAINT FK_history_to_stage FOREIGN KEY REFERENCES pipeline_stages(id),
                changed_by INT NOT NULL CONSTRAINT FK_history_user FOREIGN KEY REFERENCES users(id),
                changed_at DATETIME2 NOT NULL DEFAULT GETDATE()
            )
        END""",
        """IF OBJECT_ID(N'dbo.activities', N'U') IS NULL
        BEGIN
            CREATE TABLE activities (
                id INT IDENTITY(1,1) PRIMARY KEY,
                deal_id INT CONSTRAINT FK_activities_deal FOREIGN KEY REFERENCES deals(id) ON DELETE CASCADE,
                contact_id INT CONSTRAINT FK_activities_contact FOREIGN KEY REFERENCES contacts(id) ON DELETE CASCADE,
                activity_type NVARCHAR(20) NOT NULL CHECK (activity_type IN ('call','email','note','meeting')),
                body NVARCHAR(MAX) NOT NULL,
                author_id INT NOT NULL CONSTRAINT FK_activities_author FOREIGN KEY REFERENCES users(id),
                created_at DATETIME2 NOT NULL DEFAULT GETDATE()
            )
        END""",
        ]
    for stmt in statements:
        db_module.query(stmt)
    db_module.commit()


def seed_db():
    init_db()
    db = get_db()
    if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        db.executemany("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)", [
            ("Mayank", "mayank@example.com", generate_password_hash("demo123"), "manager"),
            ("Uday", "uday@example.com", generate_password_hash("demo123"), "rep"),
        ])
    for index, name in enumerate(PIPELINE_STAGES):
        if not db.execute("SELECT id FROM pipeline_stages WHERE name=?", (name,)).fetchone():
            db.execute("INSERT INTO pipeline_stages(name,sort_order) VALUES(?,?)", (name, index))
    manager = db.execute("SELECT id FROM users WHERE email='mayank@example.com'").fetchone()[0]
    if not db.execute("SELECT 1 FROM companies LIMIT 1").fetchone():
        companies = [("Northstar Labs", "SaaS", "https://northstar.example", "Expansion-ready account", manager), ("Greenline Retail", "Retail", "https://greenline.example", "Needs quarterly review", manager), ("Bluebird Health", "Healthcare", "", "", manager), ("Orbit Logistics", "Logistics", "", "", manager), ("Cedar Finance", "Finance", "", "", manager)]
        db.executemany("INSERT INTO companies(name,industry,website,notes,owner_id) VALUES(?,?,?,?,?)", companies)
    company_rows = db.execute("SELECT id,name FROM companies ORDER BY id").fetchall()
    if not db.execute("SELECT 1 FROM contacts LIMIT 1").fetchone():
        contacts = [("Aarav Mehta", "aarav@northstar.example", "+91 90000 10001", "VP Sales", 0), ("Riya Shah", "riya@greenline.example", "+91 90000 10002", "Operations Lead", 1), ("Nikhil Rao", "nikhil@bluebird.example", "", "Director", 2), ("Sara Khan", "sara@orbit.example", "", "Buyer", 3), ("Dev Patel", "dev@cedar.example", "", "CFO", 4)]
        db.executemany("INSERT INTO contacts(name,email,phone,title,company_id,owner_id) VALUES(?,?,?,?,?,?)", [(n,e,p,t,company_rows[i][0],manager) for n,e,p,t,i in contacts])
    stage_ids = {row['name']: row['id'] for row in db.execute("SELECT * FROM pipeline_stages")}
    contact_rows = db.execute("SELECT id,company_id FROM contacts ORDER BY id").fetchall()
    if contact_rows and not db.execute("SELECT 1 FROM deals LIMIT 1").fetchone():
        for i, stage in enumerate(PIPELINE_STAGES):
            contact = contact_rows[i % len(contact_rows)]
            db.execute("INSERT INTO deals(title,company_id,contact_id,value,stage_id,owner_id) VALUES(?,?,?,?,?,?)", (f"{stage} opportunity", contact['company_id'], contact['id'], (i + 1) * 15000, stage_ids[stage], manager))
    db.commit()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def load_user():
    user_id = session.get("user_id")
    g.user = get_db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone() if user_id else None


@app.context_processor
def globals_for_templates():
    return {"pipeline_stages": PIPELINE_STAGES}


def owned_clause(alias=""):
    if g.user["role"] == "manager":
        return "", ()
    prefix = f"{alias}." if alias else ""
    return f" WHERE {prefix}owner_id=?", (g.user["id"],)


def visible_row(table, row_id):
    row = get_db().execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
    if not row or (g.user["role"] != "manager" and row["owner_id"] != g.user["id"]):
        abort(404)
    return row


def visible_options(table):
    where, params = owned_clause()
    return get_db().execute(f"SELECT * FROM {table}{where} ORDER BY name", params).fetchall()


def deal_with_details(deal_id):
    deal = visible_row("deals", deal_id)
    return get_db().execute("""SELECT deals.*, companies.name company_name, contacts.name contact_name,
        pipeline_stages.name stage_name, users.name owner_name FROM deals
        LEFT JOIN companies ON companies.id=deals.company_id LEFT JOIN contacts ON contacts.id=deals.contact_id
        JOIN pipeline_stages ON pipeline_stages.id=deals.stage_id JOIN users ON users.id=deals.owner_id WHERE deals.id=?""", (deal["id"],)).fetchone()


def transition_deal(deal, new_stage_id):
    """Single audit path used by direct edits and board transitions."""
    if deal["stage_id"] != new_stage_id:
        db = get_db()
        db.execute("UPDATE deals SET stage_id=? WHERE id=?", (new_stage_id, deal["id"]))
        db.execute("INSERT INTO deal_stage_history(deal_id,from_stage_id,to_stage_id,changed_by) VALUES(?,?,?,?)", (deal["id"], deal["stage_id"], new_stage_id, g.user["id"]))


@app.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        user = get_db().execute("SELECT * FROM users WHERE email=?", (request.form.get("email", "").strip().lower(),)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear(); session["user_id"] = user["id"]
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Check your email and password.", "danger")
    return render_template("login.html")


@app.post("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    db = get_db(); where, params = owned_clause("deals")
    stage_counts = db.execute(f"""SELECT pipeline_stages.name, COUNT(deals.id) count FROM pipeline_stages
        LEFT JOIN deals ON deals.stage_id=pipeline_stages.id {'AND deals.owner_id=?' if where else ''}
        GROUP BY pipeline_stages.id, pipeline_stages.name, pipeline_stages.sort_order
        ORDER BY pipeline_stages.sort_order""", params).fetchall()
    total_value_row = db.execute(f"SELECT COALESCE(SUM(value),0) AS total_value FROM deals{where}", params).fetchone()
    company_where, company_params = owned_clause("companies")
    contact_where, contact_params = owned_clause("contacts")
    company_count_row = db.execute(f"SELECT COUNT(*) AS company_count FROM companies{company_where}", company_params).fetchone()
    contact_count_row = db.execute(f"SELECT COUNT(*) AS contact_count FROM contacts{contact_where}", contact_params).fetchone()
    return render_template("dashboard.html", stage_counts=stage_counts,
                           total_value=total_value_row["total_value"] if total_value_row else 0,
                           company_count=company_count_row["company_count"] if company_count_row else 0,
                           contact_count=contact_count_row["contact_count"] if contact_count_row else 0)


@app.route("/companies")
@login_required
def companies():
    db = get_db(); where, params = owned_clause("companies"); query = request.args.get("q", "").strip()
    if query:
        where += (" AND " if where else " WHERE ") + "(name LIKE ? OR industry LIKE ?)"; params += (f"%{query}%", f"%{query}%")
    return render_template("companies.html", companies=db.execute(f"SELECT * FROM companies{where} ORDER BY name", params).fetchall(), query=query)


@app.post("/companies/save")
@login_required
def save_company():
    db = get_db(); company_id = request.form.get("id"); values = tuple(request.form.get(x, "").strip() for x in ("name", "industry", "website", "notes"))
    if not values[0]: flash("Company name is required.", "danger")
    elif company_id:
        visible_row("companies", company_id); db.execute("UPDATE companies SET name=?,industry=?,website=?,notes=? WHERE id=?", (*values, company_id)); db.commit(); flash("Company updated.", "success")
    else:
        db.execute("INSERT INTO companies(name,industry,website,notes,owner_id) VALUES(?,?,?,?,?)", (*values, g.user["id"])); db.commit(); flash("Company created.", "success")
    return redirect(url_for("companies"))


@app.route("/contacts")
@login_required
def contacts():
    db = get_db(); where, params = owned_clause("contacts"); query = request.args.get("q", "").strip()
    if query:
        where += (" AND " if where else " WHERE ") + "(contacts.name LIKE ? OR contacts.email LIKE ? OR companies.name LIKE ?)"; params += (f"%{query}%",) * 3
    rows = db.execute(f"SELECT contacts.*,companies.name company_name FROM contacts JOIN companies ON companies.id=contacts.company_id{where} ORDER BY contacts.name", params).fetchall()
    return render_template("contacts.html", contacts=rows, companies=visible_options("companies"), query=query)


@app.post("/contacts/save")
@login_required
def save_contact():
    db = get_db(); contact_id = request.form.get("id"); company_id = request.form.get("company_id"); values = (request.form.get("name", "").strip(), request.form.get("email", "").strip(), request.form.get("phone", "").strip(), request.form.get("title", "").strip(), company_id)
    if not values[0] or not company_id: flash("Name and company are required.", "danger")
    else:
        visible_row("companies", company_id)
        if contact_id:
            visible_row("contacts", contact_id); db.execute("UPDATE contacts SET name=?,email=?,phone=?,title=?,company_id=? WHERE id=?", (*values, contact_id)); flash("Contact updated.", "success")
        else:
            db.execute("INSERT INTO contacts(name,email,phone,title,company_id,owner_id) VALUES(?,?,?,?,?,?)", (*values, g.user["id"])); flash("Contact created.", "success")
        db.commit()
    return redirect(url_for("contacts"))


@app.route("/contacts/<int:contact_id>")
@login_required
def contact_detail(contact_id):
    contact = visible_row("contacts", contact_id); db = get_db()
    activities = db.execute("""SELECT activities.*,users.name author_name,deals.title deal_title FROM activities
        JOIN users ON users.id=activities.author_id LEFT JOIN deals ON deals.id=activities.deal_id
        WHERE activities.contact_id=? ORDER BY activities.created_at DESC, activities.id DESC""", (contact_id,)).fetchall()
    return render_template("contact_detail.html", contact=contact, company=visible_row("companies", contact["company_id"]), activities=activities)


@app.route("/deals")
@login_required
def deals():
    db = get_db(); where, params = owned_clause("deals")
    rows = db.execute(f"""SELECT deals.*,companies.name company_name,contacts.name contact_name,pipeline_stages.name stage_name,users.name owner_name FROM deals
        LEFT JOIN companies ON companies.id=deals.company_id LEFT JOIN contacts ON contacts.id=deals.contact_id JOIN pipeline_stages ON pipeline_stages.id=deals.stage_id JOIN users ON users.id=deals.owner_id{where} ORDER BY deals.id DESC""", params).fetchall()
    owners = db.execute("SELECT * FROM users ORDER BY name").fetchall() if g.user["role"] == "manager" else [g.user]
    return render_template("deals.html", deals=rows, companies=visible_options("companies"), contacts=visible_options("contacts"), stages=db.execute("SELECT * FROM pipeline_stages ORDER BY sort_order").fetchall(), owners=owners)


def deal_form_values():
    title = request.form.get("title", "").strip(); company_id = request.form.get("company_id") or None; contact_id = request.form.get("contact_id") or None
    try: value = float(request.form.get("value", ""))
    except ValueError: value = -1
    stage_id = request.form.get("stage_id"); owner_id = request.form.get("owner_id") or g.user["id"]
    if not title or not stage_id or value < 0: return None, "Title, a pipeline stage, and a non-negative value are required."
    if company_id: visible_row("companies", company_id)
    if contact_id:
        contact = visible_row("contacts", contact_id)
        if company_id and contact["company_id"] != int(company_id): return None, "The selected contact belongs to another company."
    if g.user["role"] != "manager": owner_id = g.user["id"]
    elif not get_db().execute("SELECT 1 FROM users WHERE id=?", (owner_id,)).fetchone(): return None, "Choose a valid owner."
    return (title, company_id, contact_id, value, stage_id, owner_id), None


@app.post("/deals/save")
@login_required
def save_deal():
    db = get_db(); deal_id = request.form.get("id"); values, error = deal_form_values()
    if error: flash(error, "danger")
    elif deal_id:
        deal = visible_row("deals", deal_id)
        db.execute("UPDATE deals SET title=?,company_id=?,contact_id=?,value=?,owner_id=? WHERE id=?", (*values[:4], values[5], deal_id))
        transition_deal(deal, int(values[4])); db.commit(); flash("Deal updated.", "success")
    else:
        db.execute("INSERT INTO deals(title,company_id,contact_id,value,stage_id,owner_id) VALUES(?,?,?,?,?,?)", values); db.commit(); flash("Deal created.", "success")
    return redirect(url_for("deals"))


@app.route("/pipeline")
@login_required
def pipeline():
    db = get_db(); where, params = owned_clause("deals")
    rows = db.execute(f"""SELECT deals.*,companies.name company_name,contacts.name contact_name,pipeline_stages.name stage_name FROM deals
        LEFT JOIN companies ON companies.id=deals.company_id LEFT JOIN contacts ON contacts.id=deals.contact_id JOIN pipeline_stages ON pipeline_stages.id=deals.stage_id{where} ORDER BY deals.id DESC""", params).fetchall()
    stages = db.execute("SELECT * FROM pipeline_stages ORDER BY sort_order").fetchall()
    return render_template("pipeline.html", stages=stages, board={stage['id']: [r for r in rows if r['stage_id'] == stage['id']] for stage in stages})


@app.post("/deals/<int:deal_id>/stage")
@login_required
def update_deal_stage(deal_id):
    deal = visible_row("deals", deal_id); stage_id = request.form.get("stage_id")
    if not get_db().execute("SELECT 1 FROM pipeline_stages WHERE id=?", (stage_id,)).fetchone(): abort(400)
    transition_deal(deal, int(stage_id)); get_db().commit(); flash("Deal stage updated.", "success")
    return redirect(request.referrer or url_for("pipeline"))


@app.route("/deals/<int:deal_id>")
@login_required
def deal_detail(deal_id):
    deal = deal_with_details(deal_id); db = get_db()
    activities = db.execute("SELECT activities.*,users.name author_name FROM activities JOIN users ON users.id=activities.author_id WHERE activities.deal_id=? ORDER BY activities.created_at DESC,activities.id DESC", (deal_id,)).fetchall()
    history = db.execute("""SELECT h.*,old.name from_stage,new.name to_stage,users.name actor_name FROM deal_stage_history h
        LEFT JOIN pipeline_stages old ON old.id=h.from_stage_id JOIN pipeline_stages new ON new.id=h.to_stage_id JOIN users ON users.id=h.changed_by WHERE h.deal_id=? ORDER BY h.changed_at DESC,h.id DESC""", (deal_id,)).fetchall()
    return render_template("deal_detail.html", deal=deal, activities=activities, history=history)


@app.post("/activities/save")
@login_required
def save_activity():
    deal_id = request.form.get("deal_id") or None; contact_id = request.form.get("contact_id") or None; kind = request.form.get("activity_type"); body = request.form.get("body", "").strip()
    if deal_id: visible_row("deals", deal_id)
    if contact_id: visible_row("contacts", contact_id)
    if not (deal_id or contact_id) or kind not in {"call", "email", "note", "meeting"} or not body: flash("Choose a record, activity type, and description.", "danger")
    else:
        get_db().execute("INSERT INTO activities(deal_id,contact_id,activity_type,body,author_id) VALUES(?,?,?,?,?)", (deal_id, contact_id, kind, body, g.user["id"])); get_db().commit(); flash("Activity added.", "success")
    return redirect(request.referrer or url_for("dashboard"))


if __name__ == "__main__":
    with app.app_context(): seed_db()
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
