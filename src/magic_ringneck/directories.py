import xdg_base_dirs

RINGNECK_DIR = xdg_base_dirs.xdg_config_home() / "ringneck"
NESTBOX_SOCK = RINGNECK_DIR / "nestbox.sock"
SUPERVISOR_CONF = RINGNECK_DIR / "supervisord.conf"
