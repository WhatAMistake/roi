import sys
from pathlib import Path
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root / 'src'))

from therapist_bot import ExistentialTherapistBot


def count_ask_instructions(prob, iterations=200):
    bot = ExistentialTherapistBot(api_key='TEST', use_rag=False, language='en')
    bot.ask_question_prob = prob
    count = 0
    for _ in range(iterations):
        msgs = bot._build_messages('I am not sure about my decisions and feel lost')
        # second system message is at index 1
        if len(msgs) > 1 and isinstance(msgs[1].get('content'), str):
            content = msgs[1]['content']
            if 'Ask ONE' in content or 'Do NOT' in content:
                if 'Ask ONE' in content:
                    count += 1
    return count


if __name__ == '__main__':
    for p in [0.0, 0.1, 0.3, 0.6, 1.0]:
        cnt = count_ask_instructions(p, iterations=200)
        print(f'prob={p} -> ask_count={cnt} (out of 200)')
    print('Done')
