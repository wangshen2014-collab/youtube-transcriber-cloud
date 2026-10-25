# Cloud task instructions

This repository is a simple uploaded-audio transcription product. Desktop downloader
source lives in desktop/. Do not modify the user's installed Windows app during a cloud task.

## Everyday workflow (default): Codex computes, GitHub stores results

- When uploaded audio is supplied and the user says "全部转录" / "转录", run
  `.venv/bin/python batch_transcribe.py "ATTACHMENT_PATH_1" "ATTACHMENT_PATH_2"`.
  Enumerate ONLY this request's audio attachments. Do not sweep the whole workspace.
  Default to full audio, auto language; preserve multilingual medium, CPU int8,
  6 threads, beam 3, condition_on_previous_text true, VAD false, task=transcribe.
- Run inference INSIDE the current Codex Cloud task. Never dispatch Actions unless
  the user explicitly requests GitHub Actions. Do not run cloud_job.py by default.
  This does not authorize using the user's Windows executor for cloud computation.
- Install with `bash scripts/setup.sh` only if needed; reuse a working environment.
  Use a long-lived terminal command with bounded tool yields. Do not abandon the
  batch as an unattended daemon or promise it survives task termination.
- The script processes files sequentially, writes local TXT/SRT/JSON/ZIP, and saves
  each success to the dedicated public `transcripts` branch immediately. Return
  the printed result index/file links; never print entire transcripts or binary data.
  GitHub stores results and periodic text checkpoints, NOT source audio or models.
  This route has NO automatic 60-day expiry (that was an Actions artifact policy).
- Missing GitHub authorization must be resolved BEFORE inference. Reuse authorized
  environment credentials; otherwise ask for secure setup providing Contents read/write
  on this repo. Never request tokens in chat. Repo connection does not guarantee API
  write access, and secret availability differs across environment/task phases.
- Retry the same command after interruption. Local completed outputs are published
  without inference again. Already published content+settings is skipped across tasks.
  Periodic remote checkpoints can restore in a new task ONLY if the same audio bytes
  are supplied again. The latest unsynced segment may need recomputation.
- A failed item does not stop later items. Inspect `state/cloud-batch/batch-status.json`
  and failed worker logs when needed. A cloud path is not a verified download link.
- A Release link is an optional input transport: download the selected audio using
  a normal binary HTTP download into `incoming/`, then use batch_transcribe.py.
  For multiple assets clarify selection unless user asks to process all of them.
  Never Base64-encode audio into model context or re-upload it unnecessarily.
- Do not merge unrelated files. Only merge explicit user-selected parts or files
  whose original time ranges and common video identity can be verified. Keep all
  individual results, apply source offsets to SRT, publish a separately labelled merge.
- Keep responses quiet during normal computation. Notify on useful completion,
  failure or required user input. Never claim success without actual completed files.

## Legacy GitHub Actions route (ONLY when explicitly requested)

- If the user provides a GitHub Release page or audio asset link, submit
  `python3 cloud_job.py --release-url "URL"` (default FULL audio), then return the
  task ID/run link. Do NOT download or re-upload the source inside Codex.
  Do NOT continuously poll unless the user explicitly asks to wait for results.
  For several audio assets, ask which one or use the user's filename/asset URL.
  Manual Release assets must not be deleted by cleanup; only tagged staging assets
  belong to the automatic input cleanup policy.
- The browser Actions form accepts the same page/asset URL in audio_url, with
  optional asset_name/start/end. This path needs no Codex and no YouTube access.
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
   Exception: the local desktop downloader may use a specifically selected browser
   or local cookie file ONLY after explicit human authorization. Never upload those
   credentials to Codex Cloud/GitHub, try other browsers without consent, or bypass
   OS encryption, DRM or missing membership rights. Member transcripts must not be
   published to the public results branch without appropriate user/rights approval.
9. Never commit state/, models/, outputs/, media, OAuth credentials, or API keys to
   the code branch. batch_transcribe.py may publish only allowlisted results and
   text checkpoints to the separate transcripts branch with the user's authorization.
10. Google Drive upload and ChatGPT Library saving are not implemented by this CLI.
    Use a separately authorized supported tool if available and explicitly requested.
    Otherwise return the actual files and explain any artifact access limitations.
11. Do not assume GPU availability, unlimited runtime, faster execution, or zero cost.
12. Preserve the user's working Windows installation; this repository is independent.
