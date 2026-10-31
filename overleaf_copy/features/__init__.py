"""Optional feature modules. Each exposes a Flask Blueprint called `bp`."""

import importlib

MODULES = ["history"]


def register(app):
    for name in MODULES:
        mod = importlib.import_module(f"features.{name}")
        app.register_blueprint(mod.bp)
