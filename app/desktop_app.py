"""Serve the packaged Vue application and API from one local origin."""
from pathlib import Path

from api import app
from desktop_static import VueFiles


app.mount("/", VueFiles(directory=Path(__file__).resolve().parent / "web" / "dist", html=True), name="desktop")
