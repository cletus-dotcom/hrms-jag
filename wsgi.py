"""
WSGI entry point for production servers (e.g. Gunicorn on Render).
Usage: gunicorn -w 2 -b 0.0.0.0:$PORT wsgi:app
"""
from app import create_app

app = create_app()
