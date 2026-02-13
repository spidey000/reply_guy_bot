# Archive Folder

This directory stores local runtime artifacts (logs, screenshots, cookies, browser states, etc.) that should never be committed to Git or packaged into Docker images. Anything you drop here stays outside of source control thanks to `.archive/.gitignore`.

Suggested usage:

- Move ad-hoc debugging assets (PNG screenshots, Playwright dumps, etc.) from the repo root into this folder.
- Keep local databases (`reply_bot.db`), downloaded cookies (`cookies.json`), and audit logs inside `.archive/` so they remain on your workstation only.
- If you need to share a sanitized example, place it under a nested subfolder and manually remove sensitive content before committing it elsewhere.

Only this README and the `.gitkeep` file are tracked; all other contents are ignored automatically.
