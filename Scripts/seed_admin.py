#!/usr/bin/env python3
"""
seed_admin.py - create (or promote) an admin account.

Run this once, locally, to set up your first admin login. The password
is entered at a hidden prompt and only ever stored as a hash in scout.db
- it is never written to any file in plaintext.

Usage:
    python seed_admin.py
"""

import getpass
from werkzeug.security import generate_password_hash

import db


def main():
    db.init_db()

    email = input("Admin username (can be an email or plain username): ").strip()
    if not email:
        print("Username is required.")
        return

    existing = db.get_user_by_email(email)
    if existing:
        confirm = input(
            f"A user with this username already exists (admin={bool(existing['is_admin'])}, "
            f"approved={bool(existing['is_approved'])}). Promote to admin & approve? [y/N] "
        ).strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return
        db.set_user_admin(existing["id"], True)
        db.set_user_approved(existing["id"], True)
        print(f"'{email}' is now an approved admin.")
        return

    pw1 = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("Passwords did not match. Try again.")
        return
    if len(pw1) < 8:
        print("Password should be at least 8 characters.")
        return

    db.create_user(email, generate_password_hash(pw1), is_admin=1, is_approved=1)
    print(f"Admin account created for '{email}'. You can now log in at /login.")


if __name__ == "__main__":
    main()
