"""Pytest bridge: the suite is written for `python -m unittest discover -s tests`,
where the tests directory itself is on sys.path (for `from helpers import ...`).
Mirror that so `python -m pytest tests/` collects the same modules."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
