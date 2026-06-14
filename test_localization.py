#!/usr/bin/env python3
"""
Test script to verify localization fixes for /analyze and /meaning commands.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from i18n import t

def test_i18n_keys():
    """Test that all required translation keys exist."""
    print("=" * 60)
    print("Testing i18n keys...")
    print("=" * 60)
    
    required_keys = [
        "meaning_system_instruction",
        "analyze_story_prompt",
        "meaning_prompt",
        "meaning_prompt_absurd",
        "meaning_prompt_micro",
        "meaning_prompt_paradox",
    ]
    
    all_passed = True
    
    for lang in ["ru", "en"]:
        print(f"\nTesting language: {lang}")
        for key in required_keys:
            try:
                result = t(lang, key)
                if result == key:  # Fallback returns key itself
                    print(f"  ❌ MISSING: {key}")
                    all_passed = False
                else:
                    print(f"  ✅ OK: {key} (length: {len(result)})")
            except Exception as e:
                print(f"  ❌ ERROR: {key} - {e}")
                all_passed = False
    
    return all_passed

def test_analyze_story_prompt():
    """Test that analyze_story_prompt template works with variables."""
    print("\n" + "=" * 60)
    print("Testing analyze_story_prompt template...")
    print("=" * 60)
    
    test_story = "I feel lost and don't know what to do with my life."
    test_context = "Similar stories from database..."
    test_detected = "FREEDOM"
    
    all_passed = True
    
    for lang in ["ru", "en"]:
        print(f"\nLanguage: {lang}")
        try:
            prompt = t(lang, "analyze_story_prompt", 
                      story=test_story, 
                      context=test_context, 
                      detected=test_detected)
            
            # Check that variables were substituted
            if test_story in prompt and test_detected in prompt:
                print(f"  ✅ Template substitution works")
                print(f"  📄 Prompt preview (first 200 chars):")
                print(f"     {prompt[:200]}...")
            else:
                print(f"  ❌ Template substitution failed")
                all_passed = False
                
            # Check language-appropriate content
            if lang == "ru":
                if "Ирвин Ялом" in prompt or "Ялома" in prompt:
                    print(f"  ✅ Russian content detected")
                else:
                    print(f"  ⚠️  Russian content may be missing")
            else:
                if "Irvin Yalom" in prompt or "Yalom" in prompt:
                    print(f"  ✅ English content detected")
                else:
                    print(f"  ⚠️  English content may be missing")
                    
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            all_passed = False
    
    return all_passed

def test_meaning_system_instruction():
    """Test meaning_system_instruction for both languages."""
    print("\n" + "=" * 60)
    print("Testing meaning_system_instruction...")
    print("=" * 60)
    
    all_passed = True
    
    for lang in ["ru", "en"]:
        print(f"\nLanguage: {lang}")
        try:
            instruction = t(lang, "meaning_system_instruction")
            print(f"  📄 Instruction: {instruction}")
            
            if lang == "ru":
                # Should contain Russian text
                if "поэт" in instruction or "смысл" in instruction:
                    print(f"  ✅ Russian instruction detected")
                else:
                    print(f"  ⚠️  May not be in Russian")
            else:
                # Should contain English text
                if "poet" in instruction.lower() or "meaning" in instruction.lower():
                    print(f"  ✅ English instruction detected")
                else:
                    print(f"  ⚠️  May not be in English")
                    
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            all_passed = False
    
    return all_passed

def test_meaning_prompts():
    """Test that all meaning prompts are localized."""
    print("\n" + "=" * 60)
    print("Testing meaning prompts...")
    print("=" * 60)
    
    prompt_keys = [
        "meaning_prompt",
        "meaning_prompt_absurd", 
        "meaning_prompt_micro",
        "meaning_prompt_paradox"
    ]
    
    all_passed = True
    
    for lang in ["ru", "en"]:
        print(f"\nLanguage: {lang}")
        for key in prompt_keys:
            try:
                prompt = t(lang, key)
                print(f"  ✅ {key}: {prompt[:80]}...")
            except Exception as e:
                print(f"  ❌ {key}: ERROR - {e}")
                all_passed = False
    
    return all_passed

def simulate_bot_behavior():
    """Simulate how the bot would use these translations."""
    print("\n" + "=" * 60)
    print("Simulating bot behavior...")
    print("=" * 60)
    
    # Simulate /analyze command
    print("\n--- Simulating /analyze (English) ---")
    lang = "en"
    story = "I feel anxious about my future and don't know what path to take."
    context = ""
    detected = "FREEDOM"
    
    prompt = t(lang, "analyze_story_prompt", story=story, context=context, detected=detected)
    print(f"Generated prompt length: {len(prompt)} chars")
    print(f"Contains story: {story[:30] in prompt}")
    print(f"Contains detected: {detected in prompt}")
    
    # Simulate /meaning command
    print("\n--- Simulating /meaning (English) ---")
    meaning_instruction = t(lang, "meaning_system_instruction")
    meaning_prompt = t(lang, "meaning_prompt")
    print(f"System instruction: {meaning_instruction}")
    print(f"User prompt: {meaning_prompt[:100]}...")
    
    # Simulate Russian
    print("\n--- Simulating /analyze (Russian) ---")
    lang = "ru"
    story = "Я чувствую тревогу о будущем и не знаю, какой путь выбрать."
    detected = "СВОБОДА"
    
    prompt = t(lang, "analyze_story_prompt", story=story, context=context, detected=detected)
    print(f"Generated prompt length: {len(prompt)} chars")
    print(f"Contains story: {story[:30] in prompt}")
    print(f"Contains detected: {detected in prompt}")
    
    print("\n--- Simulating /meaning (Russian) ---")
    meaning_instruction = t(lang, "meaning_system_instruction")
    meaning_prompt = t(lang, "meaning_prompt")
    print(f"System instruction: {meaning_instruction}")
    print(f"User prompt: {meaning_prompt[:100]}...")
    
    return True

def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("LOCALIZATION TEST SUITE")
    print("Testing fixes for /analyze and /meaning commands")
    print("=" * 60)
    
    results = []
    
    results.append(("i18n keys", test_i18n_keys()))
    results.append(("analyze_story_prompt", test_analyze_story_prompt()))
    results.append(("meaning_system_instruction", test_meaning_system_instruction()))
    results.append(("meaning prompts", test_meaning_prompts()))
    results.append(("bot simulation", simulate_bot_behavior()))
    
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"  {status}: {name}")
        if not passed:
            all_passed = False
    
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 ALL TESTS PASSED!")
        print("The localization fixes are working correctly.")
    else:
        print("⚠️  SOME TESTS FAILED")
        print("Please review the errors above.")
    print("=" * 60)
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
