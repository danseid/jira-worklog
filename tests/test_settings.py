from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from jira_worklog.settings import JiraSettings, ask_settings, load_settings, save_settings


class TestLoadSettings(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.dotenv = self.tmp / ".env"
        self.config = self.tmp / "config" / "config.env"

    def load(self, environ: dict[str, str] | None = None) -> JiraSettings:
        return load_settings(environ=environ or {}, dotenv_file=self.dotenv, config_file=self.config)

    def test_nothing_set(self) -> None:
        settings = self.load()
        self.assertEqual(settings.url, "")
        self.assertFalse(settings.complete)

    def test_order_environment_then_dotenv_then_config(self) -> None:
        self.config.parent.mkdir()
        self.config.write_text("JIRA_URL=https://config.example\nJIRA_PERSONAL_TOKEN=from-config\n")
        self.dotenv.write_text("JIRA_URL=https://dotenv.example\n")
        settings = self.load({"JIRA_PERSONAL_TOKEN": "from-env"})
        self.assertEqual(settings.url, "https://dotenv.example")
        self.assertEqual(settings.personal_token, "from-env")
        self.assertTrue(settings.complete)

    def test_basic_auth_and_ssl(self) -> None:
        settings = self.load(
            {
                "JIRA_URL": "https://jira.example/",
                "JIRA_USERNAME": "jdoe",
                "JIRA_API_TOKEN": "secret",
                "JIRA_VERIFY_SSL": "false",
            }
        )
        self.assertEqual(settings.url, "https://jira.example")
        self.assertTrue(settings.complete)
        self.assertFalse(settings.verify_ssl)

    def test_url_without_token_is_not_complete(self) -> None:
        self.assertFalse(self.load({"JIRA_URL": "https://jira.example"}).complete)

    def test_save_then_load(self) -> None:
        save_settings(JiraSettings(url="https://jira.example", personal_token="abc"), self.config)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        settings = self.load()
        self.assertEqual(settings.url, "https://jira.example")
        self.assertEqual(settings.personal_token, "abc")
        self.assertTrue(settings.verify_ssl)

    def test_save_keeps_ssl_off(self) -> None:
        save_settings(JiraSettings(url="https://jira.example", personal_token="abc", verify_ssl=False), self.config)
        self.assertFalse(self.load().verify_ssl)


class TestAskSettings(unittest.TestCase):
    def run_ask(self, url: str, answers: list[str], secrets: list[str], errors: list[str | None]):
        asked: list[str] = []
        said: list[str] = []
        checked: list[JiraSettings] = []

        def ask(prompt: str) -> str:
            asked.append(prompt)
            return answers.pop(0)

        def ask_secret(prompt: str) -> str:
            asked.append(prompt)
            return secrets.pop(0)

        def check(settings: JiraSettings) -> str | None:
            checked.append(settings)
            return errors.pop(0)

        result = ask_settings(url, check, ask=ask, ask_secret=ask_secret, say=said.append)
        return result, asked, said, checked

    def test_asks_url_and_token(self) -> None:
        result, asked, _, _ = self.run_ask("", ["https://jira.example/"], ["abc"], [None])
        self.assertEqual(result, JiraSettings(url="https://jira.example", personal_token="abc"))
        self.assertEqual(len(asked), 2)

    def test_known_url_asks_only_the_token(self) -> None:
        result, asked, _, _ = self.run_ask("https://jira.example", [], ["abc"], [None])
        self.assertEqual(result.url, "https://jira.example")
        self.assertEqual(len(asked), 1)

    def test_asks_again_when_jira_refuses(self) -> None:
        result, asked, said, checked = self.run_ask(
            "https://jira.example", [""], ["wrong", "right"], ["HTTP 401", None]
        )
        self.assertEqual(result.personal_token, "right")
        self.assertIn("https://jira.example", asked[1])  # the URL is the default in the second round
        self.assertEqual(len(checked), 2)
        self.assertTrue(any("HTTP 401" in line for line in said))

    def test_empty_token_is_asked_again_without_check(self) -> None:
        result, _, _, checked = self.run_ask("https://jira.example", [], ["", "abc"], [None])
        self.assertEqual(result.personal_token, "abc")
        self.assertEqual(len(checked), 1)

    def test_setup_asks_the_known_url_again(self) -> None:
        asked: list[str] = []
        answers = ["https://new.example"]

        def ask(prompt: str) -> str:
            asked.append(prompt)
            return answers.pop(0)

        result = ask_settings(
            "https://old.example", lambda s: None, ask=ask, ask_secret=lambda p: "abc", confirm_url=True
        )
        self.assertIn("https://old.example", asked[0])
        self.assertEqual(result.url, "https://new.example")
