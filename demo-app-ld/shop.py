"""The same shop on LaunchDarkly: the "before" state for the LaunchDarkly demo."""
import os

import ldclient
from ldclient import Context
from ldclient.config import Config

ldclient.set_config(Config(os.environ.get("LD_SDK_KEY", "")))
ld = ldclient.get()


def context(user):
    b = Context.builder(user["id"])
    for k in ("plan", "country", "age"):
        if k in user:
            b.set(k, user[k])
    return b.build()


def pages(user):
    ctx = context(user)
    page = {"flow": "one_page" if ld.variation("new_checkout", ctx, False) else "classic",
            "button": ld.variation("checkout_button", ctx, "buy_now"),
            "currency": ld.variation("pricing_tier", ctx, "usd")}
    if ld.variation("age_gate", ctx, False):
        page["age_check"] = True
    if ld.variation("beta_dashboard", ctx, False):
        page["layout"] = "beta"
    if ld.variation("pro_reports", ctx, False):
        page["reports"] = "pro"
    if ld.variation("upsell_banner", ctx, False):
        page["upsell"] = True
    if ld.variation("maintenance_mode", ctx, False):
        page["banner"] = "maintenance"
    return page
