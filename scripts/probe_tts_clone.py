"""Offline configuration check by default; --live creates one billable task."""

import argparse
import csv
import getpass
import json
import logging
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid
import warnings

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from tts_service import (CloneError, clone_speech, configuration, reference_data_uri,  # noqa: E402
                         resume_clone_speech, resume_configuration, valid_task_id)


def private_directory():
    """Use POSIX owner-only permissions or a Windows ACL for the current user."""
    directory = ROOT / "temp_audio" / ".tts_probe"
    try:
        directory.parent.mkdir(exist_ok=True)
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError
        if os.name == "nt":
            result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                                    capture_output=True, text=True, check=True)
            sid = next(csv.reader(result.stdout.splitlines()))[1]
            if not sid.startswith("S-1-") or not all(c.isdigit() or c in "S-" for c in sid):
                raise ValueError
            quoted_path = str(directory).replace("'", "''")
            script = (
                "$ErrorActionPreference='Stop';"
                f"$d=Get-Item -LiteralPath '{quoted_path}';"
                "$acl=$d.GetAccessControl();$acl.SetAccessRuleProtection($true,$false);"
                "foreach($r in @($acl.GetAccessRules($true,$false,"
                "[System.Security.Principal.SecurityIdentifier])))"
                "{$acl.RemoveAccessRuleSpecific($r)};"
                f"$sid=[System.Security.Principal.SecurityIdentifier]::new('{sid}');"
                "$rule=[System.Security.AccessControl.FileSystemAccessRule]::new("
                "$sid,[System.Security.AccessControl.FileSystemRights]::FullControl,"
                "([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor "
                "[System.Security.AccessControl.InheritanceFlags]::ObjectInherit),"
                "[System.Security.AccessControl.PropagationFlags]::None,"
                "[System.Security.AccessControl.AccessControlType]::Allow);"
                "$acl.AddAccessRule($rule);$d.SetAccessControl($acl)"
            )
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, check=True)
        else:
            if directory.stat().st_uid != os.geteuid():
                raise ValueError
            os.chmod(directory, 0o700)
    except (OSError, ValueError, IndexError, StopIteration, subprocess.SubprocessError):
        raise CloneError("private_storage_unavailable") from None
    return directory


def new_task_file():
    path = private_directory() / "task.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
        return os.fdopen(fd, "w", encoding="utf-8")
    except FileExistsError:
        raise CloneError("task_file_exists") from None
    except OSError:
        raise CloneError("private_storage_unavailable") from None


def save_task(file, task_id):
    if not valid_task_id(task_id):
        raise CloneError("invalid_task_id")
    try:
        file.seek(0)
        json.dump({"task_id": task_id}, file)
        file.truncate()
        file.flush()
        os.fsync(file.fileno())
    except OSError:
        raise CloneError("task_save_failed") from None


def read_task():
    path = private_directory() / "task.json"
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "r", encoding="utf-8") as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 1024:
                raise ValueError
            if os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
                raise ValueError
            task_id = json.load(file).get("task_id")
    except FileNotFoundError:
        raise CloneError("task_file_missing") from None
    except (OSError, ValueError, TypeError, AttributeError):
        raise CloneError("invalid_task_file") from None
    if not valid_task_id(task_id):
        raise CloneError("invalid_task_file")
    return task_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--live", action="store_true", help="Create one potentially billable speech task")
    modes.add_argument("--resume", action="store_true", help="Only poll the saved task")
    modes.add_argument("--import-task-id", action="store_true", help="Privately import an existing task ID; no HTTP")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    # HTTP client debug/info logs can otherwise include task IDs in URLs.
    logging.getLogger("httpx").disabled = True
    logging.getLogger("httpcore").disabled = True
    print("model=seed_audio (documented reference-audio route)")
    if args.import_task_id:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                task_id = getpass.getpass("Existing task ID (hidden): ").strip()
            if not valid_task_id(task_id):
                raise CloneError("invalid_task_id")
            with new_task_file() as file:
                save_task(file, task_id)
        except (CloneError, getpass.GetPassWarning, EOFError, KeyboardInterrupt) as exc:
            category = exc.category if isinstance(exc, CloneError) else "import_cancelled"
            print(f"result={category}; HTTP=not_called")
            return 2
        print("result=task_saved; HTTP=not_called")
        return 0
    if args.resume:
        try:
            task_id = read_task()
            resume_configuration()
        except CloneError as exc:
            print(f"configuration={exc.category}; HTTP=not_called; code=none")
            return 2
        print("resume=enabled; no new task will be created")
    else:
        try:
            reference, timeout = configuration(require_verified=False)
            # Validate the exact local MP3 and prepare any trim without a network call.
            reference_data_uri(reference, timeout)
        except CloneError as exc:
            print(f"configuration={exc.category}; HTTP=not_called; code=none")
            return 2
        print("configuration=ready; local MP3 prepared; HTTP=not_called")
        if not args.live:
            print("HTTP=not_called; use --live to submit one potentially billable request")
            return 0
        try:
            task_file = new_task_file()
        except CloneError as exc:
            print(f"configuration={exc.category}; HTTP=not_called; code=none")
            return 2
        print("live=enabled; sending the reference in the request body; one task may be billed")
    directory = ROOT / "temp_audio"
    directory.mkdir(exist_ok=True)
    output = directory / f"tts_probe_{uuid.uuid4().hex}.mp3"
    statuses = []
    last_poll_http = None
    last_task_status = "UNKNOWN"
    wait_started = time.monotonic() if args.resume else None
    wait_finished = None

    def observe(status):
        if not args.resume and not statuses:
            print(f"submission_http={status}")
        statuses.append(status)

    def saved(task_id):
        nonlocal wait_started
        wait_started = time.monotonic()
        save_task(task_file, task_id)

    def poll(status, http_status):
        nonlocal last_poll_http, last_task_status, wait_finished
        last_poll_http = http_status
        if status is not None:
            last_task_status = status
            if status in ("SUCCEEDED", "FAILED", "CANCELLED"):
                wait_finished = time.monotonic()

    def report_wait():
        elapsed = ((wait_finished or time.monotonic()) - wait_started) if wait_started is not None else 0
        print(f"wait_seconds={elapsed:.2f}; last_task_status={last_task_status}; "
              f"last_poll_http={last_poll_http or 'not_received'}")

    try:
        if args.resume:
            duration = resume_clone_speech(task_id, output, observer=observe, poll_observer=poll)
        else:
            with task_file:
                duration = clone_speech("Hello, this is a short voice test.", output,
                                        require_verified=False, observer=observe,
                                        task_observer=saved, poll_observer=poll)
    except CloneError as exc:
        report_wait()
        print(f"result={exc.category}; HTTP={exc.status or (statuses[-1] if statuses else 'not_received')}; code={exc.code or 'none'}")
        return 1
    report_wait()
    print(f"result=valid_mp3; HTTP={statuses[-1]}; code=none; duration_seconds={duration:.2f}")
    print("Saved in temp_audio. Listen and verify voice identity before enabling; valid audio alone is not proof.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
