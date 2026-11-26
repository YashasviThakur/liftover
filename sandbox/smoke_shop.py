"""Run the demo shop's pages for fixed users and print them as JSON, to diff the shop before and after cutover.

  python smoke_shop.py SHOP_REPO ENV_FILE > out.json      (ENV_FILE = the deploy's env, e.g. .env.production)
Reads client settings from sandbox/.env: UNLEASH_CLIENT_TOKEN_PRODUCTION before, FLAGSMITH_ENVIRONMENT_KEY after.
"""
import json
import os
import random
import sys
from pathlib import Path

repo, env_file = Path(sys.argv[1]), Path(sys.argv[2])
sandbox = dict(l.split("=", 1) for l in Path(__file__).with_name(".env").read_text().splitlines() if "=" in l)
os.environ.update(dict(l.split("=", 1) for l in env_file.read_text().splitlines() if "=" in l))
os.environ.setdefault("UNLEASH_URL", sandbox["UNLEASH_URL"])
os.environ.setdefault("UNLEASH_CLIENT_TOKEN", sandbox["UNLEASH_CLIENT_TOKEN_PRODUCTION"])
os.environ.setdefault("FLAGSMITH_API_URL", sandbox["FLAGSMITH_API_URL"].rstrip("/") + "/api/v1/")
sys.path.insert(0, str(repo))

from shop.checkout import checkout_page  # noqa: E402
from shop.dashboard import dashboard  # noqa: E402
from shop.site import banner  # noqa: E402

rnd = random.Random(1)
users = [{"id": uid, "plan": rnd.choice(["free", "pro", "enterprise"]), "country": rnd.choice(["IN", "US", "FR"]),
          "age": rnd.randint(10, 40), "is_new": rnd.random() < 0.5}
         for uid in ["u-001", "u-002", "vip-1"] + [f"user-{i:04d}" for i in range(200)]]
print(json.dumps({"banner": banner(), "pages": {u["id"]: {"checkout": checkout_page(u), "dashboard": dashboard(u)}
                                                for u in users}}, indent=1, sort_keys=True))
