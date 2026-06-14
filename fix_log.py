import re

with open('src/therapist_bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_text = 'print(f"[ANALYZE] Raw response from LLM (story): {content[:200]}...")'
new_text = 'print(f"[ANALYZE] Raw response from LLM (story): {content}")'

content = content.replace(old_text, new_text)

with open('src/therapist_bot.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done")
