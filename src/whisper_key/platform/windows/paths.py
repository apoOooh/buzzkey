import os
from pathlib import Path

from ...branding import APP_DIR_NAME

def get_app_data_path():
    return Path(os.getenv('APPDATA')) / APP_DIR_NAME

def open_file(path):
    os.startfile(path)
