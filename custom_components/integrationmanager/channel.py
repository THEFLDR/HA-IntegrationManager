"""Local read-only channel interface; no persisted state or synchronization."""

from .const import CHANNELS, DOMAIN, STABLE


def get_global_channel(hass) -> str | None:
    """Return the loaded Manager option, or None when Manager is absent.

    Compatible integrations can read this at use time. They retain their own
    null/explicit override and use Stable when this returns None.
    """
    runtime = hass.data.get(DOMAIN)
    return runtime.entry.options.get("channel", STABLE) if runtime else None


def resolve_channel(hass, override: str | None = None) -> dict[str, str]:
    """Resolve an integration's intent without changing its persisted override."""
    if override is not None:
        if override not in CHANNELS:
            raise ValueError("Invalid release channel")
        return {
            "state": CHANNELS[override],
            "effective_channel": override,
            "source": "override",
        }
    channel = get_global_channel(hass)
    return {
        "state": "Managed" if channel else "Stable",
        "effective_channel": channel or STABLE,
        "source": "managed" if channel else "default",
    }
