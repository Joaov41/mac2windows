#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"
exec ./venv/bin/python menubar_app.py
