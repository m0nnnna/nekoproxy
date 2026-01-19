#!/usr/bin/env python3
"""Run the NekoProxy controller for local development."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "controller.main:app",
        host="0.0.0.0",
        port=8001,
        reload=True
    )
