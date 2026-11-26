"""The shop's flag client: Unleash today. Liftover moves this to Flagsmith."""
import os

from UnleashClient import UnleashClient

unleash = UnleashClient(
    url=os.environ.get("UNLEASH_URL", "http://localhost:4242/api"),
    app_name="demo-shop",
    api_key=os.environ.get("UNLEASH_CLIENT_TOKEN", ""),  # the token picks the environment
)
unleash.initialize_client()


def context(user) -> dict:
    return {"userId": user["id"], "properties": {
        "plan": user.get("plan", "free"), "country": user.get("country", ""), "age": str(user.get("age", 0))}}
