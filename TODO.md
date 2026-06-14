# Fix Implementation TODO

## Issue 1: Reset not clearing user data
- [ ] Edit `src/telegram_bot.py` - `_handle_reset` method
  - Add: `self.user_message_counts[user_id] = 0`
  - Add: `self.user_recent_messages[user_id] = []`

## Issue 2: RAG not working for English analyze/assoc
- [ ] Edit `src/therapist_bot.py` - `analyze_associations` method
  - Add translation of user input to Russian before RAG search
- [ ] Edit `src/therapist_bot.py` - `analyze_story` method
  - Add translation of story to Russian before RAG search

## Issue 3: Bot constantly pushes existential themes
- [ ] Edit `prompts/system_prompt.md`
  - Remove aggressive existential theme pushing
  - Focus on client's pain first
  - Make existential themes conditional
- [ ] Edit `prompts/system_prompt.en.md`
  - Same changes as Russian version

## Issue 4: Update notifications too compressed
- [ ] Edit `src/code_reviewer.py`
  - Expand bullet descriptions from ~55 to ~80 chars

## Issue 5: askprob 0.0 still asks questions (NEW)
- [ ] Edit `prompts/system_prompt.md` and `prompts/system_prompt.en.md`
  - Remove any language that mandates asking questions
  - Let the probability logic control question-asking
