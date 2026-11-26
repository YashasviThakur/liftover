from .flags import context, unleash


def checkout_page(user) -> dict:
    ctx = context(user)
    page = {"flow": "classic", "button": "Buy now"}
    if unleash.is_enabled("new_checkout", ctx):
        page["flow"] = "one_page"
    variant = unleash.get_variant("checkout_button", ctx)
    if variant["enabled"]:
        page["button"] = {"buy_now": "Buy now", "get_it_today": "Get it today",
                          "add_to_bag": "Add to bag"}[variant["payload"]["value"]]
    if unleash.is_enabled("age_gate", ctx):
        page["age_check"] = True
    return page
