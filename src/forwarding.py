import hashlib
import json
import os
import smtplib
import ssl
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def app_data_dir():
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "SIMBridge"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "SIMBridge"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "SIMBridge"


APP_DATA_DIR = app_data_dir()
CONFIG_PATH = APP_DATA_DIR / "forwarding.json"
STATE_PATH = APP_DATA_DIR / "forwarding-state.json"
LOCK_PATH = APP_DATA_DIR / "forwarding.lock"
KEYCHAIN_SERVICE = "com.wangquanrun.simbridge.smtp"
KEYCHAIN_ACCOUNT = "smtp-password"
MAX_SEEN_MESSAGES = 2000

DEFAULT_CONFIG = {
    "enabled": False,
    "smtpHost": "",
    "smtpPort": 587,
    "security": "starttls",
    "username": "",
    "sender": "",
    "recipients": [],
    "subjectPrefix": "[SIMBridge]",
    "pollInterval": 15,
    "includePdu": False,
    "activationId": "",
}


class ForwardingError(RuntimeError):
    pass


def send_system_notification(title, message, subtitle=""):
    if sys.platform == "win32":
        return send_windows_notification(title, message, subtitle)
    if sys.platform != "darwin":
        return False

    try:
        from Foundation import NSUserNotification, NSUserNotificationCenter
        notification = NSUserNotification.alloc().init()
        notification.setTitle_(str(title))
        if subtitle:
            notification.setSubtitle_(str(subtitle))
        notification.setInformativeText_(str(message))
        center = NSUserNotificationCenter.defaultUserNotificationCenter()
        center.deliverNotification_(notification)
        return True
    except Exception:
        pass

    try:
        script = f'display notification {json.dumps(str(message))} with title {json.dumps(str(title))}'
        if subtitle:
            script += f' subtitle {json.dumps(str(subtitle))}'
        subprocess.run(["osascript", "-e", script], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def send_windows_notification(title, message, subtitle=""):
    def quoted(value):
        return str(value).replace("'", "''")

    body = f"{subtitle}\n{message}" if subtitle else str(message)
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Information;"
        "$n.Visible=$true;"
        f"$n.BalloonTipTitle='{quoted(title)}';"
        f"$n.BalloonTipText='{quoted(body)}';"
        "$n.ShowBalloonTip(5000);Start-Sleep -Seconds 6;$n.Dispose()"
    )
    try:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        return True
    except Exception:
        return False


def now_text():
    return datetime.now().isoformat(timespec="seconds")


