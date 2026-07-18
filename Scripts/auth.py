from functools import wraps

from flask import abort
from flask_login import LoginManager, UserMixin, current_user

import db

login_manager = LoginManager()
login_manager.login_view = "login"


class User(UserMixin):
    def __init__(self, row):
        self.id = str(row["id"])
        self.email = row["email"]
        self.password_hash = row["password_hash"]
        self.is_admin = bool(row["is_admin"])
        self.is_approved = bool(row["is_approved"])


@login_manager.user_loader
def load_user(user_id):
    row = db.get_user_by_id(int(user_id))
    if row is None:
        return None
    return User(row)


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped
