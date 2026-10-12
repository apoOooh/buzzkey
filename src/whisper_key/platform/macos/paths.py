import subprocess
from pathlib import Path

from ...branding import APP_DIR_NAME

def get_app_data_path():
    return Path.home() / f'.{APP_DIR_NAME}'

def open_file(path):
    subprocess.run(['open', str(path)])
