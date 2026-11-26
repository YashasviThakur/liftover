import os

from .flags import context, unleash

# Homegrown flags from environment variables, set per deploy.
DARK_MODE = os.environ.get("FEATURE_DARK_MODE", "false") == "true"
NEW_ONBOARDING = os.getenv("FEATURE_NEW_ONBOARDING") == "1"
UPLOAD_LIMIT_MB = int(os.environ.get("UPLOAD_LIMIT_MB", "25"))


def dashboard(user) -> dict:
    ctx = context(user)
    view = {"theme": "dark" if DARK_MODE else "light", "upload_limit_mb": UPLOAD_LIMIT_MB}
    if unleash.is_enabled("beta_dashboard", ctx):
        view["layout"] = "beta"
    if unleash.is_enabled("pro_reports", ctx):
        view["reports"] = "pro"
    if unleash.is_enabled("recommendations_v2", ctx):
        view["recommendations"] = "v2"
    if NEW_ONBOARDING and user.get("is_new"):
        view["onboarding"] = "guided"
    return view
