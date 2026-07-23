import os
import sqlite3
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "crm.sqlite3"
PIPELINE_STAGES = ["New", "Contacted", "Qualified", "Proposal", "Won", "Lost"]

app = Flask(__name__)
app.config.update(SECRET_KEY=os.environ.get("CRM_SECRET_KEY", "dev-only-change-me"), DATABASE=DATABASE)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    get_db().executescript("""
    CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('rep','manager')) DEFAULT 'rep');
    CREATE TABLE IF NOT EXISTS companies (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, industry TEXT, website TEXT, notes TEXT, owner_id INTEGER NOT NULL REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT, phone TEXT, title TEXT, company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE, owner_id INTEGER NOT NULL REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS pipeline_stages (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, sort_order INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS deals (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, company_id INTEGER REFERENCES companies(id), contact_id INTEGER REFERENCES contacts(id), value REAL NOT NULL DEFAULT 0, stage_id INTEGER NOT NULL REFERENCES pipeline_stages(id), owner_id INTEGER NOT NULL REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS deal_stage_history (id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE, from_stage_id INTEGER REFERENCES pipeline_stages(id), to_stage_id INTEGER NOT NULL REFERENCES pipeline_stages(id), changed_by INTEGER NOT NULL REFERENCES users(id), changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS activities (id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id INTEGER REFERENCES deals(id) ON DELETE CASCADE, contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE, activity_type TEXT NOT NULL CHECK(activity_type IN ('call','email','note','meeting')), body TEXT NOT NULL, author_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    """)
    get_db().commit()


def seed_db():
    init_db(); db = get_db()
    if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return
    db.executemany("INSERT INTO users(name,email,password_hash,role) VALUES(?,?,?,?)", [
        ("Mayank", "mayank@example.com", generate_password_hash("demo123"), "manager"),
        ("Uday", "uday@example.com", generate_password_hash("demo123"), "rep"),
    ])
    db.executemany("INSERT INTO pipeline_stages(name,sort_order) VALUES(?,?)", [(name, i) for i, name in enumerate(PIPELINE_STAGES)])
    manager = db.execute("SELECT id FROM users WHERE email='mayank@example.com'").fetchone()[0]
    companies = [("Northstar Labs", "SaaS", "https://northstar.example", "Expansion-ready account", manager), ("Greenline Retail", "Retail", "https://greenline.example", "Needs quarterly review", manager), ("Bluebird Health", "Healthcare", "", "", manager), ("Orbit Logistics", "Logistics", "", "", manager), ("Cedar Finance", "Finance", "", "", manager)]
    db.executemany("INSERT INTO companies(name,industry,website,notes,owner_id) VALUES(?,?,?,?,?)", companies)
    company_rows = db.execute("SELECT id,name FROM companies ORDER BY id").fetchall()
    contacts = [("Aarav Mehta", "aarav@northstar.example", "+91 90000 10001", "VP Sales", 0), ("Riya Shah", "riya@greenline.example", "+91 90000 10002", "Operations Lead", 1), ("Nikhil Rao", "nikhil@bluebird.example", "", "Director", 2), ("Sara Khan", "sara@orbit.example", "", "Buyer", 3), ("Dev Patel", "dev@cedar.example", "", "CFO", 4)]
    db.executemany("INSERT INTO contacts(name,email,phone,title,company_id,owner_id) VALUES(?,?,?,?,?,?)", [(n,e,p,t,company_rows[i][0],manager) for n,e,p,t,i in contacts])
    stage_ids = {row['name']: row['id'] for row in db.execute("SELECT * FROM pipeline_stages")}
    contact_rows = db.execute("SELECT id,company_id FROM contacts ORDER BY id").fetchall()
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
        GROUP BY pipeline_stages.id ORDER BY pipeline_stages.sort_order""", params).fetchall()
    total_value = db.execute(f"SELECT COALESCE(SUM(value),0) FROM deals{where}", params).fetchone()[0]
    company_where, company_params = owned_clause("companies")
    contact_where, contact_params = owned_clause("contacts")
    return render_template("dashboard.html", stage_counts=stage_counts, total_value=total_value,
                           company_count=db.execute(f"SELECT COUNT(*) FROM companies{company_where}", company_params).fetchone()[0],
                           contact_count=db.execute(f"SELECT COUNT(*) FROM contacts{contact_where}", contact_params).fetchone()[0])


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
