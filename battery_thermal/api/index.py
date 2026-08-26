"""
api/index.py
============
Vercel serverless entrypoint. The @vercel/python runtime expects a
WSGI-callable module-level variable named `app` in this file. The Dash
object built in ../app.py is *not* itself WSGI-callable (you reach the
underlying Flask server via `app.server`), so this thin shim re-exports
that Flask server under the name Vercel looks for.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app as _dash_app  # noqa: E402

app = _dash_app.server  # WSGI application Vercel will serve
