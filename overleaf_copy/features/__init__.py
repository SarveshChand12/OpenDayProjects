"""Optional feature modules. Each exposes a Flask Blueprint called `bp`."""

import importlib

MODULES = ["history", "proofing", "submission", "search"]


def register(app):
    for name in MODULES:
        mod = importlib.import_module(f"features.{name}")
        app.register_blueprint(mod.bp)
