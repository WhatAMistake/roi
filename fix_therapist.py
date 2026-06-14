with open('src/therapist_bot.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find and replace line 966
for i, line in enumerate(lines):
    if '[ANALYZE] Raw response from LLM (story): {content[:200]}...' in line:
        lines[i] = '            print(f"[ANALYZE] Raw response from LLM (story): {content}")\n'
        # Insert new lines after
        lines.insert(i+1, '            \n')
        lines.insert(i+2, '            if not content or not content.strip():\n')
        lines.insert(i+3, '                print(f"[ANALYZE] WARNING: LLM returned empty content!")\n')
        lines.insert(i+4, '                return "Ошибка: LLM вернул пустой ответ. Попробуйте ещё раз."\n')
        lines.insert(i+5, '            \n')
        break

with open('src/therapist_bot.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Done")
