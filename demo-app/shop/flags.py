"""The shop's flag client: Unleash today. Liftover moves this to Flagsmith."""
import os

from UnleashClient import UnleashClient

unleash = UnleashClient(
    url=os.environ.get("UNLEASH_URL", "http://localhost:4242/api"),
    app_name="demo-shop",
    environment=os.environ.get("APP_ENV", "production"),
    custom_headers={"Authorization": os.environ.get("UNLEASH_CLIENT_TOKEN", "")},
)


def context(user) -> dict:
    return {"userId": user["id"], "properties": {
        "plan": user.get("plan", "free"), "country": user.get("country", ""), "age": str(user.get("age", 0))}}
