# Experimental CometAPI reference-audio TTS

The default remains `TTS_PROVIDER=fable`. The documented CometAPI
[Runway speech API](https://apidoc.cometapi.com/api/video/runway/official-format/generate-speech-from-text.md)
offers `seed_audio` with `voice: {type: reference-audio, audioUri: ...}`.
Its schema explicitly documents both an HTTPS URL and a Base64 audio Data URI
of at most 5 MiB; [Runway's SDK](https://github.com/runwayml/sdk-python/blob/main/src/runwayml/types/text_to_speech_create_params.py)
documents `data:audio/mp3;base64,...`. Actual CometAPI acceptance and use of
the reference have not been verified by a live test.
It creates a task with `POST /runwayml/v1/text_to_speech`; poll
`GET /runwayml/v1/tasks/{id}` with Bearer authentication and download a
temporary output URL after `SUCCEEDED`. This is reference-conditioned synthesis,
not a documented persistent voice-ID creation API. No voice ID is created.
The documented ElevenLabs models on this route use Runway presets, not reference
audio. CometAPI's [OpenAI-compatible speech API](https://apidoc.cometapi.com/api/audio/create-speech.md)
documents fixed voices and binary MP3 output, not `minimax-speech` or
`voice_config.audio_url`. MiniMax's own
[clone API](https://platform.minimax.io/docs/guides/speech-voice-clone) uses
file upload, `file_id` and a separate `voice_id`; this is not evidence that
CometAPI proxies those endpoints.

Only use a recording with the speaker's consent. Set `TTS_REFERENCE_PATH` to
the local MP3. It is not hosted or published, but the live request sends the
recording to CometAPI and its upstream in the JSON body. Install `ffmpeg`
and `ffprobe` on PATH. The reference is checked and decoded locally. Clips
longer than 25 seconds are re-encoded to the first 20 seconds in a temporary
file; choose a clean speech clip yourself, since this does not assess voice
quality or select the best segment. A clip must be at least one second and its
encoded Data URI must be at most 5 MiB. The original file is not changed.
The former `TTS_REFERENCE_URL` setting is no longer used by this experiment;
there is no automatic URL fallback.
Set `TTS_CLONE_MODEL=seed_audio`, leave
`TTS_CLONE_VOICE_ID` empty, and use the existing `OPENAI_API_KEY` or
`COMET_API_KEY` (the OpenAI key takes precedence).

Run `python scripts/probe_tts_clone.py` for a free offline configuration check.
Run `python scripts/probe_tts_clone.py --live` only when ready to pay for one
short generation. It polls the task and stores the result in ignored
`temp_audio/`. It never prints the reference data, path, key or task ID.
Unknown provider error codes are redacted because error fields may echo secrets.
Successful decoding does not establish voice identity: listen to the result,
compare it to the reference yourself.
No real-world success has been established by the automated tests.

Only after that verification set `TTS_CLONE_VERIFIED=1` and
`TTS_PROVIDER=clone`, then restart the bot. Each clone request sends the
reference again; the provider does not document a reusable voice ID. Redo
verification if the recording, model or provider changes. Clear the flag immediately
when any of them changes. A missing configuration, failed task, timeout or
invalid MP3 triggers fallback without another clone attempt. The task can
still complete and be charged after a timeout. To revert immediately, set
`TTS_PROVIDER=fable` and restart. `TTS_MODEL` retains precedence over
`TTS_FALLBACK_MODEL`; absent either, fallback is `tts-1`, voice `fable`.
Telegram's `sendVoice` supports MP3 as well as OGG/Opus, so the existing MP3
delivery path remains unchanged.
