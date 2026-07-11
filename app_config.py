"""Конфигурация приложения (путь к БД и прочие настройки)."""
import os
import sys
import json

if getattr(sys, 'frozen', False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _APP_DIR = os.path.dirname(os.path.abspath(__file__))

_CONFIG_PATH = os.path.join(_APP_DIR, "app_config.json")


def load_config():
    """Загрузить конфиг из app_config.json."""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(config: dict):
    """Сохранить конфиг в app_config.json."""
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def get_db_path():
    """Получить путь к БД из конфига."""
    return load_config().get("db_path")


def set_db_path(path):
    """Сохранить путь к БД в конфиге."""
    cfg = load_config()
    cfg["db_path"] = path
    save_config(cfg)


def get_default_archive_folder():
    """Получить папку для архивов по умолчанию (Archiv/ рядом с приложением)."""
    return os.path.join(_APP_DIR, "Archiv")


def get_archive_settings():
    """Получить настройки архивации."""
    cfg = load_config()
    return {
        "enabled": cfg.get("archive_enabled", False),
        "count": cfg.get("archive_count", 3),
        "folder": cfg.get("archive_folder"),
    }


def set_archive_settings(enabled, count, folder):
    """Сохранить настройки архивации."""
    cfg = load_config()
    cfg["archive_enabled"] = enabled
    cfg["archive_count"] = count
    cfg["archive_folder"] = folder
    save_config(cfg)
