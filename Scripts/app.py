import secrets
import os

from datetime import datetime, timezone, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, abort
from flask_login import (
    login_user, logout_user, login_required, current_user,
)
from werkzeug.security import generate_password_hash, check_password_hash

import db
import scheduler
import notifier
import crypto_util
from auth import login_manager, User, admin_required

app = Flask(__name__)

# Secret key persists across restarts (stored in a local file) so logged-in
# sessions don't get invalidated every time you restart the server.
SECRET_KEY_FILE = "secret_key.txt"
if os.path.exists(SECRET_KEY_FILE):
    app.secret_key = open(SECRET_KEY_FILE).read().strip()
else:
    key = secrets.token_hex(32)
    with open(SECRET_KEY_FILE, "w") as f:
        f.write(key)
    app.secret_key = key

login_manager.init_app(app)


@app.template_filter("fmt_dt")
def fmt_dt(value):
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    now = datetime.now(timezone.utc)
    delta = now - dt
    seconds = delta.total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        mins = int(seconds // 60)
        return f"{mins} min ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours}h ago"
    return dt.strftime("%d %b, %H:%M")


@app.template_global()
def next_run_str(job):
    if job["is_running"]:
        return "running now"
    if not job["enabled"]:
        return "—"
    if not job["last_run_at"]:
        return "due now"
    last = datetime.fromisoformat(job["last_run_at"])
    next_time = last + timedelta(minutes=job["interval_minutes"])
    seconds = (next_time - datetime.now(timezone.utc)).total_seconds()
    if seconds <= 0:
        return "due now"
    if seconds < 60:
        return "in <1 min"
    mins = int(seconds // 60)
    if mins < 60:
        return f"in {mins} min"
    hours = mins // 60
    rem = mins % 60
    if hours < 24:
        return f"in {hours}h {rem}m" if rem else f"in {hours}h"
    return "on " + next_time.strftime("%d %b, %H:%M")


# --- auth routes ---

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form["email"].strip()
        password = request.form["password"]
        row = db.get_user_by_email(email)
        if row is None or not check_password_hash(row["password_hash"], password):
            flash("Incorrect username or password.", "error")
            return render_template("login.html")
        if not row["is_approved"]:
            flash("Your account is awaiting admin approval.", "error")
            return render_template("login.html")
        login_user(User(row))
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        email = request.form["email"].strip()
        password = request.form["password"]
        confirm = request.form["confirm"]

        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("signup.html")
        if password != confirm:
            flash("Passwords did not match.", "error")
            return render_template("signup.html")
        if len(password) < 8:
            flash("Password should be at least 8 characters.", "error")
            return render_template("signup.html")
        if db.get_user_by_email(email):
            flash("An account with that username already exists.", "error")
            return render_template("signup.html")

        db.create_user(email, generate_password_hash(password), is_admin=0, is_approved=0)
        db.log_event(email, "signup_requested", f"New sign-up awaiting approval: {email}")
        flash("Account created. An admin needs to approve it before you can log in.", "success")
        return redirect(url_for("login"))
    return render_template("signup.html")


# --- admin: user management ---

@app.route("/admin/users")
@login_required
@admin_required
def admin_users():
    return render_template(
        "admin_users.html",
        users=db.list_users(),
        pending=db.list_pending_users(),
    )


@app.route("/admin/users/<int:user_id>/approve", methods=["POST"])
@login_required
@admin_required
def approve_user(user_id):
    target = db.get_user_by_id(user_id)
    db.set_user_approved(user_id, True)
    if target:
        db.log_event(current_user.email, "user_approved", f"Approved user: {target['email']}")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/reject", methods=["POST"])
@login_required
@admin_required
def reject_user(user_id):
    target = db.get_user_by_id(user_id)
    db.delete_user(user_id)
    if target:
        db.log_event(current_user.email, "user_rejected", f"Rejected sign-up: {target['email']}")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/toggle_admin", methods=["POST"])
@login_required
@admin_required
def toggle_admin(user_id):
    if str(user_id) == current_user.id:
        flash("You can't remove your own admin status.", "error")
        return redirect(url_for("admin_users"))
    target = db.get_user_by_id(user_id)
    db.set_user_admin(user_id, not target["is_admin"])
    action = "Removed admin from" if target["is_admin"] else "Made admin:"
    db.log_event(current_user.email, "user_admin_toggled", f"{action} {target['email']}")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_user(user_id):
    if str(user_id) == current_user.id:
        flash("You can't delete your own account.", "error")
        return redirect(url_for("admin_users"))
    target = db.get_user_by_id(user_id)
    db.delete_user(user_id)
    if target:
        db.log_event(current_user.email, "user_deleted", f"Deleted user: {target['email']}")
    return redirect(url_for("admin_users"))


# --- main app (all require login) ---

@app.route("/")
@login_required
def index():
    jobs = db.list_jobs()
    return render_template("index.html", jobs=jobs)


@app.route("/jobs/new", methods=["GET", "POST"])
@login_required
def new_job():
    if request.method == "POST":
        character_id = int(request.form["character_id"])
        road_names = [r.strip() for r in request.form.getlist("road_name") if r.strip()]
        gob_name = request.form["gob_name"].strip()
        if not road_names:
            flash("Add at least one road.", "error")
            return redirect(url_for("new_job"))
        job_id = db.add_job(
            character_id=character_id,
            road_names=road_names,
            gob_name=gob_name,
            interval_minutes=int(request.form["interval_minutes"]),
            created_by=int(current_user.id),
        )
        char = db.get_character(character_id)
        db.log_event(current_user.email, "job_created",
                      f"Created job for {char['name']} ({char['account_label']}): "
                      f"{', '.join(road_names)} watching for {gob_name}")
        return redirect(url_for("index"))
    characters = db.list_characters()
    return render_template("job_form.html", characters=characters, job=None, job_roads=[])


@app.route("/jobs/<int:job_id>/edit", methods=["GET", "POST"])
@login_required
def edit_job(job_id):
    job = db.get_job(job_id)
    if request.method == "POST":
        road_names = [r.strip() for r in request.form.getlist("road_name") if r.strip()]
        gob_name = request.form["gob_name"].strip()
        if not road_names:
            flash("Add at least one road.", "error")
            return redirect(url_for("edit_job", job_id=job_id))
        db.update_job(
            job_id,
            character_id=int(request.form["character_id"]),
            road_names=road_names,
            gob_name=gob_name,
            interval_minutes=int(request.form["interval_minutes"]),
        )
        db.log_event(current_user.email, "job_edited",
                      f"Edited job for {job['character_name']} ({job['account_label']}): "
                      f"now {', '.join(road_names)} watching for {gob_name}")
        return redirect(url_for("index"))
    characters = db.list_characters()
    job_roads = db.get_job_roads(job_id)
    return render_template("job_form.html", characters=characters, job=job, job_roads=job_roads)


@app.route("/jobs/<int:job_id>/delete", methods=["POST"])
@login_required
def delete_job(job_id):
    job = db.get_job(job_id)
    db.delete_job(job_id)
    if job:
        db.log_event(current_user.email, "job_deleted",
                      f"Deleted job for {job['character_name']} ({job['account_label']}): "
                      f"{job['roads_display']} watching for {job['gob_name']}")
    return redirect(url_for("index"))


@app.route("/jobs/<int:job_id>/toggle", methods=["POST"])
@login_required
def toggle_job(job_id):
    job = db.get_job(job_id)
    db.set_job_enabled(job_id, not job["enabled"])
    action = "disabled" if job["enabled"] else "enabled"
    db.log_event(current_user.email, "job_toggled",
                  f"{action.capitalize()} job for {job['character_name']}: {job['roads_display']} → {job['gob_name']}")
    return redirect(url_for("index"))


@app.route("/jobs/<int:job_id>/run_now", methods=["POST"])
@login_required
def run_now(job_id):
    job = db.get_job(job_id)
    scheduler.run_now(job_id)
    if job:
        db.log_event(current_user.email, "job_run_triggered",
                      f"Manually triggered job for {job['character_name']}: "
                      f"{job['roads_display']} → {job['gob_name']}")
    return redirect(url_for("index"))


@app.route("/accounts", methods=["GET", "POST"])
@login_required
def accounts():
    if request.method == "POST":
        password = request.form.get("password", "").strip()
        if not password:
            flash("A password is required — this app never relies on cached login tokens.", "error")
            return redirect(url_for("accounts"))
        label = request.form["label"].strip()
        username = request.form["username"].strip()
        db.add_account(label, username, crypto_util.encrypt(password))
        db.log_event(current_user.email, "account_created", f"Added account: {label} ({username})")
        return redirect(url_for("accounts"))
    return render_template("accounts.html", accounts=db.list_accounts(), characters=db.list_characters())


@app.route("/accounts/<int:account_id>/edit", methods=["GET", "POST"])
@login_required
def edit_account(account_id):
    account = db.get_account(account_id)
    if request.method == "POST":
        password = request.form.get("password", "").strip()
        if not password and not account["password_encrypted"]:
            flash("This account has no saved password yet — a password is required.", "error")
            return render_template("account_edit.html", account=account)
        label = request.form["label"].strip()
        username = request.form["username"].strip()
        db.update_account_basic(account_id, label, username)
        db.log_event(current_user.email, "account_edited", f"Edited account: {label} ({username})")
        if password:
            db.set_account_password(account_id, crypto_util.encrypt(password))
            db.log_event(current_user.email, "account_password_changed",
                          f"Changed saved password for account: {label} ({username})")
        return redirect(url_for("accounts"))
    return render_template("account_edit.html", account=account)


@app.route("/accounts/<int:account_id>/clear_password", methods=["POST"])
@login_required
def clear_account_password(account_id):
    account = db.get_account(account_id)
    db.clear_account_password(account_id)
    if account:
        db.log_event(current_user.email, "account_password_cleared",
                      f"Cleared saved password for account: {account['label']} ({account['username']})")
    return redirect(url_for("edit_account", account_id=account_id))


@app.route("/accounts/<int:account_id>/delete", methods=["POST"])
@login_required
def delete_account(account_id):
    account = db.get_account(account_id)
    db.delete_account(account_id)
    if account:
        db.log_event(current_user.email, "account_deleted",
                      f"Deleted account: {account['label']} ({account['username']})")
    return redirect(url_for("accounts"))


@app.route("/characters/new", methods=["POST"])
@login_required
def new_character():
    account_id = int(request.form["account_id"])
    name = request.form["name"].strip()
    db.add_character(account_id, name)
    account = db.get_account(account_id)
    db.log_event(current_user.email, "character_created",
                  f"Added character {name} to account {account['label'] if account else account_id}")
    return redirect(url_for("accounts"))


@app.route("/characters/<int:character_id>/delete", methods=["POST"])
@login_required
def delete_character(character_id):
    char = db.get_character(character_id)
    db.delete_character(character_id)
    if char:
        db.log_event(current_user.email, "character_deleted",
                      f"Deleted character {char['name']} from account {char['account_label']}")
    return redirect(url_for("accounts"))


@app.route("/settings/test_webhook", methods=["POST"])
@login_required
def test_webhook():
    role_id = db.get_setting("discord_role_id")
    template = db.get_setting("discord_message_template") or notifier.DEFAULT_TEMPLATE
    try:
        content = template.format(character="anglerbot", account="Main", gob="caveangler", road="Winnfield")
    except (KeyError, IndexError, ValueError):
        content = notifier.DEFAULT_TEMPLATE.format(character="anglerbot", account="Main", gob="caveangler", road="Winnfield")
    ok, detail = notifier.send_message(content, mention_role_id=role_id)
    flash(detail, "success" if ok else "error")
    return redirect(url_for("settings"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    if request.method == "POST":
        db.set_setting("bindir", request.form["bindir"].strip())
        db.set_setting("server", request.form["server"].strip())
        db.set_setting("discord_webhook_url", request.form["discord_webhook_url"].strip())
        db.set_setting("discord_role_id", request.form["discord_role_id"].strip())
        db.set_setting("discord_message_template", request.form["discord_message_template"].strip())
        db.log_event(current_user.email, "settings_updated", "Updated app settings")
        return redirect(url_for("settings"))
    return render_template(
        "settings.html",
        bindir=db.get_setting("bindir", ""),
        server=db.get_setting("server", "game.havenandhearth.com"),
        discord_webhook_url=db.get_setting("discord_webhook_url", ""),
        discord_role_id=db.get_setting("discord_role_id", ""),
        discord_message_template=db.get_setting("discord_message_template", notifier.DEFAULT_TEMPLATE),
    )


@app.route("/logs")
@login_required
def logs():
    q = request.args.get("q", "").strip()
    entries = db.list_log(search=q if q else None)
    return render_template("logs.html", entries=entries, q=q)


def create_app():
    db.init_db()
    scheduler.start()
    return app


if __name__ == "__main__":
    create_app()
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
