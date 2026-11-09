"""Optional feature modules. Each exposes a Flask Blueprint called `bp`."""

import importlib

MODULES = ["history", "proofing", "submission", "search", "references", "stats", "notes", "backup", "ai", "word_import"]


def register(app):
    for name in MODULES:
        mod = importlib.import_module(f"features.{name}")
        app.register_blueprint(mod.bp)
