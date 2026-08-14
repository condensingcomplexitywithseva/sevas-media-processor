# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import logging
import webbrowser

from flask import Blueprint, request, jsonify

from version import APP_LINKS

about_bp = Blueprint('about', __name__)
logger = logging.getLogger(__name__)


@about_bp.route('/open_link', methods=['POST'])
def open_link():
    target = (request.json or {}).get("target", "")
    url = APP_LINKS.get(target)
    if url is None:
        return jsonify({"status": "error",
                        "message": f"Unknown link key: {target!r}"}), 404
    try:
        webbrowser.open(url)
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Failed to open {url}: {e}")
        return jsonify({"status": "error",
                        "message_key": "err_open_link_failed",
                        "url": url}), 500
