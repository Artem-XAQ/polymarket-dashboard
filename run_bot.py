"""Wrapper to ensure bot.py runs from the correct working directory."""
import os
import sys

# Change to the dashboard directory so relative paths (data/, config.yaml) work
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

# Now run bot.main()
from bot import main
main()
