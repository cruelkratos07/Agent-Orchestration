"""Provision a high-entropy bearer token locally; no account or network required.

Run yourself in a private terminal. Store the token in a password manager;
add only the printed credential record to SUPPORT_CREDENTIALS.
"""
import argparse
import hashlib
import json
import secrets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subject", help="Stable principal ID, e.g. customer-ananya")
    parser.add_argument("--role", choices=("customer", "reviewer"), required=True)
    parser.add_argument("--customer-id", type=int)
    args = parser.parse_args()
    if args.role == "customer" and (args.customer_id is None or args.customer_id < 1):
        parser.error("customers require a positive --customer-id")
    token = secrets.token_urlsafe(32)
    row = {"subject": args.subject, "role": args.role,
           "sha256": hashlib.sha256(token.encode()).hexdigest()}
    if args.role == "customer":
        row["customer_id"] = args.customer_id
    print("Private access token (do not commit or share):", token)
    print("Credential record:")
    print(json.dumps(row))


if __name__ == "__main__":
    main()
