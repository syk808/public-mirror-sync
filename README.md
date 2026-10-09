# public-mirror-sync

Keep public Git repos in step with private working copies, and refuse to push anything that looks like a secret. One Python file, no dependencies.

让公开仓库跟着自用的源码走，同时拦住任何看起来像密钥或个人信息的内容。只有一个 Python 文件，没有依赖。

## The problem

The code you actually run lives outside the public repo: it has your paths, your proxy, sometimes a token in a config next to it. Publishing means copying files across, undoing the machine-specific lines, and checking you did not copy something you should not have. Done by hand every day, one of those steps eventually gets skipped.

This script does the three steps the same way every time:

1. **Copy** the listed source files into the repo's local clone, applying per-file text substitutions (a hard-coded path becomes an environment variable, for example).
2. **Scan** every file git would add, tracked or not, against secret patterns plus your own block list. One hit blocks the whole repo: nothing is committed, nothing is pushed.
3. **Commit and push only when content really changed.** Files are staged before comparing, so a line-ending difference is not a change. No empty commits.

## Usage

```
copy mirror.example.json mirror.json     # then edit it; mirror.json is git-ignored
python mirror_sync.py --dry              # what would change; git index left as it was
python mirror_sync.py                    # sync, scan, commit, push
```

Run it from a scheduler once a day. Exit code is 1 if any repo was blocked or a push failed, 0 otherwise.

## Config

```json
{
  "proxy": "",
  "block": ["my-real-name", "internal\\.example\\.com"],
  "repos": {
    "my-tool": {
      "path": "my-tool",
      "files": [
        { "from": "~/work/my-tool/tool.py", "to": "tool.py",
          "replace": [ { "find": "…the private line…", "with": "…the public line…" } ] }
      ]
    },
    "edited-in-place": { "files": [] }
  }
}
```

| Key | Meaning |
|---|---|
| `proxy` | HTTP proxy for `git push`. Empty for none. |
| `block` | Extra regular expressions that must never appear: your name, usernames, chat ids, hostnames. |
| `repos.<name>.path` | Local clone, relative to the config file. Defaults to the repo name. |
| `repos.<name>.files` | Files to copy in. Empty means the repo is edited in place and only scanned and pushed. |
| `files[].replace` | Substitutions applied to the copy. Each `find` must match exactly once. |
| `repos.<name>.branch` | Defaults to `main`. |
| `repos.<name>.message` | Commit message; `{files}` expands to the changed paths. |

## Behaviours worth knowing

- **A substitution that stops matching skips the file.** If the private line was edited and `find` no longer matches once, the file is not copied at all, and a note is printed. Copying it unsubstituted would publish the very line the substitution was there to hide.
- **The scan reports where, not what.** A hit prints `file:line` and the length of the match, never the matched text, because the report may itself land in a log or a chat.
- **Untracked files are scanned too.** A stray output file dropped into the clone is caught before `git add -A` picks it up.
- **A blocked repo does not stop the others.**
- **A failed push keeps the commit.** The next run pushes it.

## Built-in patterns

EVM addresses, Telegram bot tokens, `sk-` style API keys, GitHub tokens, AWS access key ids, Slack tokens, private key headers, `key = "…"` style assignments, and personal e-mail addresses at common providers.

These are shapes, not a guarantee. The scan cannot recognise a secret that looks like ordinary text; your own names and ids belong in `block`. The first publish of a repo still deserves a read by a person. After any publish, cloning the public repo fresh and searching its full history is the check that counts.

## License

MIT
