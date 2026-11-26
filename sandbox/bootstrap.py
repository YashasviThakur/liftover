"""Create a sandbox admin, organisation and admin API key on the local Flagsmith.

Idempotent. Writes sandbox/.env (git-ignored) with:
  FLAGSMITH_API_URL, FLAGSMITH_API_KEY_localhost_8000 (the env var name the CLI expects for this host),
  FLAGSMITH_ADMIN_EMAIL, FLAGSMITH_ADMIN_PASSWORD, FLAGSMITH_ORG_ID.
Secrets are never printed.
"""
import json
import secrets
import subprocess
import urllib.request
from pathlib import Path

API = "http://localhost:8000/api/v1"
ENV_FILE = Path(__file__).with_name(".env")
KEY_VAR = "FLAGSMITH_API_KEY_localhost_8000"


def load_env() -> dict:
    if not ENV_FILE.exists():
        return {}
    pairs = (line.split("=", 1) for line in ENV_FILE.read_text().splitlines() if "=" in line)
    return {k: v for k, v in pairs}


def save_env(env: dict) -> None:
    ENV_FILE.write_text("".join(f"{k}={v}\n" for k, v in env.items()))


def call(method: str, path: str, body=None, token=None, key=None):
    req = urllib.request.Request(f"{API}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Token {token}")
    if key:
        req.add_header("Authorization", f"Api-Key {key}")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or "null")


def main() -> None:
    env = load_env()
    env.setdefault("FLAGSMITH_API_URL", "http://localhost:8000")
    env.setdefault("FLAGSMITH_ADMIN_EMAIL", "admin@liftover.test")
    env.setdefault("FLAGSMITH_ADMIN_PASSWORD", secrets.token_urlsafe(18))
    save_env(env)

    # createsuperuser fails harmlessly when the user already exists.
    subprocess.run(
        ["docker", "compose", "exec", "-T",
         "-e", f"DJANGO_SUPERUSER_EMAIL={env['FLAGSMITH_ADMIN_EMAIL']}",
         "-e", f"DJANGO_SUPERUSER_PASSWORD={env['FLAGSMITH_ADMIN_PASSWORD']}",
         "-e", "DJANGO_SUPERUSER_FIRST_NAME=Sandbox", "-e", "DJANGO_SUPERUSER_LAST_NAME=Admin",
         "-e", "DJANGO_SUPERUSER_SIGN_UP_TYPE=NO_INVITE",
         "flagsmith", "python", "manage.py", "createsuperuser", "--noinput"],
        cwd=Path(__file__).parent, capture_output=True, check=False)

    token = call("POST", "/auth/login/", {"email": env["FLAGSMITH_ADMIN_EMAIL"],
                                          "password": env["FLAGSMITH_ADMIN_PASSWORD"]})["key"]

    # A free organisation holds one project, so each demo (Unleash, LaunchDarkly) gets its own organisation.
    # Created with the login token: creating one with an API key fails in 2.295.2 (500, HistoricalSubscription).
    for name, org_var, key_var in (("Liftover Sandbox", "FLAGSMITH_ORG_ID", KEY_VAR),
                                   ("Liftover LD Sandbox", "FLAGSMITH_LD_ORG_ID", "FLAGSMITH_LD_API_KEY")):
        orgs = {o["name"]: o for o in call("GET", "/organisations/", token=token)["results"]}
        org = orgs.get(name) or call("POST", "/organisations/", {"name": name}, token=token)
        env[org_var] = str(org["id"])
        if key_var not in env:
            env[key_var] = call("POST", f"/organisations/{org['id']}/master-api-keys/",
                                {"name": "liftover-sandbox", "is_admin": True}, token=token)["key"]
        save_env(env)
        # Prove the key works without printing it.
        visible = [o["id"] for o in call("GET", "/organisations/", key=env[key_var])["results"]]
        print(f"ok: organisation {name!r} (id {org['id']}), admin API key works: {org['id'] in visible}")
    print(f"wrote {ENV_FILE.name}")


if __name__ == "__main__":
    main()
