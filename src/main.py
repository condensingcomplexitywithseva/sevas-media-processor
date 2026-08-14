# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import sys
import traceback
import os
import logging
import threading
try:
    import tkinter as tk
    from tkinter import messagebox
    has_tkinter = True
except ImportError:
    has_tkinter = False
from logging.handlers import MemoryHandler
from pathlib import Path
import json
import locale

from fs_utils import get_safe_path

def _read_preboot_locale() -> dict:
    try:
        lang = locale.getlocale()[0]
        if lang is None:
            lang = os.environ.get('LANG', 'en_US.UTF-8').split('.')[0]
    except Exception:
        lang = 'en_US'

    locales_dir = Path(__file__).resolve().parent / "locales"

    lang_code = lang[:2].lower() if lang else "en"
    target_json = f"{lang_code}.json"

    if not (locales_dir / target_json).exists():
        target_json = "en.json"

    try:
        with open(locales_dir / target_json, "r", encoding="utf-8") as f:
            parsed = json.load(f)
            return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


_preboot_locale = None


def get_localized_string(key: str, default: str) -> str:
    global _preboot_locale
    if _preboot_locale is None:
        _preboot_locale = _read_preboot_locale()
    return _preboot_locale.get(key, default)

def get_splash_icon_data_uri() -> str:
    try:
        import base64
        icon_png = Path(__file__).resolve().parent / "static" / "app_icon_64.png"
        return "data:image/png;base64," + base64.b64encode(icon_png.read_bytes()).decode("ascii")
    except Exception:
        return ""


memory_buffer = MemoryHandler(capacity=1000, target=None)
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - [%(name)s] - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), memory_buffer],
)

logging.info("Bootstrapping Flask environment...")

def write_fatal_panic_log(error_details_string: str, app_root: Path = None) -> None:
    import json

    if app_root is None:
        app_root = Path(__file__).resolve().parent.parent

    panic_log_path = None
    try:
        settings_file = app_root / "settings.json"
        if settings_file.exists():
            with open(settings_file, "r", encoding="utf-8") as sf:
                data = json.load(sf)
                if "OUTPUT_FOLDER_PATH" in data:
                    output_dir = Path(data["OUTPUT_FOLDER_PATH"]).resolve()
                    potential_path = output_dir / "MEDIA_PROCESSOR_CRASH_LOG.txt"
                    try:
                        Path(get_safe_path(potential_path.parent)).mkdir(parents=True, exist_ok=True)
                        with open(get_safe_path(potential_path), "a", encoding="utf-8"):
                            pass
                        panic_log_path = potential_path
                    except OSError:
                        potential_path = output_dir.parent / "MEDIA_PROCESSOR_CRASH_LOG.txt"
                        Path(get_safe_path(potential_path.parent)).mkdir(parents=True, exist_ok=True)
                        with open(get_safe_path(potential_path), "a", encoding="utf-8"):
                            pass
                        panic_log_path = potential_path
    except Exception:
        pass

    if not panic_log_path:
        home_dir = Path.home()
        desktop_path = home_dir / "Desktop" / "MEDIA_PROCESSOR_CRASH_LOG.txt"
        panic_log_path = (
            desktop_path
            if desktop_path.parent.exists()
            else home_dir / "MEDIA_PROCESSOR_CRASH_LOG.txt"
        )

    try:
        with open(get_safe_path(panic_log_path), "w", encoding="utf-8") as f:
            f.write(
                "========================================================================\n"
                "                 CRITICAL STARTUP CRASH \n"
                "========================================================================\n\n"
                "The application failed to launch. Details:\n\n"
                f"{error_details_string}\n\n"
                "--- Trapped Pre-Boot System Logs ---\n"
                + "\n".join([record.getMessage() for record in memory_buffer.buffer])
                + "\n"
            )
    except Exception:
        pass


def load_secure_env():
    try:
        from config_loader import get_env_tokens
        tokens = get_env_tokens()
        for key, value in tokens.items():
            if key.endswith("_TOKEN"):
                os.environ[key] = value
    except Exception as e:
        logging.error(f"Failed to load secure .env: {e}")


