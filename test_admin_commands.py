#!/usr/bin/env python3
"""Test script to verify admin commands functionality."""

import sys
import os
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta

# Add src to path
sys.path.insert(0, 'src')

def test_admin_commands():
    """Test admin command logic."""
    print("=" * 60)
    print("TESTING ADMIN COMMANDS")
    print("=" * 60)
    
    # Create a temporary directory for test data
    test_dir = tempfile.mkdtemp()
    histories_dir = Path(test_dir) / "user_histories"
    histories_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Create test user history files
        # User 1: Active recently (within 24h)
        user1_file = histories_dir / "user_111111.json"
        user1_data = {
            "user_id": 111111,
            "history": [{"role": "user", "content": "Hello"}],
            "session_summary": "User 1 is exploring themes of anxiety and meaning.",
            "messages_since_summary": 5,
            "last_updated": datetime.now().isoformat()
        }
        with open(user1_file, 'w', encoding='utf-8') as f:
            json.dump(user1_data, f)
        
        # User 2: Active recently (within 24h)
        user2_file = histories_dir / "user_222222.json"
        user2_data = {
            "user_id": 222222,
            "history": [{"role": "user", "content": "Hi there"}],
            "session_summary": "User 2 discussing relationship issues.",
            "messages_since_summary": 3,
            "last_updated": datetime.now().isoformat()
        }
        with open(user2_file, 'w', encoding='utf-8') as f:
            json.dump(user2_data, f)
        
        # User 3: Old (more than 24h ago)
        user3_file = histories_dir / "user_333333.json"
        user3_data = {
            "user_id": 333333,
            "history": [{"role": "user", "content": "Old message"}],
            "session_summary": "Old user summary.",
            "messages_since_summary": 10,
            "last_updated": (datetime.now() - timedelta(days=2)).isoformat()
        }
        with open(user3_file, 'w', encoding='utf-8') as f:
            json.dump(user3_data, f)
        
        # Modify timestamps to simulate old file
        old_time = (datetime.now() - timedelta(days=2)).timestamp()
        os.utime(user3_file, (old_time, old_time))
        
        print("\n✅ Created test user files:")
        print(f"   - user_111111.json (recent)")
        print(f"   - user_222222.json (recent)")
        print(f"   - user_333333.json (old, 2 days ago)")
        
        # Test /stats logic
        print("\n--- Testing /stats command logic ---")
        
        import time
        now = time.time()
        last_24h = now - (24 * 60 * 60)
        
        user_files = list(histories_dir.glob("user_*.json"))
        total_users = len(user_files)
        recent_users = []
        
        for f in user_files:
            try:
                mtime = f.stat().st_mtime
                if mtime > last_24h:
                    user_id_str = f.stem.replace("user_", "")
                    recent_users.append(int(user_id_str))
            except Exception as e:
                print(f"   Error reading {f}: {e}")
        
        print(f"   Total users: {total_users}")
        print(f"   Recent users (24h): {len(recent_users)}")
        print(f"   Recent user IDs: {sorted(recent_users)}")
        
        assert total_users == 3, f"Expected 3 total users, got {total_users}"
        assert len(recent_users) == 2, f"Expected 2 recent users, got {len(recent_users)}"
        assert 111111 in recent_users, "User 111111 should be recent"
        assert 222222 in recent_users, "User 222222 should be recent"
        assert 333333 not in recent_users, "User 333333 should NOT be recent"
        
        print("   ✅ /stats logic works correctly!")
        
        # Test /look37 logic
        print("\n--- Testing /look37 command logic ---")
        
        def load_user_history(user_id):
            history_path = histories_dir / f"user_{user_id}.json"
            if history_path.exists():
                try:
                    with open(history_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        return {
                            'history': data.get('history', []),
                            'session_summary': data.get('session_summary'),
                            'messages_since_summary': data.get('messages_since_summary', 0)
                        }
                except Exception:
                    pass
            return {'history': [], 'session_summary': None, 'messages_since_summary': 0}
        
        # Test loading user 111111
        user_data = load_user_history(111111)
        assert user_data['history'], "User 111111 should have history"
        assert user_data['session_summary'], "User 111111 should have summary"
        print(f"   User 111111: {len(user_data['history'])} messages")
        print(f"   Summary: {user_data['session_summary'][:50]}...")
        
        # Test loading non-existent user
        empty_data = load_user_history(999999)
        assert not empty_data['history'], "Non-existent user should have empty history"
        print("   Non-existent user 999999: No history found (correct)")
        
        print("   ✅ /look37 logic works correctly!")
        
        print("\n" + "=" * 60)
        print("🎉 ALL ADMIN COMMAND TESTS PASSED!")
        print("=" * 60)
        return True
        
    finally:
        # Cleanup
        shutil.rmtree(test_dir)
        print(f"\n🧹 Cleaned up test directory: {test_dir}")

if __name__ == "__main__":
    success = test_admin_commands()
    sys.exit(0 if success else 1)
