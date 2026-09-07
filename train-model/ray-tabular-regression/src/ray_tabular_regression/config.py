import hashlib
import yaml

from .binding import BindingError


def load_bound_config(path, binding):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != binding["config_digest"]:
        raise BindingError("config digest does not match signed binding")
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise BindingError("config must be a mapping")
    return value
