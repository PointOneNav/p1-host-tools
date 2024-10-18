#!/usr/bin/env python3

import os
import sys

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)

from bin.raw_analysis import extract_format

if __name__ == "__main__":
    extract_format('nmea')
