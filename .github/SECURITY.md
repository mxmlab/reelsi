# Security policy

## Reporting a vulnerability

**Do not open a public issue for security problems.** Use GitHub's private
reporting instead: the **Security** tab → **Report a vulnerability**.

Please include what the problem is, how to reproduce it, and what an attacker
gains. A proof of concept helps a great deal.

Expect a first response within 7 days. Once a fix ships, you will be credited in
the release notes unless you prefer otherwise.

## Threat model — please read before reporting

Reelsi is a **local, single-user desktop tool**. It is not a multi-tenant service
and was never designed to be one. The following are deliberate design decisions,
not vulnerabilities:

- **The web UI has no authentication.** It binds to `127.0.0.1` and assumes the
  only user on the machine is the operator.
- **The API accepts filesystem paths from the front end** and reads and writes
  media anywhere the user's account can reach. That is the entire purpose of the
  tool — it edits your video files. Two limits do apply: every `/api/*` request is
  rejected unless its `Host` header is localhost (this is what stops a hostile page
  from reaching the API via DNS rebinding), and `/api/media` will never serve
  `ai_config.json`, whatever path you ask it for.
- **Generated ExtendScript (`.jsx`) runs inside After Effects** with whatever
  permissions AE has.
- **API keys live in plaintext** in `ai_config.json`, protected by the operating
  system's file permissions. The file is in `.gitignore` and keys are masked in
  the UI as `•••xxxx`.
- **The breath detector model executes remote code.** The breath detector
  (`core/breath.py`) loads `mispeech/ced-tiny` from Hugging Face with
  `trust_remote_code=True`, meaning Python code from the model repository
  executes upon loading; the revision is not pinned. The model is loaded only by
  the **Breaths** cutting stage and by `tools/train_breath.py`. To run without it,
  use Custom cutting with **Breaths** unticked (⚙ › Cut › Cutting stages) or pass
  `--no-breath` to `core.omni_cut` / `core.gigaam_cut`.

What *is* in scope, and worth reporting:

- Anything that makes the server reachable beyond `127.0.0.1` without the user
  explicitly asking for it.
- Leaking API keys or other secrets into logs, error pages, HTTP responses,
  telemetry, or generated files.
- Remote code execution triggered by a **file** — a crafted XML, project file,
  media file, or LLM response that leads to code execution or writes outside the
  working directory.
- Path traversal reachable from a source that is not the local user: a malicious
  filename inside an imported project, an LLM response, or a downloaded asset.
- A web page in the user's browser being able to drive the local API. The `Host`
  check closes DNS rebinding; a way around it, or a state-changing `POST` reachable
  from a hostile page, is worth reporting.

## If you expose Reelsi to a network

Don't. If you must, put it behind an authenticating reverse proxy on a trusted
network. Binding it to `0.0.0.0` hands anyone who can reach the port full read
and write access to your filesystem and your API keys.