def bool_value(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def parse_recipients(value):
    if isinstance(value, list):
        parts = value
    else:
        parts = str(value or "").replace(";", ",").replace("\n", ",").split(",")
    recipients = []
    for part in parts:
        address = parseaddr(str(part).strip())[1]
        if address and address not in recipients:
            recipients.append(address)
    return recipients


def normalize_config(value, current=None):
    source = dict(DEFAULT_CONFIG)
    if current:
        source.update(current)
    value = value or {}

    port = value.get("smtpPort", source["smtpPort"])
    interval = value.get("pollInterval", source["pollInterval"])
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ForwardingError("SMTP 端口必须是数字")
    try:
        interval = int(interval)
    except (TypeError, ValueError):
        raise ForwardingError("检查间隔必须是数字")
    if not 1 <= port <= 65535:
        raise ForwardingError("SMTP 端口必须在 1 到 65535 之间")
    if not 10 <= interval <= 300:
        raise ForwardingError("检查间隔必须在 10 到 300 秒之间")

    security = str(value.get("security", source["security"])).lower()
    if security not in ("starttls", "ssl", "none"):
        raise ForwardingError("未知的 SMTP 加密方式")

    return {
        "enabled": bool_value(value.get("enabled", source["enabled"])),
        "smtpHost": str(value.get("smtpHost", source["smtpHost"])).strip()[:255],
        "smtpPort": port,
        "security": security,
        "username": str(value.get("username", source["username"])).strip()[:320],
        "sender": str(value.get("sender", source["sender"])).strip()[:320],
        "recipients": parse_recipients(value.get("recipients", source["recipients"])),
        "subjectPrefix": str(value.get("subjectPrefix", source["subjectPrefix"])).strip()[:120],
        "pollInterval": interval,
        "includePdu": bool_value(value.get("includePdu", source["includePdu"])),
        "activationId": str(source.get("activationId", "")),
    }


def validate_config(config, password_available=False):
    if not config["smtpHost"]:
        raise ForwardingError("请填写 SMTP 服务器")
    if not config["sender"] or "@" not in parseaddr(config["sender"])[1]:
        raise ForwardingError("请填写有效的发件邮箱")
    if not config["recipients"] or any("@" not in address for address in config["recipients"]):
        raise ForwardingError("请至少填写一个有效的收件邮箱")
    if config["username"] and not password_available:
        raise ForwardingError("请填写 SMTP 密码或授权码")


def read_json(path, default):
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else dict(default)
    except (OSError, ValueError):
        return dict(default)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(temp_path, 0o600)
    temp_path.replace(path)


class ForwardingFileLock:
    def __enter__(self):
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.handle = LOCK_PATH.open("a+", encoding="utf-8")
        os.chmod(LOCK_PATH, 0o600)
        if os.name == "nt":
            self.handle.seek(0, os.SEEK_END)
            if self.handle.tell() == 0:
                self.handle.write("\0")
                self.handle.flush()
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        if os.name == "nt":
            self.handle.seek(0)
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def load_config():
    return normalize_config(read_json(CONFIG_PATH, DEFAULT_CONFIG))


def keychain_password():
    if sys.platform == "win32":
        return windows_credential_password()
    if sys.platform != "darwin":
        return ""
    result = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a",
            KEYCHAIN_ACCOUNT,
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.rstrip("\n") if result.returncode == 0 else ""


def save_keychain_password(password):
    if sys.platform == "win32":
        save_windows_credential_password(password)
        return
    if sys.platform != "darwin":
        raise ForwardingError("当前系统不支持安全保存 SMTP 密码")
    result = subprocess.run(
        [
            "/usr/bin/security",
            "add-generic-password",
            "-U",
            "-a",
            KEYCHAIN_ACCOUNT,
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
            password,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ForwardingError(result.stderr.strip() or "无法把 SMTP 密码保存到 macOS 钥匙串")


def delete_keychain_password():
    if sys.platform == "win32":
        delete_windows_credential_password()
        return
    if sys.platform != "darwin":
        return
    subprocess.run(
        [
            "/usr/bin/security",
            "delete-generic-password",
            "-a",
            KEYCHAIN_ACCOUNT,
            "-s",
            KEYCHAIN_SERVICE,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def windows_credential_types():
    import ctypes
    from ctypes import wintypes

    class FileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

    class Credential(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", FileTime),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    return ctypes, wintypes, Credential


def windows_credential_password():
    ctypes, wintypes, Credential = windows_credential_types()
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    credential = ctypes.POINTER(Credential)()
    advapi32.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(Credential)),
    ]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    if not advapi32.CredReadW(KEYCHAIN_SERVICE, 1, 0, ctypes.byref(credential)):
        return ""
    try:
        value = credential.contents
        raw = ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize)
        return raw.decode("utf-16-le")
    finally:
        advapi32.CredFree(credential)


def save_windows_credential_password(password):
    ctypes, wintypes, Credential = windows_credential_types()
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    blob = str(password).encode("utf-16-le")
    buffer = (wintypes.BYTE * len(blob)).from_buffer_copy(blob)
    credential = Credential()
    credential.Type = 1
    credential.TargetName = KEYCHAIN_SERVICE
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(wintypes.BYTE))
    credential.Persist = 2
    credential.UserName = KEYCHAIN_ACCOUNT
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    if not advapi32.CredWriteW(ctypes.byref(credential), 0):
        error = ctypes.get_last_error()
        raise ForwardingError(f"无法把 SMTP 密码保存到 Windows 凭据管理器（错误 {error}）")


def delete_windows_credential_password():
    ctypes, wintypes, _Credential = windows_credential_types()
    advapi32 = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL
    if not advapi32.CredDeleteW(KEYCHAIN_SERVICE, 1, 0):
        error = ctypes.get_last_error()
        if error != 1168:
            raise ForwardingError(f"无法从 Windows 凭据管理器删除 SMTP 密码（错误 {error}）")


def message_fingerprint(message):
    source = {
        "index": message.get("index"),
        "pdu": message.get("pdu", ""),
        "decoded": message.get("decoded", {}),
    }
    data = json.dumps(source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def build_forwarding_email(config, message, test=False):
    decoded = message.get("decoded") or {}
    peer = decoded.get("peer") or "未知号码"
    timestamp = decoded.get("timestamp") or now_text().replace("T", " ")
    text = decoded.get("text") or decoded.get("error") or "(空内容)"
    prefix = config.get("subjectPrefix") or "[SIMBridge]"

    email = EmailMessage()
    email["From"] = config["sender"]
    email["To"] = ", ".join(config["recipients"])
    email["Subject"] = f"{prefix} {'测试邮件' if test else f'来自 {peer} 的新短信'}"
    if test:
        body = "SIMBridge 邮件转发测试成功。\n\n收到此邮件表示 SMTP 配置可以正常发送。"
    else:
        lines = [
            "SIMBridge 收到一条新短信。",
            "",
            f"号码：{peer}",
            f"时间：{timestamp}",
            f"模块索引：{message.get('index', '-')}",
            "",
            "短信内容：",
            text,
        ]
        if config.get("includePdu") and message.get("pdu"):
            lines.extend(["", "原始 PDU：", message["pdu"]])
        body = "\n".join(lines)
    email.set_content(body)
    return email


def send_smtp(config, email, password=""):
    validate_config(config, password_available=bool(password) or not config["username"])
    context = ssl.create_default_context()
    if config["security"] == "ssl":
        with smtplib.SMTP_SSL(
            config["smtpHost"],
            config["smtpPort"],
            timeout=20,
            context=context,
        ) as smtp:
            if config["username"]:
                smtp.login(config["username"], password)
            smtp.send_message(email)
        return

    with smtplib.SMTP(config["smtpHost"], config["smtpPort"], timeout=20) as smtp:
        smtp.ehlo()
        if config["security"] == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        if config["username"]:
            smtp.login(config["username"], password)
        smtp.send_message(email)


class ForwardingService:
    def __init__(self, messages_loader):
        self.messages_loader = messages_loader
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()
        self.runtime = {
            "running": False,
            "lastCheck": "",
            "lastForwardedAt": "",
            "lastError": "",
            "forwardedCount": 0,
        }

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="sms-email-forwarder", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)

    def _set_runtime(self, **values):
        with self.lock:
            self.runtime.update(values)

    def _record_forwarded(self):
        with self.lock:
            self.runtime.update({
                "lastForwardedAt": now_text(),
                "lastError": "",
                "forwardedCount": self.runtime.get("forwardedCount", 0) + 1,
            })

    def settings(self):
        config = load_config()
        password_set = bool(keychain_password())
        with self.lock:
            runtime = dict(self.runtime)
        public = {key: value for key, value in config.items() if key != "activationId"}
        public.update(runtime)
        public.update({"ok": True, "passwordSet": password_set})
        return public

    def update(self, value):
        current = load_config()
        config = normalize_config(value, current=current)
        supplied_password = str((value or {}).get("password", ""))
        clear_password = bool_value((value or {}).get("clearPassword", False))
        existing_password = keychain_password()
        password_available = bool(supplied_password or (existing_password and not clear_password))

        if config["enabled"]:
            validate_config(config, password_available=password_available or not config["username"])

        if supplied_password:
            save_keychain_password(supplied_password)
        elif clear_password:
            delete_keychain_password()

        if config["enabled"] and not current.get("enabled"):
            config["activationId"] = uuid.uuid4().hex
        elif not config["enabled"]:
            config["activationId"] = current.get("activationId", "")

        write_json(CONFIG_PATH, config)
        self.wake_event.set()
        return self.settings()

    def send_test(self, value):
        current = load_config()
        config = normalize_config(value, current=current)
        supplied_password = str((value or {}).get("password", ""))
        password = supplied_password or keychain_password()
        validate_config(config, password_available=bool(password) or not config["username"])
        send_smtp(config, build_forwarding_email(config, {}, test=True), password=password)
        return {"ok": True, "message": "测试邮件已发送"}

    def _run(self):
        self._set_runtime(running=True)
        try:
            while not self.stop_event.is_set():
                config = load_config()
                try:
                    self._poll_once(config)
                except Exception as exc:
                    self._set_runtime(lastCheck=now_text(), lastError=str(exc))
                interval = config.get("pollInterval", 15)
                self.wake_event.wait(interval)
                self.wake_event.clear()
        finally:
            self._set_runtime(running=False)

    def _poll_once(self, config=None):
        with ForwardingFileLock():
            return self._poll_once_locked(config)

    def _poll_once_locked(self, config=None):
        config = config or load_config()
        result = self.messages_loader("inbox")
        messages = result.get("messages") or []
        state = read_json(STATE_PATH, {"activationId": "", "seen": []})
        fingerprints = [message_fingerprint(message) for message in messages]

        if state.get("activationId") != config.get("activationId"):
            write_json(
                STATE_PATH,
                {"activationId": config.get("activationId", ""), "seen": fingerprints[-MAX_SEEN_MESSAGES:]},
            )
            self._set_runtime(lastCheck=now_text(), lastError="")
            return 0

        seen = list(state.get("seen") or [])
        seen_set = set(seen)
        password = keychain_password()
        forwarded = 0
        for message, fingerprint in zip(messages, fingerprints):
            if fingerprint in seen_set:
                continue

            # Send a native system notification.
            decoded = message.get("decoded") or {}
            peer = decoded.get("peer") or f"索引 {message.get('index', '')}"
            text = decoded.get("text") or decoded.get("error") or "(空内容)"
            send_system_notification(
                title="SIMBridge",
                subtitle=f"收到来自 {peer} 的新短信",
                message=text,
            )

            # Send Email Forwarding if enabled
            if config.get("enabled"):
                try:
                    email = build_forwarding_email(config, message)
                    send_smtp(config, email, password=password)
                    self._record_forwarded()
                except Exception as exc:
                    self._set_runtime(lastError=str(exc))

            seen.append(fingerprint)
            seen_set.add(fingerprint)
            forwarded += 1
            write_json(
                STATE_PATH,
                {"activationId": config.get("activationId", ""), "seen": seen[-MAX_SEEN_MESSAGES:]},
            )

        self._set_runtime(lastCheck=now_text(), lastError="")
        return forwarded
