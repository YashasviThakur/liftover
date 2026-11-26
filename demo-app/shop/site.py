from .flags import unleash


def banner() -> str | None:
    if unleash.is_enabled("maintenance_mode"):
        return "We're doing some maintenance. Back soon."
    if unleash.is_enabled("holiday_banner"):
        return "Holiday sale: 20% off everything."
    return None
