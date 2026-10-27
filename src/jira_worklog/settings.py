"""Jira URL and credentials.

Read order: environment variables, then a .env file in the current folder, then the config file
that the first start writes. The token is never a command-line option.
"""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from dotenv import dotenv_values

KEYS = ("JIRA_URL", "JIRA_PERSONAL_TOKEN", "JIRA_USERNAME", "JIRA_API_TOKEN", "JIRA_VERIFY_SSL")
CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "jira-worklog" / "config.env"


@dataclass(frozen=True)
class JiraSettings:
    url: str
    personal_token: str | None = None
    username: str | None = None
    api_token: str | None = None
    verify_ssl: bool = True

    @property
    def complete(self) -> bool:
        return bool(self.url and (self.personal_token or (self.username and self.api_token)))


def _read(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if k in KEYS and v}


def load_settings(
    environ: Mapping[str, str] = os.environ,
    dotenv_file: Path = Path(".env"),
    config_file: Path = CONFIG_FILE,
) -> JiraSettings:
    values = {**_read(config_file), **_read(dotenv_file), **{k: environ[k] for k in KEYS if environ.get(k)}}
    return JiraSettings(
        url=values.get("JIRA_URL", "").strip().rstrip("/"),
        personal_token=values.get("JIRA_PERSONAL_TOKEN"),
        username=values.get("JIRA_USERNAME"),
        api_token=values.get("JIRA_API_TOKEN"),
        verify_ssl=values.get("JIRA_VERIFY_SSL", "true").strip().lower() in {"1", "true", "yes", "y", "on"},
    )


def save_settings(settings: JiraSettings, config_file: Path = CONFIG_FILE) -> None:
    """Write the config file. Only the user can read it (mode 600)."""
    lines = [f"JIRA_URL={settings.url}"]
    if settings.personal_token:
        lines.append(f"JIRA_PERSONAL_TOKEN={settings.personal_token}")
    if settings.username and settings.api_token:
        lines += [f"JIRA_USERNAME={settings.username}", f"JIRA_API_TOKEN={settings.api_token}"]
    if not settings.verify_ssl:
        lines.append("JIRA_VERIFY_SSL=false")
    config_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(config_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    config_file.chmod(0o600)


def ask_settings(
    url: str,
    check: Callable[[JiraSettings], str | None],
    ask: Callable[[str], str] = input,
    ask_secret: Callable[[str], str] = getpass.getpass,
    say: Callable[[str], None] = print,
    confirm_url: bool = False,
) -> JiraSettings:
    """Ask in the terminal until Jira accepts the URL and the token. check returns an error text or None.

    A known url is not asked again, unless confirm_url is true. Then it is the default answer.
    """
    ask_url = confirm_url or not url
    while True:
        if ask_url:
            default = f" [{url}]" if url else " (for example https://jira.example.com)"
            url = (ask(f"Jira URL{default}: ").strip() or url).rstrip("/")
            if not url:
                continue
        token = ask_secret("Jira personal access token (input is hidden): ").strip()
        if not token:
            continue
        settings = JiraSettings(url=url, personal_token=token)
        error = check(settings)
        if error is None:
            return settings
        say(f"Jira did not accept these values: {error}")
        ask_url = True
