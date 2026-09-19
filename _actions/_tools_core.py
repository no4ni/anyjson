"""Ядро tools: глобальные пути, крипто, init_tools.

Этот модуль — основа для остальных _tools_*.py.
Здесь хранятся:
  • _BASE_DIR, _AGENT_NAME, _MY_DIR, _PROFILES_DIR
  • _CRYPTO (CryptoManager)
  • PUBLIC_TOOLS_DIR, _WIKIGET_PATH, _TOOLS_DIR

Остальные _tools_*.py читают эти значения через геттеры:
  from _actions import _tools_core
  base = _tools_core.get_base_dir()
"""
from pathlib import Path


# ---------- Глобальная конфигурация ----------
_BASE_DIR = None
_AGENT_NAME = None
_MY_DIR = None
_PROFILES_DIR = None
_WIKI_TEXTS_DIR = None
_CANONICAL_ALL_PATH = None
_CRYPTO = None
_TOOLS_DIR = None
PUBLIC_TOOLS_DIR = None
_WIKIGET_PATH = None


def get_base_dir():
    return _BASE_DIR


def get_agent_name():
    return _AGENT_NAME


def get_my_dir():
    return _MY_DIR


def get_profiles_dir():
    return _PROFILES_DIR


def get_crypto():
    return _CRYPTO


def get_wikiget_path():
    return _WIKIGET_PATH


def get_public_tools_dir():
    return PUBLIC_TOOLS_DIR


def get_canonical_all_path():
    return _CANONICAL_ALL_PATH


def set_canonical_all_path(path):
    global _CANONICAL_ALL_PATH
    _CANONICAL_ALL_PATH = Path(path) if path else None


def get_tools_dir():
    return _TOOLS_DIR


# ---------- Криптоменеджер ----------
class CryptoManager:
    def __init__(self, key_dir):
        from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa
        self.key_dir = Path(key_dir)
        self.key_dir.mkdir(parents=True, exist_ok=True)
        self.private_key_path = self.key_dir / "private_key.pem"
        self.public_key_path = self.key_dir / "public_key.pem"
        self.private_key = None
        self.public_key = None
        self._load_or_generate_keys()

    def _load_or_generate_keys(self):
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
        if self.private_key_path.exists() and self.public_key_path.exists():
            with open(self.private_key_path, "rb") as f:
                self.private_key = serialization.load_pem_private_key(
                    f.read(), password=None, backend=default_backend()
                )
            with open(self.public_key_path, "rb") as f:
                self.public_key = serialization.load_pem_public_key(
                    f.read(), backend=default_backend()
                )
        else:
            self.private_key = ed25519.Ed25519PrivateKey.generate()
            self.public_key = self.private_key.public_key()
            with open(self.private_key_path, "wb") as f:
                f.write(self.private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption()
                ))
            with open(self.public_key_path, "wb") as f:
                f.write(self.public_key.public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo
                ))

    def get_public_key_hex(self):
        from cryptography.hazmat.primitives import serialization
        return self.public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        ).hex()

    def sign_message(self, message_dict):
        import json
        message_dict.pop("signature", None)
        message_json = json.dumps(message_dict, sort_keys=True, separators=(',', ':'))
        signature = self.private_key.sign(message_json.encode('utf-8')).hex()
        message_dict["signature"] = signature
        return message_dict

    @staticmethod
    def verify_message(message_dict, public_key_hex):
        from cryptography.hazmat.primitives.asymmetric import ed25519
        signature_hex = message_dict.pop("signature", None)
        if not signature_hex:
            return False
        try:
            signature = bytes.fromhex(signature_hex)
            public_key_bytes = bytes.fromhex(public_key_hex)
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
            import json
            message_json = json.dumps(message_dict, sort_keys=True, separators=(',', ':'))
            public_key.verify(signature, message_json.encode('utf-8'))
            return True
        except Exception:
            return False


# ---------- init_tools ----------
def init_tools(base_dir, agent_name, wiki_texts_dir=None, tools_dir=None,
               public_tools_dir=None, wikiget_path=None):
    """Устанавливает глобальные пути. Вызывать один раз при старте."""
    global _BASE_DIR, _AGENT_NAME, _MY_DIR, _PROFILES_DIR, _CRYPTO
    global _TOOLS_DIR, PUBLIC_TOOLS_DIR, _WIKIGET_PATH, _WIKI_TEXTS_DIR

    _BASE_DIR = Path(base_dir)
    _AGENT_NAME = agent_name

    # пути берём из _config — единый источник правды
    try:
        from _config import PROFILES_DIR as _CFG_PROFILES, MY_DIR as _CFG_MY
        _PROFILES_DIR = _CFG_PROFILES
        _MY_DIR = _CFG_MY
    except Exception:
        _MY_DIR = _BASE_DIR / agent_name / "memory"
        _PROFILES_DIR = _BASE_DIR / "simnet" / "profiles"

    _MY_DIR.mkdir(parents=True, exist_ok=True)
    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)

    _CRYPTO = CryptoManager(_MY_DIR)

    if wiki_texts_dir:
        _WIKI_TEXTS_DIR = Path(wiki_texts_dir)

    _WIKIGET_PATH = Path(wikiget_path) if wikiget_path else None

    _TOOLS_DIR = Path(tools_dir) if tools_dir else Path(__file__).parent
    PUBLIC_TOOLS_DIR = Path(public_tools_dir) if public_tools_dir else Path(__file__).parent


