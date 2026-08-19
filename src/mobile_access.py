import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
from pathlib import Path

from storage import app_data_dir


PASSWORD_ITERATIONS = 310_000
CHALLENGE_LIFETIME_SECONDS = 300


def is_loopback(address):
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def local_ipv4_addresses():
    addresses = set()
    try:
        hostname = socket.gethostname()
        for item in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = item[4][0]
            if not address.startswith("127.") and not address.startswith("169.254."):
                addresses.add(address)
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            address = sock.getsockname()[0]
            if not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


class MobileAccess:
    def __init__(self, pair_code=None, account_path=None):
        self.pair_code = str(pair_code or secrets.randbelow(1_000_000)).zfill(6)
        self.account_path = Path(account_path) if account_path else app_data_dir() / "mobile_access.json"
        self._tokens = set()
        self._challenges = {}
        self._failures = {}
        self._lock = threading.RLock()
        self._account = self._load_account()

    def _load_account(self):
        try:
            value = json.loads(self.account_path.read_text(encoding="utf-8"))
            required = {"username", "salt", "passwordHash", "iterations"}
            if required.issubset(value):
                return value
        except (OSError, ValueError, TypeError):
            pass
        return None

    @staticmethod
    def _password_hash(password, salt, iterations):
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
            dklen=32,
        )

    def configure(self, username, password):
        username = str(username or "").strip()
        password = str(password or "")
        if len(username) < 3 or len(username) > 64:
            raise ValueError("账号长度需为 3–64 个字符")
        if any(character.isspace() for character in username):
            raise ValueError("账号不能包含空格")
        if len(password) < 8:
            raise ValueError("密码至少需要 8 个字符")
        if len(password) > 256:
            raise ValueError("密码不能超过 256 个字符")

        salt = secrets.token_bytes(16)
        password_hash = self._password_hash(password, salt, PASSWORD_ITERATIONS)
        account = {
            "username": username,
            "salt": base64.b64encode(salt).decode("ascii"),
            "passwordHash": base64.b64encode(password_hash).decode("ascii"),
            "iterations": PASSWORD_ITERATIONS,
        }
        with self._lock:
            self.account_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.account_path.with_name(f".{self.account_path.name}.{secrets.token_hex(6)}.tmp")
            try:
                temporary.write_text(json.dumps(account, ensure_ascii=False), encoding="utf-8")
                try:
                    temporary.chmod(0o600)
                except OSError:
                    pass
                os.replace(str(temporary), str(self.account_path))
            finally:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
            self._account = account
            self._tokens.clear()
            self._challenges.clear()
            self._failures.clear()
        return {"ok": True, "username": username, "hasAccount": True}

    def _check_rate_limit(self, address, stage, now):
        key = (address, stage)
        failures, blocked_until = self._failures.get(key, (0, 0.0))
        if blocked_until > now:
            wait = max(1, int(blocked_until - now))
            raise PermissionError(f"尝试次数过多，请 {wait} 秒后重试")
        if blocked_until:
            self._failures.pop(key, None)
            return 0
        return failures

    def _record_failure(self, address, stage, failures, now):
        key = (address, stage)
        failures += 1
        blocked_until = now + 60 if failures >= 5 else 0.0
        self._failures[key] = (0 if blocked_until else failures, blocked_until)

    def login(self, username, password, address):
        username = str(username or "").strip()
        password = str(password or "")
        now = time.monotonic()
        with self._lock:
            failures = self._check_rate_limit(address, "login", now)
            account = self._account
            valid = False
            if account:
                try:
                    salt = base64.b64decode(account["salt"], validate=True)
                    expected = base64.b64decode(account["passwordHash"], validate=True)
                    actual = self._password_hash(password, salt, int(account["iterations"]))
                    username_valid = hmac.compare_digest(
                        username.encode("utf-8"), str(account["username"]).encode("utf-8")
                    )
                    valid = username_valid and hmac.compare_digest(actual, expected)
                except (KeyError, ValueError, TypeError):
                    valid = False
            if not account:
                raise PermissionError("请先在 Mac 上设置手机登录账号")
            if not valid:
                self._record_failure(address, "login", failures, now)
                raise PermissionError("账号或密码不正确")

            challenge = secrets.token_urlsafe(32)
            self._challenges[challenge] = (address, now + CHALLENGE_LIFETIME_SECONDS)
            self._failures.pop((address, "login"), None)
            self._remove_expired_challenges(now)
            return challenge

    def _remove_expired_challenges(self, now):
        expired = [key for key, (_, expires_at) in self._challenges.items() if expires_at <= now]
        for key in expired:
            self._challenges.pop(key, None)

    def pair(self, code, challenge, address):
        now = time.monotonic()
        challenge = str(challenge or "").strip()
        with self._lock:
            failures = self._check_rate_limit(address, "pair", now)
            self._remove_expired_challenges(now)
            challenge_value = self._challenges.get(challenge)
            if not challenge_value or challenge_value[0] != address:
                raise PermissionError("账号验证已失效，请重新输入账号和密码")
            if not hmac.compare_digest(str(code).strip(), self.pair_code):
                self._record_failure(address, "pair", failures, now)
                raise PermissionError("配对码不正确")
            token = secrets.token_urlsafe(32)
            self._tokens.add(token)
            self._challenges.pop(challenge, None)
            self._failures.pop((address, "pair"), None)
            return token

    def authorized(self, authorization, address):
        if is_loopback(address):
            return True
        prefix = "Bearer "
        if not authorization or not authorization.startswith(prefix):
            return False
        token = authorization[len(prefix):].strip()
        with self._lock:
            return token in self._tokens

    def account_info(self):
        with self._lock:
            return {
                "ok": True,
                "hasAccount": self._account is not None,
                "username": str(self._account.get("username", "")) if self._account else "",
            }

    def info(self, address, port):
        local = is_loopback(address)
        account = self.account_info()
        result = {
            "ok": True,
            "local": local,
            "requiresPairing": not local,
            "hasAccount": account["hasAccount"],
            "port": port,
        }
        if local:
            addresses = local_ipv4_addresses()
            result["username"] = account["username"]
            result["pairCode"] = self.pair_code
            result["urls"] = [f"http://{value}:{port}/" for value in addresses]
        return result
