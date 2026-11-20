"""WSGI entry point for PythonAnywhere and other WSGI hosts."""

import os
from pathlib import Path

os.environ.setdefault("COMCHAT_DATABASE", str(Path.home() / "comchat.sqlite3"))
os.environ.setdefault("COMCHAT_SECURE_COOKIES", "1")

from app_server import ComChat, DATABASE, create_wsgi_application


application = create_wsgi_application(ComChat(DATABASE))
