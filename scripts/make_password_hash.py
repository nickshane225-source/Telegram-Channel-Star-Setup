#!/usr/bin/env python3
"""
Password Hash Generator for StarPost Manager.
Generates a bcrypt hash to use as ADMIN_PASSWORD_HASH in your .env file.
"""
import sys
import getpass
import bcrypt


def generate_hash(password: str) -> str:
    """Generate a secure bcrypt hash for a given password."""
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def main():
    print("=" * 60)
    print("StarPost Manager - Admin Password Hash Generator")
    print("=" * 60)

    if len(sys.argv) > 1:
        password = sys.argv[1]
    else:
        password = getpass.getpass("Enter new admin password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Error: Passwords do not match!")
            sys.exit(1)

    if not password:
        print("Error: Password cannot be empty!")
        sys.exit(1)

    password_hash = generate_hash(password)
    print("\nGenerated Bcrypt Password Hash:")
    print("-" * 60)
    print(password_hash)
    print("-" * 60)
    print("\nCopy and paste this into your .env file:")
    print(f"ADMIN_PASSWORD_HASH='{password_hash}'\n")


if __name__ == "__main__":
    main()
