#!/usr/bin/env python3
"""Check the translations currently used by the bot's analysis commands."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from i18n import LOCALES, t


def test_i18n_keys():
    required = ("analyze_start", "story_too_short", "assoc_confirm",
                "meaning_system", "meaning_prompt")
    for lang in ("ru", "en"):
        for key in required:
            assert key in LOCALES[lang]
            assert t(lang, key, label="FREEDOM", words="one, two").strip() != key


def test_association_confirmation_template():
    for lang in ("ru", "en"):
        message = t(lang, "assoc_confirm", label="FREEDOM", words="one, two")
        assert "FREEDOM" in message
        assert "one, two" in message


def test_meaning_system_instruction():
    ru = t("ru", "meaning_system")
    en = t("en", "meaning_system")
    assert "наблюдатель" in ru.lower()
    assert "observer" in en.lower()
    assert ru != en


def test_meaning_prompts():
    ru = t("ru", "meaning_prompt")
    en = t("en", "meaning_prompt")
    assert ru and en and ru != en
    assert "наблюдени" in ru.lower()
    assert "observation" in en.lower()


def main():
    tests = (test_i18n_keys, test_association_confirmation_template,
             test_meaning_system_instruction, test_meaning_prompts)
    failed = False
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed = True
            print(f"FAILED: {test.__name__}: {exc}")
        else:
            print(f"PASSED: {test.__name__}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
