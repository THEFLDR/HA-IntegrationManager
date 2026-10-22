"""Local read-only channel interface; no persisted state or synchronization."""

from .const import CHANNEL_MODES, CHANNELS, DEFAULT, DOMAIN, MANAGED, OVERRIDE, STABLE


def get_global_channel(hass) -> str | None:
    """Return the loaded Manager option, or None when Manager is absent.

    Compatible integrations can read this at use time. They retain their own
    null/explicit override and use Stable when this returns None.
    """
    runtime = hass.data.get(DOMAIN)
    if runtime is None:
        return None
    return runtime.entry.options.get("global_channel", runtime.entry.options.get("channel", STABLE))


def resolve_channel(hass, override: str | None = None, mode: str | None = None) -> dict[str, str]:
    """Resolve an integration's intent without changing its persisted override."""
    if mode is None:
        mode = OVERRIDE if override is not None else MANAGED
    if mode not in CHANNEL_MODES:
        raise ValueError("Invalid release channel mode")
    if mode == OVERRIDE:
        if override not in CHANNELS:
            raise ValueError("Invalid release channel")
        return {
            "state": CHANNELS[override],
            "effective_channel": override,
            "source": "override",
        }
    if mode == DEFAULT:
        return {"state": "Stable", "effective_channel": STABLE, "source": DEFAULT}
    channel = get_global_channel(hass)
    return {
        "state": "Managed" if channel else "Stable",
        "effective_channel": channel or STABLE,
        "source": "managed" if channel else "default",
    }
