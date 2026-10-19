# Cloud task instructions

This repository runs YouTube audio transcription. Use the CLI; do not build a GUI.

1. If necessary run `bash scripts/setup.sh` (Linux, Python 3.11 recommended).
2. Run `.venv/bin/python transcribe_cli.py --url "URL"` with the user's requested
   language and time range. Defaults: multilingual medium, CPU int8, 6 threads,
   beam 3, condition_on_previous_text true, VAD false. Do not silently change them.
3. Actually execute transcription; downloading audio alone is not a successful test.
4. Inspect generated TXT and SRT. Report detected language, stage timings,
   approximate RAM, configuration, output paths, and any download/compute failures.
5. Exit 75 means paused, not failed. Re-run the same options to resume. For
   `--pause-file`, remove the stop file first. SIGTERM/SIGINT is cooperative;
   do not promise instantaneous interruption or shutdown-safe saving after SIGKILL.
6. Use one invocation per video; multiple URLs in one user request can be processed
   serially. Do not start simultaneous invocations against the same state directory.
7. The audio cache and model directory are task-local unless external persistence
   is configured. Never promise they are shared between new Cloud tasks.
8. Do not read browser cookies, request user passwords, or bypass YouTube restrictions.
   If cloud downloading is blocked, report it and suggest a user-provided audio file
   for a future diagnostic; do not claim transcription succeeded.
9. Never commit state/, models/, outputs/, media, OAuth credentials, or API keys.
10. Google Drive upload and ChatGPT Library saving are not implemented by this CLI.
    Use a separately authorized supported tool if available and explicitly requested.
    Otherwise return the actual files and explain any artifact access limitations.
11. Do not assume GPU availability, unlimited runtime, faster execution, or zero cost.
12. Preserve the user's working Windows installation; this repository is independent.
