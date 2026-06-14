import re

with open('src/therapist_bot.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the print line and add validation
old_block = '''            content = response.choices[0].message.content
            print(f"[ANALYZE] Raw response from LLM (story): {content[:200]}...")
            
            return content.strip()'''

new_block = '''            content = response.choices[0].message.content
            print(f"[ANALYZE] Raw response from LLM (story): {content}")
            
            if not content or not content.strip():
                print(f"[ANALYZE] WARNING: LLM returned empty content!")
                return "Ошибка: LLM вернул пустой ответ. Попробуйте ещё раз."
            
            return content.strip()'''

content = content.replace(old_block, new_block)

with open('src/therapist_bot.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done")
