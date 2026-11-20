"""Generate a dev-mode RS256 keypair: jwks.json (public) + private_key.pem.

The private key signs dev tokens only and must never be committed —
AUTH_MODE=entra removes it entirely.
"""

import argparse
import json
import secrets
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate(out_dir: Path, kid: str | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    kid = kid or secrets.token_hex(8)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": kid, "alg": "RS256", "use": "sig"})

    (out_dir / "private_key.pem").write_bytes(private_pem)
    (out_dir / "jwks.json").write_text(json.dumps({"keys": [jwk]}, indent=2))
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path(".dev-keys"))
    parser.add_argument("--kid", default=None)
    args = parser.parse_args()
    out = generate(args.out_dir, args.kid)
    print(f"Wrote {out / 'jwks.json'} and {out / 'private_key.pem'}")


if __name__ == "__main__":
    main()
