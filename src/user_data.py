# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


from dataclasses import dataclass
from enum import Enum

SETTINGS_FILE_NAME = "settings.json"
DEFAULT_INPUT_FOLDER = "input"
DEFAULT_OUTPUT_FOLDER = "output"
VENV_DIR_NAME = "venv"
ENV_FILE_NAME = ".env"
LOGS_DIR_NAME = "logs"
WEBVIEW_PROFILE_DIR_NAME = "webview"
PANIC_LOG_NAME = "MEDIA_PROCESSOR_CRASH_LOG.txt"


class Bucket(str, Enum):

    MOVED_BY_USER = "moved_by_user"
    OUTSIDE_FOLDER = "outside_folder"
    REBUILT_BY_INSTALLER = "rebuilt_by_installer"
    DIAGNOSTIC_NOT_CARRIED = "diagnostic_not_carried"


class Site(str, Enum):

    APP_FOLDER = "app_folder"
    APP_DATA = "app_data"
    OUTPUT_FOLDER = "output_folder"
    OUTPUT_PARENT = "output_parent"
    HOME = "home"


@dataclass(frozen=True)
class UserPath:
    name: str
    site: Site
    bucket: Bucket
    reason: str


def user_paths() -> tuple[UserPath, ...]:
    return (
        UserPath(SETTINGS_FILE_NAME, Site.APP_FOLDER, Bucket.MOVED_BY_USER,
                 "the user's configuration"),
        UserPath(DEFAULT_INPUT_FOLDER, Site.APP_FOLDER, Bucket.MOVED_BY_USER,
                 "the user's media (default location; INPUT_FOLDER_PATH may point elsewhere)"),
        UserPath(DEFAULT_OUTPUT_FOLDER, Site.APP_FOLDER, Bucket.MOVED_BY_USER,
                 "every run's JPEGs, reports, database and archived run folders "
                 "(default location; OUTPUT_FOLDER_PATH may point elsewhere)"),
        UserPath(VENV_DIR_NAME, Site.APP_FOLDER, Bucket.REBUILT_BY_INSTALLER,
                 "the Python libraries; the setup script builds a fresh one"),
        UserPath(ENV_FILE_NAME, Site.APP_DATA, Bucket.OUTSIDE_FOLDER,
                 "the API tokens"),
        UserPath(LOGS_DIR_NAME, Site.APP_DATA, Bucket.OUTSIDE_FOLDER,
                 "the system logs"),
        UserPath(WEBVIEW_PROFILE_DIR_NAME, Site.APP_DATA, Bucket.OUTSIDE_FOLDER,
                 "the window's WebView2 profile: its cache and the page's stored "
                 "language and translations"),
        UserPath(PANIC_LOG_NAME, Site.OUTPUT_FOLDER, Bucket.DIAGNOSTIC_NOT_CARRIED,
                 "the startup crash log, first-choice site"),
        UserPath(PANIC_LOG_NAME, Site.OUTPUT_PARENT, Bucket.DIAGNOSTIC_NOT_CARRIED,
                 "the startup crash log when the output folder cannot be written"),
        UserPath(PANIC_LOG_NAME, Site.HOME, Bucket.DIAGNOSTIC_NOT_CARRIED,
                 "the startup crash log when settings.json cannot be read "
                 "(the Desktop if it exists, else the home folder)"),
    )


def moved_by_user() -> tuple[str, ...]:
    return tuple(p.name for p in user_paths() if p.bucket is Bucket.MOVED_BY_USER)
