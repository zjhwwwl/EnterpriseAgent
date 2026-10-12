"""ASGI entry point. Tests import enterprise_agent.application instead."""

from enterprise_agent.application import create_app as create_app

app = create_app()
