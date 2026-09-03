"""Reconstruct credentials/ (client_secret.json + token.json) from base64 .env vars."""
import base64
import os


def write(name: str, env: str) -> None:
    b64 = os.environ.get(env)
    if not b64:
        print(f"  {env} not set -> skipping credentials/{name}")
        return
    os.makedirs("credentials", exist_ok=True)
    with open(f"credentials/{name}", "wb") as f:
        f.write(base64.b64decode(b64))
    print(f"  credentials/{name} written")


def main() -> None:
    write("client_secret.json", "CLIENT_SECRET_JSON")
    write("token.json", "TOKEN_JSON")


if __name__ == "__main__":
    main()
