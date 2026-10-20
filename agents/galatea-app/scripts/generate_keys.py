import argparse
from pathlib import Path
import secrets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

p = argparse.ArgumentParser()
p.add_argument("directory")
a = p.parse_args()
root = Path(a.directory)
root.mkdir(parents=True, exist_ok=True, mode=0o700)
key = Ed25519PrivateKey.generate()
values = {
    "host-private.pem": key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ),
    "host-public.pem": key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ),
}
values.update(
    {
        name + ".token": (secrets.token_urlsafe(48) + "\n").encode()
        for name in ["mcp", "provider", "exporter", "continuation"]
    }
)
for name, value in values.items():
    with (root / name).open("xb") as f:
        (root / name).chmod(0o600)
        f.write(value)
print("Created protected credential files; values are not printed.")
