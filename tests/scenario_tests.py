from pathlib import Path
import sys
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root / 'src'))

from lang_utils import detect_language
from i18n import t


def simulate_text_flow(initial_lang, text):
    """Simulate Telegram text handling logic for language switching.
    Returns resulting lang after processing the text.
    Rules (copied from bot):
    - if len(text.strip()) >= 20: detect_language(text); if code startswith 'ru' and prob>0.75 -> 'ru'
    - otherwise keep initial_lang (or DEFAULT_LANG behavior handled by caller)
    """
    lang = initial_lang
    if len(text.strip()) >= 20:
        code, prob = detect_language(text)
        if code and code.startswith('ru') and prob > 0.75:
            lang = 'ru'
        else:
            # keep initial
            lang = initial_lang
    return lang


def simulate_voice_flow(initial_lang, transcribed_text):
    """Simulate voice handling logic: after transcription, detect_language and if ru with prob>0.6 switch to ru.
    """
    lang = initial_lang
    code, prob = detect_language(transcribed_text)
    if code and code.startswith('ru') and prob > 0.6:
        lang = 'ru'
    return lang


if __name__ == '__main__':
    errors = 0

    # Short text should not change language
    lang = simulate_text_flow('en', 'Hi')
    print('Short text ->', lang)
    if lang != 'en':
        print('ERROR: short text should not switch to ru')
        errors += 1

    # Long English text should stay en
    long_en = 'I feel a sense of unease about the choices I have made in my life.'
    lang = simulate_text_flow('en', long_en)
    print('Long English ->', lang)
    if lang != 'en':
        print('ERROR: long English should not switch to ru')
        errors += 1

    # Long Russian text should switch to ru
    long_ru = 'Я чувствую тревогу из-за принятых решений и не могу найти смысла в том, что происходит.'
    lang = simulate_text_flow('en', long_ru)
    print('Long Russian ->', lang)
    if lang != 'ru':
        print('ERROR: long Russian should switch to ru')
        errors += 1

    # Voice transcription simulation (Russian)
    transcribed = 'Мне кажется, что жизнь потеряла смысл и это пугает меня.'
    lang = simulate_voice_flow('en', transcribed)
    print('Voice Russian ->', lang)
    if lang != 'ru':
        print('ERROR: voice Russian should switch to ru')
        errors += 1

    # i18n checks
    try:
        en_w = t('en', 'welcome', name='Test')
        ru_w = t('ru', 'welcome', name='Тест')
        print('i18n samples ok')
    except Exception as e:
        print('i18n error', e)
        errors += 1

    if errors:
        print('SCENARIO TESTS FAILED with', errors, 'errors')
        sys.exit(1)
    else:
        print('SCENARIO TESTS PASSED')
        sys.exit(0)
