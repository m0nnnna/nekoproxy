#!/usr/bin/env python3
"""Run the NekoProxy agent for local development."""

import asyncio
import os

# Set environment variables for local development
os.environ.setdefault("NEKO_AGENT_CONTROLLER_URL", "http://localhost:8001")
os.environ.setdefault("NEKO_AGENT_WIREGUARD_IP", "127.0.0.1")
os.environ.setdefault("NEKO_AGENT_HOSTNAME", "local-agent")

from agent.main import main

if __name__ == "__main__":
    asyncio.run(main())
