import re

with open('src/therapist_bot.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find line 966 and replace it
for i in range(len(lines)):
    if '[ANALYZE] Raw response from LLM (story):' in lines[i] and '[:200]' in lines[i]:
        lines[i] = '            print(f"[ANALYZE] Raw response from LLM (story): {content}")\n'
        print(f"Found and replaced line {i+1}")
        break

with open('src/therapist_bot.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Done")
