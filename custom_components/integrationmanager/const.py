"""Integration Manager constants."""

DOMAIN = "integrationmanager"
NAME = "Integration Manager"
VERSION = "0.1.6"
OWNER = "THEFLDR"
REPOSITORY = f"{OWNER}/HA-IntegrationManager"
CATALOG_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/main/repositories.json"
STABLE = "stable"
RC = "release_candidate"
CHANNELS = {STABLE: "Stable", RC: "Release Candidate"}
MANAGED = "managed"
DEFAULT = "default"
OVERRIDE = "override"
CHANNEL_MODES = {MANAGED: "Managed", DEFAULT: "Default", OVERRIDE: "Override"}
MIN_HA_VERSION = "2025.12.0"
MAX_ARCHIVE = 20 * 1024 * 1024
MAX_UNPACKED = 80 * 1024 * 1024
MAX_FILES = 2000
