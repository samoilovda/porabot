"""
Pytest Configuration — Async Test Support
=========================================

This configuration enables pytest-asyncio to run async tests automatically.
"""

import asyncio
import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop