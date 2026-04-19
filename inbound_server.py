"""
Optional standalone server for inbound webhooks only.

Production (Railway): use the combined app instead:
  gunicorn dashboard:app

That serves the Intelligence Console and /webhooks/inbound-email on one port.

Run this module only if you need inbound on a separate process:
  gunicorn -b 0.0.0.0:8080 inbound_server:app
"""

from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask

from src.inbound_routes import inbound_bp

app = Flask(__name__)
app.register_blueprint(inbound_bp)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    port = int(os.getenv("INBOUND_PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
