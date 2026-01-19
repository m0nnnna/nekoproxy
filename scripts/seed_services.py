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
# Each service defines both the listen port and backend target
SERVICE_TEMPLATES = [
    {
        "name": "SMTP",
        "description": "Email SMTP service",
        "listen_port": 2525,
        "backend_host": "mail.example.com",
        "backend_port": 25,
        "protocol": Protocol.TCP
    },
    {
        "name": "SMTP-TLS",
        "description": "Email SMTP with TLS",
        "listen_port": 2587,
        "backend_host": "mail.example.com",
        "backend_port": 587,
        "protocol": Protocol.TCP
    },
    {
        "name": "IMAP",
        "description": "Email IMAP service",
        "listen_port": 2143,
        "backend_host": "mail.example.com",
        "backend_port": 143,
        "protocol": Protocol.TCP
    },
    {
        "name": "IMAP-SSL",
        "description": "Email IMAP with SSL",
        "listen_port": 2993,
        "backend_host": "mail.example.com",
        "backend_port": 993,
        "protocol": Protocol.TCP
    },
    {
        "name": "TeamSpeak-Voice",
        "description": "TeamSpeak voice communication",
        "listen_port": 9987,
        "backend_host": "ts.example.com",
        "backend_port": 9987,
        "protocol": Protocol.UDP
    },
    {
        "name": "TeamSpeak-Query",
        "description": "TeamSpeak ServerQuery",
        "listen_port": 10011,
        "backend_host": "ts.example.com",
        "backend_port": 10011,
        "protocol": Protocol.TCP
    },
    {
        "name": "TeamSpeak-Files",
        "description": "TeamSpeak file transfer",
        "listen_port": 30033,
        "backend_host": "ts.example.com",
        "backend_port": 30033,
        "protocol": Protocol.TCP
    },
    {
        "name": "Mattermost",
        "description": "Mattermost chat server",
        "listen_port": 8065,
        "backend_host": "chat.example.com",
        "backend_port": 8065,
        "protocol": Protocol.TCP
    },
    {
        "name": "WoW-Auth",
        "description": "World of Warcraft Auth Server",
        "listen_port": 3724,
        "backend_host": "wow.example.com",
        "backend_port": 3724,
        "protocol": Protocol.TCP
    },
    {
        "name": "WoW-World",
        "description": "World of Warcraft World Server",
        "listen_port": 8085,
        "backend_host": "wow.example.com",
        "backend_port": 8085,
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
                listen_port=template["listen_port"],
                backend_host=template["backend_host"],
                backend_port=template["backend_port"],
                protocol=template["protocol"]
            )
            print(f"  Added: {template['name']} (:{template['listen_port']} -> {template['backend_host']}:{template['backend_port']})")
            added += 1

        print(f"\nDone! Added {added} services, skipped {skipped} existing.")
        print("\nRemember to:")
        print("  1. Update the backend hosts to your actual server addresses")
        print("  2. Go to Assignments page to deploy services to agents")

    finally:
        db.close()


if __name__ == "__main__":
    print("Seeding service templates...")
    seed_services()
