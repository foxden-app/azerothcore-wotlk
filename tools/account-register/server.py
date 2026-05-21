#!/usr/bin/env python3
"""Lightweight AzerothCore account registration page.

The service intentionally does not write SRP6 fields directly. It validates a
small form and delegates account creation to the worldserver SOAP command:

    account create <username> <password> [email]
"""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import html
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


DEFAULT_SOAP_URL = "http://127.0.0.1:7879/"
DEFAULT_SOAP_USER = "SOAP_PANEL"
DEFAULT_GM_ENV_FILE = "~/.config/acore/gm.env"
SAVED_ENV_KEYS = {"ACORE_GM_PASS", "ACORE_GM_USER", "ACORE_SOAP_URL"}

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_]{2,16}$")
PASSWORD_RE = re.compile(r"^[A-Za-z0-9_@#%+=:,.!?-]{6,16}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}$")


def load_saved_gm_env() -> None:
    env_file = Path(os.environ.get("ACCOUNT_REGISTER_GM_ENV_FILE", DEFAULT_GM_ENV_FILE)).expanduser()
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    except OSError as exc:
        log_event("config_env_read_error", path=str(env_file), error=str(exc))
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()

        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key in SAVED_ENV_KEYS and key not in os.environ:
            os.environ[key] = value.strip().strip("\"'")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}


