"""Keep public Git repos in step with private working copies, without leaking anything.

For each repo in the config: copy the listed source files into the repo's local clone
(applying per-file text substitutions), scan every tracked and untracked file for secrets,
and commit + push only if the content really changed. A secret hit blocks the whole repo.

    python mirror_sync.py                 # sync, scan, commit, push
    python mirror_sync.py --dry           # show what would change, touch nothing in git
    python mirror_sync.py --config my.json

No dependencies beyond Python 3.8+ and git on PATH.
"""
import argparse
import json
import os
import re
import subprocess
import sys

# Shapes of things that must never be pushed. Add your own names, ids and hosts under "block" in the config.
DEFAULT_BLOCK = [
    r"0x[a-fA-F0-9]{40}\b",                               # EVM address
    r"\b[0-9]{8,10}:[A-Za-z0-9_-]{30,}",                  # Telegram bot token
    r"\bsk-[A-Za-z0-9_-]{16,}",                           # API keys in the sk- family
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",                      # GitHub tokens
    r"\bAKIA[0-9A-Z]{16}\b",                              # AWS access key id
    r"\bxox[abp]-[A-Za-z0-9-]{10,}",                      # Slack tokens
    r"BEGIN [A-Z ]*PRIVATE KEY",
    r"(?i)\b(api[_-]?key|secret|token|passw(or)?d)\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']",
    r"[A-Za-z0-9._%+-]+@(gmail|outlook|hotmail|yahoo|qq|163|proton(mail)?)\.[a-z.]{2,}",
]
BINARY = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".pdf", ".zip", ".woff", ".woff2", ".ttf")


def git(repo, *args, proxy=None, timeout=120):
    cmd = ["git"] + (["-c", "http.proxy=" + proxy] if proxy else []) + list(args)
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def copy_sources(repo, files):
    """Returns notes about files that were skipped. A file whose substitution no longer matches is left alone:
    pushing it unsubstituted is exactly the leak the substitution exists to prevent."""
    notes = []
    for item in files:
        src = os.path.expandvars(os.path.expanduser(item["from"]))
        if not os.path.isfile(src):
            notes.append("missing source: " + item["from"])
            continue
        text = open(src, encoding="utf-8").read().replace("\r\n", "\n")
        for sub in item.get("replace", []):
            if text.count(sub["find"]) != 1:
                notes.append("%s: expected exactly one match for a substitution, found %d; file skipped"
                             % (item["to"], text.count(sub["find"])))
                break
            text = text.replace(sub["find"], sub["with"])
        else:
            dst = os.path.join(repo, item["to"])
            os.makedirs(os.path.dirname(dst) or repo, exist_ok=True)
            with open(dst, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
    return notes


def scan(repo, patterns):
    """Everything git would add: tracked files plus untracked ones that are not ignored."""
    hits = []
    for name in git(repo, "ls-files", "-co", "--exclude-standard").stdout.splitlines():
        path = os.path.join(repo, name)
        if not name or not os.path.isfile(path) or name.lower().endswith(BINARY):
            continue
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for number, line in enumerate(fh, 1):
                for label, pattern in patterns:
                    found = pattern.search(line)
                    if found:
                        # Print where, not what: the report itself may end up in a log or a chat.
                        # Block-list entries are named by position for the same reason: they are your own names.
                        hits.append("%s:%d (%d chars, rule %s)" % (name, number, len(found.group(0)), label))
                        break
    return hits


def run(config, dry):
    base = os.path.dirname(os.path.abspath(config["_path"]))
    proxy = config.get("proxy") or None
    patterns = [("builtin %s…" % p[:16], re.compile(p)) for p in DEFAULT_BLOCK]
    patterns += [("block[%d]" % i, re.compile(p)) for i, p in enumerate(config.get("block", []))]
    failed = False
    for name, spec in config["repos"].items():
        repo = os.path.join(base, spec.get("path", name))
        if not os.path.isdir(os.path.join(repo, ".git")):
            print("[%s] no local clone at %s, skipped" % (name, repo))
            continue
        branch = spec.get("branch", "main")
        notes = copy_sources(repo, spec.get("files", []))
        hits = scan(repo, patterns)
        # Stage first, then compare: a file that differs only in line endings is not a change.
        git(repo, "add", "-A")
        changed = [l for l in git(repo, "diff", "--cached", "--name-only").stdout.splitlines() if l.strip()]
        if dry or hits:
            git(repo, "reset", "-q")
        for note in notes:
            print("[%s] note: %s" % (name, note))
        if hits:
            failed = True
            print("[%s] BLOCKED, nothing committed or pushed:" % name)
            for hit in hits[:20]:
                print("    " + hit)
            continue
        if not changed:
            ahead = "ahead" in git(repo, "status", "-sb").stdout.split("\n")[0]
            if ahead and not dry:
                pushed = push(repo, branch, proxy)
                print("[%s] no new changes; pushed earlier commits: %s" % (name, "ok" if pushed else "FAILED"))
                failed = failed or not pushed
            else:
                print("[%s] no changes" % name)
            continue
        print("[%s] changed: %s" % (name, ", ".join(changed)))
        if dry:
            continue
        stat = git(repo, "diff", "--cached", "--shortstat").stdout.strip()
        message = spec.get("message", "Sync from working copy: {files}").format(files=", ".join(changed))[:200]
        git(repo, "commit", "-m", message)
        pushed = push(repo, branch, proxy)
        print("[%s] committed (%s); push %s" % (name, stat, "ok" if pushed else "FAILED, commit is kept locally"))
        failed = failed or not pushed
    return 1 if failed else 0


def push(repo, branch, proxy, attempts=4):
    for _ in range(attempts):
        if git(repo, "push", "origin", branch, proxy=proxy).returncode == 0:
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "mirror.json"))
    parser.add_argument("--dry", action="store_true", help="report only; leaves the git index as it was")
    args = parser.parse_args()
    try:
        with open(args.config, encoding="utf-8") as fh:
            config = json.load(fh)
    except OSError:
        sys.exit("config not found: %s (copy mirror.example.json to mirror.json)" % args.config)
    config["_path"] = args.config
    sys.exit(run(config, args.dry))


if __name__ == "__main__":
    main()
