#!/usr/bin/env python3
"""Read-only update preview. --llm explicitly opts in to paid API requests.

The production cache and snapshots are copied into a temporary directory.
No Telegram messages are sent and no production file is changed.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from code_reviewer import UpdateCache, collect_update_diff, split_update_diff, _recover_snapshot


def load_therapist_bot():
    # Preserve the normal module identity for all other consumers.
    from therapist_bot import ExistentialTherapistBot
    return ExistentialTherapistBot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", action="store_true",
                        help="Use the configured LLM (may incur charges and sends diff to provider)")
    args = parser.parse_args(argv)
    production = UpdateCache(project_root)
    if not production.path.exists():
        print("No confirmed baseline; dry-run will not create one.")
        return 1
    data = production._read()
    with tempfile.TemporaryDirectory(prefix="roi-changelog-") as directory:
        root = Path(directory)
        # Capture the exact current files once; do not copy other runtime data.
        _, snapshots = production._capture()
        for rel, content in snapshots.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode("utf-8"))
        cache = UpdateCache(root)
        cache.path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(production.path, cache.path)
        backups = project_root / "data" / "code_backup"
        if backups.is_dir():
            shutil.copytree(backups, root / "data" / "code_backup")
        pending = cache.prepare()
        if not pending:
            print("No changes.")
            return 0
        state = cache._read()
        baseline = {key: value for key, value in state.items() if isinstance(value, str)}
        change = collect_update_diff(project_root, baseline, state["_update_state"]["snapshots"], pending)
        print(f"Files: {change['file_count']}; lines: +{change['added']} -{change['removed']}; "
              f"fragments: {len(split_update_diff(change))}")
        if change["missing"]:
            print("Unverified: " + ", ".join(change["missing"]))
        if not args.llm:
            print("Structural dry-run only; pass --llm to generate with the configured provider.")
            return 0
        if pending["status"] == "sending" or any(pending["notes"].values()):
            print("Existing approved/edited draft; it will not be regenerated.")
            print(json.dumps(pending["notes"], ensure_ascii=False, indent=2))
            return 0
        from dotenv import load_dotenv
        load_dotenv(project_root / ".env")
        therapist = load_therapist_bot()(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            api_key=os.getenv("OPENAI_API_KEY"),
            api_base=os.getenv("OPENAI_API_BASE"),
            use_rag=False,
        )
        # Recover against the real Git repository, but persist only to the copy.
        for rel in pending["paths"]:
            if rel in baseline and rel not in state["_update_state"]["snapshots"]:
                recovered = _recover_snapshot(project_root, rel, baseline[rel])
                if recovered is not None:
                    state["_update_state"]["snapshots"][rel] = recovered
        cache._write(state)
        draft = cache.generate(therapist, pending)
        print(json.dumps({"notes": draft["notes"], "reason": draft["reason"],
                          "analysis": {k: v for k, v in draft.get("analysis", {}).items()
                                       if k not in ("fragments", "findings")}},
                         ensure_ascii=False, indent=2))
        return 1 if draft["reason"].startswith("Не удалось") else 0


if __name__ == "__main__":
    sys.exit(main())