def env_int(name: str, default: int, *, minimum: int = 0, maximum: int = 10_000) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def env_value(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    soap_url: str
    soap_user: str
    soap_password: str
    invite_code: str
    require_invite: bool
    trust_proxy: bool
    rate_limit_per_hour: int
    realm_name: str
    public_base_url: str
    soap_timeout: int


def load_config() -> Config:
    load_saved_gm_env()
    return Config(
        host=os.getenv("ACCOUNT_REGISTER_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=env_int("ACCOUNT_REGISTER_PORT", 18080, minimum=1, maximum=65535),
        soap_url=os.getenv("ACCOUNT_REGISTER_SOAP_URL", os.getenv("ACORE_SOAP_URL", DEFAULT_SOAP_URL)).strip()
        or DEFAULT_SOAP_URL,
        soap_user=os.getenv("ACCOUNT_REGISTER_SOAP_USER", os.getenv("ACORE_GM_USER", DEFAULT_SOAP_USER)).strip()
        or DEFAULT_SOAP_USER,
        soap_password=env_value("ACCOUNT_REGISTER_SOAP_PASSWORD", os.getenv("ACORE_GM_PASS", "")),
        invite_code=os.getenv("ACCOUNT_REGISTER_INVITE_CODE", "").strip(),
        require_invite=env_bool("ACCOUNT_REGISTER_REQUIRE_INVITE", True),
        trust_proxy=env_bool("ACCOUNT_REGISTER_TRUST_PROXY", False),
        rate_limit_per_hour=env_int("ACCOUNT_REGISTER_RATE_LIMIT_PER_HOUR", 12, minimum=1, maximum=240),
        realm_name=os.getenv("ACCOUNT_REGISTER_REALM_NAME", "Agent PlayerBot").strip() or "Agent PlayerBot",
        public_base_url=os.getenv("ACCOUNT_REGISTER_PUBLIC_BASE_URL", "").strip(),
        soap_timeout=env_int("ACCOUNT_REGISTER_SOAP_TIMEOUT", 20, minimum=3, maximum=120),
    )


def log_event(event: str, **fields: Any) -> None:
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event,
        **fields,
    }
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    if ":" in tag:
        return tag.rsplit(":", 1)[1]
    return tag


def build_soap_payload(command: str) -> bytes:
    escaped = html.escape(command, quote=False)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="urn:AC">
  <SOAP-ENV:Body>
    <ns1:executeCommand>
      <command>{escaped}</command>
    </ns1:executeCommand>
  </SOAP-ENV:Body>
</SOAP-ENV:Envelope>
""".encode("utf-8")


def parse_soap_response(body: bytes) -> tuple[bool, str]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        text = body.decode("utf-8", errors="replace").strip()
        return (True, text) if text else (True, "")

    for elem in root.iter():
        if local_name(elem.tag) == "Fault":
            parts: list[str] = []
            for child in elem.iter():
                if local_name(child.tag) in {"faultstring", "detail"} and child.text:
                    parts.append(child.text.strip())
            return False, " ".join(parts).strip() or "SOAP fault"

    for elem in root.iter():
        if local_name(elem.tag) in {"result", "executeCommandResult"}:
            return True, (elem.text or "").strip()

    return True, body.decode("utf-8", errors="replace").strip()


def build_account_create_command(username: str, password: str, email_value: str = "") -> str:
    parts = ["account", "create", username, password]
    if email_value:
        parts.append(email_value)
    return " ".join(parts)


def account_create_succeeded(message: str, username: str) -> bool:
    text = message.strip()
    lower = text.lower()
    if "account created" in lower or "创建帐号" in text or "创建账号" in text:
        return True
    if not text:
        return False

    failure_markers = (
        "already exist",
        "already exists",
        "not created",
        "can't be longer",
        "too long",
        "sql",
        "failed",
        "failure",
        "错误",
        "失败",
        "重复",
        "过长",
    )
    if any(marker in lower or marker in text for marker in failure_markers):
        return False

    # AzerothCore account create is expected to print LANG_ACCOUNT_CREATED.
    # An unrecognized response is safer to treat as a failed registration.
    log_event("account_create_unrecognized_response", username=username, response=text[:240])
    return False


def friendly_account_create_error(message: str) -> str:
    lower = message.lower()
    if "already" in lower or "exist" in lower or "已存在" in message or "重复" in message:
        return "这个账号已经存在。"
    if "too long" in lower or "longer than" in lower or "过长" in message or "长度" in message:
        return "账号或密码长度不符合 AzerothCore 限制。"
    if "soap password" in lower or "403" in lower or "401" in lower:
        return "注册服务配置不完整，请联系管理员。"
    return "账号没有创建成功。"


def execute_soap_command(config: Config, command: str) -> tuple[bool, str]:
    if not config.soap_password:
        return False, "SOAP password is not configured"

    token = base64.b64encode(f"{config.soap_user}:{config.soap_password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        config.soap_url,
        data=build_soap_payload(command),
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": '"urn:AC#executeCommand"',
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=config.soap_timeout) as response:
            return parse_soap_response(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read()
        if body:
            ok, message = parse_soap_response(body)
            return False, message if message else f"HTTP {exc.code}: {exc.reason}"
        return False, f"HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        return False, f"SOAP connection failed: {exc.reason}"
    except TimeoutError:
        return False, "SOAP request timed out"


def validate_registration(form: dict[str, str], config: Config) -> list[str]:
    errors: list[str] = []
    username = form.get("username", "").strip()
    password = form.get("password", "")
    confirm = form.get("confirm", "")
    email_value = form.get("email", "").strip()
    invite = form.get("invite", "").strip()

    if config.require_invite and invite != config.invite_code:
        errors.append("邀请码不正确。")
    if not USERNAME_RE.match(username):
        errors.append("账号必须是 3-17 位英文、数字或下划线，并且不能以下划线开头。")
    if not PASSWORD_RE.match(password):
        errors.append("密码必须是 6-16 位，不能包含空格；允许英文、数字和 _@#%+=:,.!?-。")
    if password != confirm:
        errors.append("两次输入的密码不一致。")
    if email_value and (len(email_value) > 255 or not EMAIL_RE.match(email_value)):
        errors.append("邮箱格式不正确。")
    return errors


class RateLimiter:
    def __init__(self, limit_per_hour: int) -> None:
        self.limit_per_hour = limit_per_hour
        self.hits: dict[str, list[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        cutoff = now - 3600
        items = [ts for ts in self.hits.get(key, []) if ts > cutoff]
        if len(items) >= self.limit_per_hour:
            self.hits[key] = items
            return False
        items.append(now)
        self.hits[key] = items
        return True


def one(values: dict[str, list[str]], key: str) -> str:
    return values.get(key, [""])[0]


def render_page(
    config: Config,
    *,
    csrf: str,
    values: dict[str, str] | None = None,
    errors: list[str] | None = None,
    success: str = "",
) -> str:
    values = values or {}
    errors = errors or []
    username = html.escape(values.get("username", ""))
    email_value = html.escape(values.get("email", ""))
    invite = html.escape(values.get("invite", ""))
    realm_name = html.escape(config.realm_name)
    csrf = html.escape(csrf)
    error_html = "".join(f"<li>{html.escape(item)}</li>" for item in errors)
    success_html = f'<div class="notice success">{html.escape(success)}</div>' if success else ""
    invite_field = (
        f"""
        <label>
          <span>邀请码</span>
          <input name="invite" value="{invite}" autocomplete="one-time-code" required>
        </label>
        """
        if config.require_invite
        else ""
    )
    error_block = f'<div class="notice error"><ul>{error_html}</ul></div>' if errors else ""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{realm_name} 注册</title>
  <style>
    :root {{ color-scheme: light; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }}
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f4f6f8; color: #111827; }}
    main {{ width: min(420px, calc(100vw - 32px)); }}
    h1 {{ margin: 0 0 6px; font-size: 24px; letter-spacing: 0; }}
    p {{ margin: 0 0 20px; color: #4b5563; line-height: 1.5; }}
    form {{ background: white; border: 1px solid #d7dde5; border-radius: 8px; padding: 22px; box-shadow: 0 10px 30px rgba(15, 23, 42, .08); }}
    label {{ display: block; margin: 0 0 14px; }}
    span {{ display: block; margin: 0 0 6px; font-size: 13px; color: #374151; }}
    input {{ width: 100%; box-sizing: border-box; height: 40px; border: 1px solid #cbd5e1; border-radius: 6px; padding: 0 10px; font-size: 15px; }}
    input:focus {{ outline: 2px solid #2563eb33; border-color: #2563eb; }}
    button {{ width: 100%; height: 42px; border: 0; border-radius: 6px; background: #1f2937; color: white; font-size: 15px; cursor: pointer; }}
    button:hover {{ background: #111827; }}
    .notice {{ border-radius: 6px; padding: 10px 12px; margin: 0 0 14px; line-height: 1.45; }}
    .notice ul {{ margin: 0; padding-left: 20px; }}
    .error {{ background: #fef2f2; color: #991b1b; border: 1px solid #fecaca; }}
    .success {{ background: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0; }}
    .hint {{ margin-top: 14px; font-size: 12px; color: #6b7280; }}
  </style>
</head>
<body>
  <main>
    <form method="post" action="/register" autocomplete="off">
      <h1>{realm_name} 注册</h1>
      <p>创建一个用于登录 WotLK 客户端的游戏账号。</p>
      {success_html}
      {error_block}
      <input type="hidden" name="csrf" value="{csrf}">
      {invite_field}
      <label>
        <span>账号</span>
        <input name="username" value="{username}" maxlength="17" pattern="[A-Za-z0-9][A-Za-z0-9_]{{2,16}}" required>
      </label>
      <label>
        <span>邮箱（可选）</span>
        <input type="email" name="email" value="{email_value}" maxlength="255">
      </label>
      <label>
        <span>密码</span>
        <input type="password" name="password" maxlength="16" required>
      </label>
      <label>
        <span>确认密码</span>
        <input type="password" name="confirm" maxlength="16" required>
      </label>
      <button type="submit">创建账号</button>
      <div class="hint">密码不会写入日志；注册由 worldserver SOAP 执行 AzerothCore 原生命令。</div>
    </form>
  </main>
</body>
</html>
"""


class AccountRegisterHandler(BaseHTTPRequestHandler):
    server_version = "ACoreAccountRegister/0.1"

    def config(self) -> Config:
        return self.server.config  # type: ignore[attr-defined]

    def rate_limiter(self) -> RateLimiter:
        return self.server.rate_limiter  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        log_event("http_access", client=self.client_ip(), message=fmt % args)

    def client_ip(self) -> str:
        config = self.config()
        if config.trust_proxy:
            forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            if forwarded:
                return forwarded
        return self.client_address[0]

    def security_headers(self) -> None:
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")

    def send_html(
        self,
        status: HTTPStatus,
        body: str,
        *,
        csrf: str | None = None,
        include_body: bool = True,
    ) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.security_headers()
        if csrf:
            self.send_header("Set-Cookie", f"acr_csrf={csrf}; HttpOnly; SameSite=Lax; Path=/")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if include_body:
            self.wfile.write(data)

    def send_text(self, status: HTTPStatus, body: str, *, include_body: bool = True) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.security_headers()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if include_body:
            self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path in {"/health", "/healthz"}:
            self.send_text(HTTPStatus.OK, "ok\n")
            return

        if urllib.parse.urlparse(self.path).path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        csrf = secrets.token_urlsafe(24)
        self.send_html(HTTPStatus.OK, render_page(self.config(), csrf=csrf), csrf=csrf)

    def do_HEAD(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in {"/health", "/healthz"}:
            self.send_text(HTTPStatus.OK, "ok\n", include_body=False)
            return
        if path == "/":
            self.send_html(
                HTTPStatus.OK,
                render_page(self.config(), csrf=""),
                include_body=False,
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if urllib.parse.urlparse(self.path).path != "/register":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        config = self.config()
        client = self.client_ip()
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > 8192:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return

        body = self.rfile.read(length).decode("utf-8", errors="replace")
        parsed = urllib.parse.parse_qs(body, keep_blank_values=True, max_num_fields=12)
        form = {
            "username": one(parsed, "username").strip(),
            "email": one(parsed, "email").strip(),
            "password": one(parsed, "password"),
            "confirm": one(parsed, "confirm"),
            "invite": one(parsed, "invite").strip(),
        }

        csrf_form = one(parsed, "csrf")
        csrf_cookie = ""
        for part in self.headers.get("Cookie", "").split(";"):
            key, sep, value = part.strip().partition("=")
            if sep and key == "acr_csrf":
                csrf_cookie = value
                break

        errors = []
        if not csrf_form or not csrf_cookie or not secrets.compare_digest(csrf_form, csrf_cookie):
            errors.append("表单已过期，请刷新后重试。")

        if not self.rate_limiter().allow(client):
            errors.append("这个 IP 的注册尝试太频繁，请稍后再试。")

        errors.extend(validate_registration(form, config))
        csrf = secrets.token_urlsafe(24)
        safe_values = {"username": form["username"], "email": form["email"], "invite": form["invite"]}

        if errors:
            log_event("register_rejected", client=client, username=form["username"], reason="validation")
            self.send_html(
                HTTPStatus.BAD_REQUEST,
                render_page(config, csrf=csrf, values=safe_values, errors=errors),
                csrf=csrf,
            )
            return

        command = build_account_create_command(form["username"], form["password"], form["email"])
        ok, message = execute_soap_command(config, command)
        if ok:
            ok = account_create_succeeded(message, form["username"])
        if not ok:
            user_message = friendly_account_create_error(message)
            log_event("register_failed", client=client, username=form["username"], soap_error=message[:240])
            self.send_html(
                HTTPStatus.BAD_REQUEST,
                render_page(config, csrf=csrf, values=safe_values, errors=[user_message]),
                csrf=csrf,
            )
            return

        log_event("register_created", client=client, username=form["username"], email=bool(form["email"]))
        self.send_html(
            HTTPStatus.OK,
            render_page(
                config,
                csrf=csrf,
                success=f"账号 {form['username']} 已创建，可以登录 {config.realm_name}。",
            ),
            csrf=csrf,
        )


class AccountRegisterServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], config: Config) -> None:
        super().__init__(server_address, AccountRegisterHandler)
        self.config = config
        self.rate_limiter = RateLimiter(config.rate_limit_per_hour)


def check_config(config: Config) -> int:
    issues = []
    if config.require_invite and not config.invite_code:
        issues.append("ACCOUNT_REGISTER_INVITE_CODE is required when ACCOUNT_REGISTER_REQUIRE_INVITE=1")
    if not config.soap_password:
        issues.append("ACCOUNT_REGISTER_SOAP_PASSWORD or ACORE_GM_PASS is required")
    for issue in issues:
        print(issue, file=sys.stderr)
    return 1 if issues else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the lightweight AzerothCore account registration page.")
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()

    config = load_config()
    if args.check_config:
        return check_config(config)

    if check_config(config):
        return 2

    server = AccountRegisterServer((config.host, config.port), config)

    def stop(signum: int, _frame: Any) -> None:
        log_event("shutdown_signal", signal=signum)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log_event(
        "startup",
        host=config.host,
        port=config.port,
        soap_url=config.soap_url,
        soap_user=config.soap_user,
        require_invite=config.require_invite,
        rate_limit_per_hour=config.rate_limit_per_hour,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log_event("shutdown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
