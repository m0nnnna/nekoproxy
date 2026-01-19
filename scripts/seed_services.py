#!/usr/bin/env python3
"""
Seed script to add pre-configured service templates.
Run after starting the controller for the first time.
"""

import sys
sys.path.insert(0, '.')

from controller.database.database import SessionLocal, engine, Base
from controller.database.repositories import ServiceRepository
from shared.models.common import Protocol

# Pre-configured service templates
SERVICE_TEMPLATES = [
    {
        "name": "SMTP",
        "description": "Email SMTP service",
        "default_backend_host": "mail.example.com",
        "default_backend_port": 25,
        "protocol": Protocol.TCP
    },
    {
        "name": "SMTP-TLS",
        "description": "Email SMTP with TLS",
        "default_backend_host": "mail.example.com",
        "default_backend_port": 587,
        "protocol": Protocol.TCP
    },
    {
        "name": "IMAP",
        "description": "Email IMAP service",
        "default_backend_host": "mail.example.com",
        "default_backend_port": 143,
        "protocol": Protocol.TCP
    },
    {
        "name": "IMAP-SSL",
        "description": "Email IMAP with SSL",
        "default_backend_host": "mail.example.com",
        "default_backend_port": 993,
        "protocol": Protocol.TCP
    },
    {
        "name": "TeamSpeak-Voice",
        "description": "TeamSpeak voice communication",
        "default_backend_host": "ts.example.com",
        "default_backend_port": 9987,
        "protocol": Protocol.UDP
    },
    {
        "name": "TeamSpeak-Query",
        "description": "TeamSpeak ServerQuery",
        "default_backend_host": "ts.example.com",
        "default_backend_port": 10011,
        "protocol": Protocol.TCP
    },
    {
        "name": "TeamSpeak-Files",
        "description": "TeamSpeak file transfer",
        "default_backend_host": "ts.example.com",
        "default_backend_port": 30033,
        "protocol": Protocol.TCP
    },
    {
        "name": "Mattermost",
        "description": "Mattermost chat server",
        "default_backend_host": "chat.example.com",
        "default_backend_port": 8065,
        "protocol": Protocol.TCP
    },
    {
        "name": "WoW-Auth",
        "description": "World of Warcraft Auth Server",
        "default_backend_host": "wow.example.com",
        "default_backend_port": 3724,
        "protocol": Protocol.TCP
    },
    {
        "name": "WoW-World",
        "description": "World of Warcraft World Server",
        "default_backend_host": "wow.example.com",
        "default_backend_port": 8085,
        "protocol": Protocol.TCP
    },
]


def seed_services():
    """Add service templates to database."""
    # Create tables
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        repo = ServiceRepository(db)

        added = 0
        skipped = 0

        for template in SERVICE_TEMPLATES:
            existing = repo.get_by_name(template["name"])
            if existing:
                print(f"  Skipped: {template['name']} (already exists)")
                skipped += 1
                continue

            repo.create(
                name=template["name"],
                description=template["description"],
                default_backend_host=template["default_backend_host"],
                default_backend_port=template["default_backend_port"],
                protocol=template["protocol"]
            )
            print(f"  Added: {template['name']}")
            added += 1

        print(f"\nDone! Added {added} services, skipped {skipped} existing.")
        print("\nRemember to update the backend hosts to your actual server addresses!")

    finally:
        db.close()


if __name__ == "__main__":
    print("Seeding service templates...")
    seed_services()
