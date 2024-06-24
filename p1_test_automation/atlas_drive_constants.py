from pathlib import Path
import os

DRIVE_TEST_TMP_DIR = Path('/tmp/atlas_drive_test')
DRIVE_DIR = Path('/logs/drive_tests')
METADATA_FILENAME = "drive_test_metadata.json"
TCP_DUMP_FILENAME = "tcpdump.pcap"
LOCK_FILE = DRIVE_TEST_TMP_DIR / "atlas_drive_lock"


def create_dirs():
    if not os.path.exists(DRIVE_DIR):
        os.makedirs(DRIVE_DIR, exist_ok=True)
    if not os.path.exists(DRIVE_TEST_TMP_DIR):
        os.makedirs(DRIVE_TEST_TMP_DIR, exist_ok=True)