class Api:

    def __init__(self):
        self._window = None

    def browse_folder(self):
        if self._window:
            import webview
            result = self._window.create_file_dialog(webview.FileDialog.FOLDER)
            return result[0] if result else None
        return None

    def browse_file(self):
        if self._window:
            import webview
            result = self._window.create_file_dialog(
                webview.FileDialog.OPEN,
                file_types=("Text files (*.txt)", "All files (*.*)"),
            )
            return result[0] if result else None
        return None


def register_windows_app_identity():
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "SevasMediaProcessor.DesktopApp"
        )
    except Exception:
        pass


def load_native_window_icon():
    try:
        import clr
        clr.AddReference('System.Windows.Forms')
        clr.AddReference('System.Drawing')
        from System.Drawing import Icon
        from System.Windows.Forms import MethodInvoker
        icon_file = Path(__file__).resolve().parent / "static" / "app_icon.ico"
        return Icon(str(icon_file)), MethodInvoker
    except Exception:
        return None


def apply_native_window_icon(window, native_icon):
    if native_icon is None:
        return
    icon, method_invoker = native_icon
    try:
        form = window.native

        def set_icon_on_ui_thread():
            try:
                form.Icon = icon
            except Exception:
                pass

        form.BeginInvoke(method_invoker(set_icon_on_ui_thread))
    except Exception:
        pass


