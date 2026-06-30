#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from db import init_db
from app import app

if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=8080, debug=False)
