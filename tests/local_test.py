import sys
from pathlib import Path
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root / 'src'))

print('PYTHON PATH:', sys.path[0])

errors = 0

# Test lang_utils
try:
    from lang_utils import detect_language
    print('detect_language("Hello...") ->', detect_language('Hello, how are you?'))
    print('detect_language("Привет...") ->', detect_language('Привет, как дела?'))
except Exception as e:
    print('lang_utils import/error:', e)
    errors += 1

# Test i18n
try:
    from i18n import t
    print('i18n en welcome:', t('en', 'welcome', name='Test')[:80])
    print('i18n ru welcome:', t('ru', 'welcome', name='Тест')[:80])
except Exception as e:
    print('i18n import/error:', e)
    errors += 1

# Test therapist_bot prompt loading
try:
    from therapist_bot import ExistentialTherapistBot
    # Provide a dummy api_key so the OpenAI client does not raise during init in tests
    be = ExistentialTherapistBot(language='en', use_rag=False, api_key='TEST_KEY')
    br = ExistentialTherapistBot(language='ru', use_rag=False, api_key='TEST_KEY')
    print('EN prompt snippet:', be.system_prompt[:120].replace('\n',' ') )
    print('RU prompt snippet:', br.system_prompt[:120].replace('\n',' ') )
    if 'Irvin' not in be.system_prompt and 'Yalom' not in be.system_prompt:
        print('Warning: English prompt may not contain expected tokens')
except Exception as e:
    print('therapist_bot import/error:', e)
    errors += 1

if errors:
    print('TESTS FAILED with', errors, 'errors')
    sys.exit(1)
else:
    print('SMOKE TESTS PASSED')
    sys.exit(0)
