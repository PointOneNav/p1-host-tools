#!/usr/bin/env python

import sys
from pathlib import Path

# Add the host tool root directory to the python path.
repo_root = Path(__file__).parents[1].resolve()
sys.path.append(repo_root)

def main():
  print('Hello World')

if __name__ == '__main__':
  main()
