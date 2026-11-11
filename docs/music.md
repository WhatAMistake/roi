# Music library

The default private audio directory is `/opt/roi-media/music` (not served over HTTP).
No audio is included in the repository. Set `ROI_MUSIC_DIR` to change it. Example:

```
/opt/roi-media/music/
  death/first.mp3
  freedom/first.mp3
  isolation/first.mp3
  meaninglessness/first.mp3
```

Fill `docs/music_manifest.json` (or set `ROI_MUSIC_MANIFEST` to a protected external
JSON file). Each of its 28 entries has a permanent `id`, `category`, relative `file`,
`title`, `artist`, an optional internal `description`, and optional `caption_ru`
and `caption_en` strings. Example:

```json
{"id":"isolation-01","category":"isolation","file":"isolation/first.mp3","title":"Title","artist":"Artist","description":"Quiet, distant atmosphere","caption_ru":"Тихая, отстранённая атмосфера.","caption_en":"A quiet, distant atmosphere."}
```

`description` is an internal description and is never appended to the audio caption.
Write each localized caption as a short, natural, slightly evocative text in Roy's
voice (one or two sentences), without therapeutic advice or excessive poetry.
Manual and automatic sends use the user's saved language preference (or the bot's
default language). Manual sends prepend a localized introduction naming the chosen
theme; automatic sends contain only the corresponding song caption. Missing, blank,
or non-string captions use a short neutral phrase in that language; the other
language and `description` are not used as substitutes. No LLM request is made for
a caption. Keep the full manual caption, including its introduction, within
Telegram's 1024-character limit.

Keep IDs permanently attached to the same recordings, even when renaming files;
never recycle IDs. Categories are `death`, `freedom`, `isolation`, `meaninglessness`.
Empty entries and missing, unsafe, non-MP3 or oversized files are skipped. The
Bot API upload limit is 50 MiB. Only an MP3 header is checked locally; Telegram
validates the complete file. Do not expose the directory through a web server.

Use an existing valid `USER_PREFS_ENCRYPTION_KEY`; music is unavailable without
it. `ROI_MUSIC_DB` defaults to `data/music.sqlite`. SQLite tables are created on
first use; no existing data is migrated or reset. Back up this database together
with the encryption key and the manifest. Restrict filesystem permissions to the
bot account. User IDs are HMAC-pseudonymized, but delivery history is sensitive
metadata and the database is not encrypted. Changing/loss of the key changes user
identities; do not rotate it without a deliberate migration. `/reset` only starts
a new dialogue session, not a new music history.

Set `ROI_MUSIC_INITIATIVE=false` to disable automatic sends without disabling
`/music`; `ROI_MUSIC_COOLDOWN_DAYS` defaults to 7. At most one automatic send per
session is allowed. Classification is conservative, and current privacy consent
is required; there is no separate confirmation before each automatic send.

Deployment: upload the MP3s to `/opt/roi-media/music`, fill the manifest, set the
environment variables, restrict permissions and restart the bot. No audio needs
to exist for the bot to start. Check the catalogue without sending anything:

```sh
cd /opt/roi
ROI_MUSIC_DIR=/opt/roi-media/music python src/music.py --check
# Equivalent package entrypoint:
ROI_MUSIC_DIR=/opt/roi-media/music python -m app.features.music --check
python -m pytest tests/test_music.py
```

The implementation lives in `app/features/music/`: catalogue validation,
SQLite reservations/history, and Telegram interaction are separate components.
`src/music.py` remains a compatibility import and catalogue-check entrypoint.
Default manifest and database paths still resolve from the repository root;
configured relative paths continue to resolve from the working directory.

Afterwards use `/music` in an already-consenting private Telegram chat and choose
one of the four category buttons. This sends a real track and permanently consumes
it for that user.

Telegram does not support idempotency keys or an atomic transaction with SQLite.
If Telegram rejects a send explicitly, its reservation is released. If the process
dies or the network fails after a request begins, the outcome is unknowable: the
reservation is retained permanently to avoid duplicate delivery. An administrator
must investigate such rows (`status='reserved'`) before any manual intervention;
do not blindly delete them. A confirmed send followed by a database failure has
the same fail-closed behavior. There is no automatic history reset or retry.
