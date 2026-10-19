# Cloud task instructions

This repository is a simple uploaded-audio transcription product. Desktop downloader
source lives in desktop/. Do not modify the user's installed Windows app during a cloud task.

## Everyday workflow (default)

- When the user gives a YouTube URL, use
  `python3 cloud_job.py --url "URL" --wait --download-dir outputs/github`.
  GitHub Actions downloads it; the Codex task only talks to GitHub. Default to full
  video unless the user selects a time range. Do not promise download success.
- A mobile browser can also use the repository's Actions → transcribe.yml →
  Run workflow form: youtube_url plus optional start/end, leave job_tag blank.
- When the user uploads audio and says to transcribe, default to the ENTIRE file.
  Only add --start/--end when the user explicitly requests a range.
- Use `python3 cloud_job.py --audio-file "PATH" --wait --download-dir outputs/github`.
  This submits actual computation to GitHub Actions and returns a 60-day artifact.
  You do NOT need to install Whisper in the Codex environment for this route.
- Run tool calls with yields/polling so the user can keep steering; do not create a
  new submission just because a wait was interrupted. Reuse the printed --job ID.
- If GitHub authorization is unavailable, tell the user to configure GH_TOKEN as
  an environment secret. Never ask them to paste a token into chat. Do not claim
  repo connection automatically supplies API upload/dispatch permission.
- The user has authorized public audio/results in this repository. They are NOT
  committed to Git history. Inputs are temporary Release assets, deleted after
  the run; orphan cleanup targets only tool-tagged transfers older than seven days.
- Results expire after 60 days via native artifact retention. Report actual
  expires_at; do not describe local task files/chat copies as having that same TTL.
- Download the artifact via the helper when authorized. Safely inspect its TXT,
  SRT and JSON, report language and timings, and provide actual run/artifact links.
  Public artifact downloads can still require GitHub authentication.
- Google Drive and ChatGPT Library upload are not part of this product.

## Direct CLI / debugging

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
