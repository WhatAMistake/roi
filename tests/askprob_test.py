"""Inspect the soft question-initiative policy at a few preference values."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from therapist_bot import ExistentialTherapistBot


if __name__ == "__main__":
    bot = ExistentialTherapistBot(api_key="TEST", use_rag=False, language="en")
    for preference in (0.0, 0.5, 1.0):
        bot.ask_question_prob = preference
        print(f"{preference}: {bot._build_messages('I feel lost')[-2]['content']}")