def main():
    import webview

    register_windows_app_identity()
    native_window_icon = load_native_window_icon()

    translated_string = get_localized_string("confirm_shutdown", "Are you sure you want to close the application?")
    msg_starting_title = get_localized_string("msg_starting_title", "Seva's Media Processor - Starting...")
    msg_loading = get_localized_string("msg_loading", "Seva's Media Processor is loading...")

    js_api = Api()

    splash_icon_uri = get_splash_icon_data_uri()
    splash_icon_html = (
        f'<img src="{splash_icon_uri}" width="36" height="36" alt="">'
        if splash_icon_uri
        else ""
    )

    splash_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{
                background-color: #ffffff;
                color: #333333;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                height: 100vh;
                margin: 0;
                overflow: hidden;
            }}
            .spinner {{
                border: 4px solid #f3f3f3;
                border-top: 4px solid #3498db;
                border-radius: 50%;
                width: 30px;
                height: 30px;
                animation: spin 1s linear infinite;
                margin-top: 15px;
            }}
            @keyframes spin {{
                0% {{ transform: rotate(0deg); }}
                100% {{ transform: rotate(360deg); }}
            }}
        </style>
    </head>
    <body>
        <div style="display: flex; align-items: center; gap: 10px;">
            {splash_icon_html}
            <div style="font-size: 16px;">{msg_loading}</div>
        </div>
        <div class="spinner"></div>
    </body>
    </html>
    """

    primary_screen = webview.screens[0]

    splash_window = webview.create_window(
        msg_starting_title,
        html=splash_html,
        width=340,
        height=150,
        frameless=True,
        on_top=True,
        background_color='#ffffff',
        text_select=True,
        hidden=True,
        screen=primary_screen
    )

    main_window = webview.create_window(
        "Seva's Media Processor",
        hidden=True,
        confirm_close=True,
        width=1280,
        height=800,
        min_size=(1024, 768),
        maximized=True,
        js_api=js_api,
        text_select=True,
        screen=primary_screen
    )
    js_api._window = main_window

    splash_loaded_flag = False
    splash_destroyed_flag = False

    def on_splash_loaded():
        nonlocal splash_loaded_flag
        if not splash_loaded_flag and not splash_destroyed_flag:
            splash_loaded_flag = True
            try:
                splash_window.show()
            except Exception:
                pass

    splash_window.events.loaded += on_splash_loaded

    main_loaded_flag = False
    flask_url_loaded = False

    def on_main_loaded_deliver_token():
        if not flask_url_loaded:
            return
        from routes.web_server import SESSION_TOKEN

        try:
            main_window.evaluate_js(
                f"window.__receiveApiToken({json.dumps(SESSION_TOKEN)})"
            )
        except Exception as e:
            logging.getLogger("SYSTEM").error(
                f"Failed to deliver the API token to the window: {e}"
            )

    main_window.events.loaded += on_main_loaded_deliver_token

    def on_main_loaded():
        nonlocal main_loaded_flag, splash_destroyed_flag
        if flask_url_loaded and not main_loaded_flag:
            main_loaded_flag = True
            main_window.show()
            try:
                splash_destroyed_flag = True
                splash_window.destroy()
            except Exception:
                pass

    main_window.events.loaded += on_main_loaded

    def on_splash_loaded_apply_icon():
        apply_native_window_icon(splash_window, native_window_icon)

    def on_main_loaded_apply_icon():
        apply_native_window_icon(main_window, native_window_icon)

    splash_window.events.loaded += on_splash_loaded_apply_icon
    main_window.events.loaded += on_main_loaded_apply_icon

    backend_error = None

    def start_backend():
        nonlocal backend_error
        try:
            logger = logging.getLogger("SYSTEM")
            from version import APP_VERSION
            logger.info(f"Seva's Media Processor v{APP_VERSION} Engine: Starting backend...")

            logger.info("Verifying environment and security tokens...")
            load_secure_env()

            logger.info("Synchronizing configuration with disk storage...")
            from routes.web_server import create_app
            from config_loader import load_for_ui, log_settings_errors

            merged_settings, initial_errors = load_for_ui()

            from central_logger import setup_logging
            setup_logging(
                merged_settings.get("LOGGING_LEVEL", "INFO"),
                memory_buffer_handler=memory_buffer,
            )

            if initial_errors:
                log_settings_errors(initial_errors)
                logger.warning(
                    f"Bootstrap sequence completed with {len(initial_errors)} configuration warnings."
                )
            else:
                logger.info("Bootstrap sequence successful. Engine is ready.")

            app = create_app()

            logger.info("Starting local Flask web server on dynamically assigned port...")

            from werkzeug.serving import make_server

            server = make_server("127.0.0.1", 0, app, threaded=True)
            assigned_port = server.server_port
            logger.info(f"Dynamically assigned port: {assigned_port}")

            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()

            nonlocal flask_url_loaded
            main_window.load_url(f"http://127.0.0.1:{assigned_port}")
            flask_url_loaded = True

        except Exception as e:
            backend_error = e
            crash_trace = traceback.format_exc()
            logging.critical(f"Failed to launch the application interface: {e}")
            print(f"CRITICAL ERROR: {e}", file=sys.stderr)
            write_fatal_panic_log(crash_trace)

            try:
                splash_window.destroy()
            except Exception:
                pass
            try:
                main_window.destroy()
            except Exception:
                pass

    backend_thread = threading.Thread(target=start_backend, daemon=True)
    backend_thread.start()

    def on_closing():
        try:
            import routes.execution_api as exec_api
            exec_api.abort_flag.set()
            if exec_api.processing_thread and exec_api.processing_thread.is_alive():
                exec_api.processing_thread.join(timeout=5.0)
        except Exception:
            pass
        return True

    main_window.events.closing += on_closing

    try:
        import webview
        webview.settings['OPEN_DEVTOOLS_IN_DEBUG'] = False
        webview.start(localization={'global.quitConfirmation': translated_string}, private_mode=False, debug=False)

        try:
            from central_logger import close_logging
            close_logging()
        except Exception:
            pass

    except Exception as e:
        crash_trace = traceback.format_exc()
        logging.critical(f"Failed to launch the application interface: {e}")
        print(f"CRITICAL ERROR: {e}", file=sys.stderr)
        write_fatal_panic_log(crash_trace)
        backend_error = e

    if backend_error:
        if has_tkinter:
            try:
                err_root = tk.Tk()
                err_root.withdraw()
                err_root.attributes("-topmost", True)
                messagebox.showerror("Startup Error", f"The application failed to launch.\n\nError: {backend_error}\n\nPlease check the crash log for details.")
                err_root.destroy()
            except Exception:
                pass

        try:
            from central_logger import close_logging
            close_logging()
        except Exception:
            pass

        raise RuntimeError(f"Fatal Startup Error: {str(backend_error)}")

if __name__ == "__main__":
    main()
