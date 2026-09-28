"""Reconstruct credentials/ (client_secret.json + token.json) from base64 .env vars.

Both files must decode to valid JSON, and a value that does not is REFUSED: a
truncated paste used to be base64-decoded into binary garbage that was written
straight over a working credential file, which then failed much later with a
confusing "UnicodeDecodeError" from somewhere else entirely.
"""
import base64
import binascii
import json
import os


def write(name: str, env: str) -> bool:
    """Decode env var -> credentials/<name>. Returns True when the file was written."""
    b64 = os.environ.get(env)
    if not b64 or not b64.strip():
        print(f"  {env} not set -> skipping credentials/{name}")
        return False
    b64 = b64.strip()

    try:
        raw = base64.b64decode(b64)  # lenient about wrapping/whitespace
    except (binascii.Error, ValueError) as e:
        print(f"  !! {env} is not valid base64 ({e}) -> credentials/{name} left untouched")
        return False

    try:
        json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        print(f"  !! {env} ({len(b64)} chars) decoded to {len(raw)} bytes that are not "
              f"JSON ({type(e).__name__}) -> credentials/{name} left untouched.")
        print(f"     Usual cause: a truncated paste. The value must be the whole "
              f"single-line output of: base64 -i credentials/token.json | tr -d '\\n'")
        return False

    os.makedirs("credentials", exist_ok=True)
    with open(f"credentials/{name}", "wb") as f:
        f.write(raw)
    print(f"  credentials/{name} written ({len(raw)} bytes from {len(b64)} base64 chars)")
    return True


def main() -> None:
    write("client_secret.json", "CLIENT_SECRET_JSON")
    write("token.json", "TOKEN_JSON")

    # A corrupt token.json is worse than a missing one: it fails later, elsewhere,
    # with an unrelated-looking error. Remove it so the next run reports the real
    # problem ("token invalid, re-consent") instead.
    path = "credentials/token.json"
    if os.path.exists(path):
        try:
            json.loads(open(path, encoding="utf-8").read())
        except (UnicodeDecodeError, ValueError, OSError):
            os.remove(path)
            print(f"  !! {path} was not valid JSON -> removed, so the next run "
                  f"reports the real problem instead of a decode error")


if __name__ == "__main__":
    main()
