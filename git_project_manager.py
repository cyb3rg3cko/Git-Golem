#!/usr/bin/env python3
"""
Git Project Manager
-------------------
A small desktop app for maintaining your GitHub projects.

* Projects list on the left, command buttons on the right
* Import existing folders/repos, create new projects (+ GitHub repo), clone
* One-time GitHub login so git never asks for username/password again
* Buttons run git commands in the background and stream their output
* Per-project settings: choose which buttons to show and in what order
* Full log of every command that has been run

Standard library only (Tkinter). Requires Git to be installed.
Run with:  python git_project_manager.py
"""

import datetime
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

APP_NAME = "Git Project Manager"
APP_DIR = Path.home() / ".git_project_manager"
CONFIG_FILE = APP_DIR / "config.json"
LOG_FILE = APP_DIR / "command_log.jsonl"
MAX_LOGGED_OUTPUT = 20000
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # hide console flashes on Windows
GITHUB_RE = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$")
TOKEN_URL = "https://github.com/settings/tokens/new?scopes=repo,workflow&description=Git%20Project%20Manager"

GITIGNORES = {
    "Python": "__pycache__/\n*.py[cod]\n.venv/\nvenv/\nbuild/\ndist/\n*.egg-info/\n.env\n.DS_Store\n",
    "Node": "node_modules/\ndist/\nbuild/\n.env\nnpm-debug.log*\n.DS_Store\n",
    "General": ".DS_Store\nThumbs.db\n.idea/\n.vscode/\n.env\n",
    "None": "",
}


# --------------------------------------------------------------------------- helpers

def git_env():
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"   # never hang waiting for a username/password
    env["GIT_MERGE_AUTOEDIT"] = "no"   # merges don't open an editor
    env["GIT_EDITOR"] = "true"         # nothing can wait on an editor in the background
    return env


def git_out(args, cwd=None, stdin=None, timeout=20):
    """Run a git command synchronously. Returns (exit_code, text)."""
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd or str(Path.home()), input=stdin,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=git_env(), timeout=timeout, creationflags=NO_WINDOW,
        )
        out = r.stdout if r.returncode == 0 else (r.stdout + r.stderr)
        return r.returncode, out.strip()
    except Exception as e:  # git missing, bad cwd, timeout…
        return 1, str(e)


def split_args(text):
    if os.name == "nt":  # keep Windows backslashes intact
        toks = shlex.split(text, posix=False)
        return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in toks]
    return shlex.split(text)


def quote_cmd(argv):
    return " ".join(shlex.quote(a) for a in argv)


def slugify(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-")


def parse_github(url):
    m = GITHUB_RE.search(url or "")
    return (m.group(1), m.group(2)) if m else None


def get_github_token():
    """Ask git's credential helper for the stored GitHub token."""
    code, out = git_out(["credential", "fill"], stdin="protocol=https\nhost=github.com\n\n")
    if code == 0:
        for line in out.splitlines():
            if line.startswith("password="):
                return line[len("password="):]
    raise RuntimeError("No GitHub token is stored yet. Open Git ▸ GitHub Login… and save a personal access token.")


def github_api(method, path, token, payload=None):
    req = urllib.request.Request(
        "https://api.github.com" + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "git-project-manager",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        try:
            info = json.loads(e.read())
            msg = info.get("message", "")
            errs = "; ".join(x.get("message", str(x)) if isinstance(x, dict) else str(x)
                             for x in info.get("errors", []))
            msg = f"{msg} ({errs})" if errs else msg
        except Exception:
            msg = e.reason
        raise RuntimeError(f"GitHub API error {e.code}: {msg}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach GitHub: {e.reason}")


def resource_path(name):
    """Path to a file shipped with the app — works from source and from a PyInstaller binary."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def open_path(path):
    if os.name == "nt":
        os.startfile(path)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def credential_helper_options():
    if os.name == "nt":
        return ["manager", "wincred", "store"]
    if sys.platform == "darwin":
        return ["osxkeychain", "store", "cache --timeout=86400"]
    return ["store", "cache --timeout=86400"]


def S(argv=None, cwd=None, stdin=None, label=None, func=None, project=None):
    """One step of work: either a command (argv) or a Python function (func).
    `project` overrides the project the whole run belongs to (used by Check all status)."""
    return {"argv": argv, "cwd": cwd, "stdin": stdin, "label": label, "func": func, "project": project}


def F(key, label, default="", kind="entry", **kw):
    """A form field for FormDialog. kind: entry | password | text | check | combo | folder."""
    return dict(key=key, label=label, default=default, kind=kind, **kw)


def local_branches(path):
    code, out = git_out(["branch", "--format=%(refname:short)"], path)
    return out.splitlines() if code == 0 and out else []


def current_branch(path):
    code, out = git_out(["branch", "--show-current"], path)
    return out if code == 0 else ""


def origin_url(path):
    code, out = git_out(["remote", "get-url", "origin"], path)
    return out if code == 0 else ""


# --------------------------------------------------------------------------- command buttons
# Each builder gets (app, project) and returns a list of steps, (steps, on_done), or None to cancel.

def b_status(app, p):
    return [S(["git", "status"])]


def b_init(app, p):
    return [S(["git", "init", "-b", "main"])]


def forced(app, title):
    return f"{title}  (FORCE)" if app.forcing else title


def b_add(app, p):
    r = app.ask(forced(app, "git add"), [F("paths", "Files to add", ".",
                              hint="Space-separated paths. '.' adds everything in the folder.")])
    if not r:
        return None
    return [S(["git", "add", *(["-f"] if app.forcing else []), "--", *(split_args(r["paths"]) or ["."])])]


def b_commit(app, p):
    r = app.ask("git commit", [
        F("message", "Commit message", kind="text"),
        F("add_all", "Stage all changes first (git add -A)", False, "check"),
        F("amend", "Amend the previous commit", False, "check"),
    ])
    if not r:
        return None
    if not r["message"] and not r["amend"]:
        messagebox.showerror(APP_NAME, "A commit message is required.", parent=app)
        return None
    steps = [S(["git", "add", "-A"])] if r["add_all"] else []
    argv = ["git", "commit"]
    if r["amend"]:
        argv.append("--amend")
        if not r["message"]:
            argv.append("--no-edit")
    if r["message"]:
        argv += ["-m", r["message"]]
    return steps + [S(argv)]


def b_push(app, p):
    if app.forcing:
        return [S(["git", "push", "--force-with-lease", "-u", "origin", "HEAD"])]
    return [S(["git", "push", "-u", "origin", "HEAD"])]


def b_pull(app, p):
    if app.forcing:
        return [S(["git", "fetch", "origin"]), S(["git", "reset", "--hard", "@{u}"])]
    code, _ = git_out(["config", "pull.rebase"], p["path"])
    return [S(["git", "pull"] if code == 0 else ["git", "pull", "--no-rebase"])]


def b_fetch(app, p):
    return [S(["git", "fetch", "--all", "--prune"])]


def b_branch(app, p):
    r = app.ask(forced(app, "git branch"), [
        F("name", "New branch name", hint="Leave blank to just list all branches."),
        F("switch", "Switch to the new branch", True, "check"),
    ])
    if not r:
        return None
    if not r["name"]:
        return [S(["git", "branch", "-a", "-vv"])]
    if app.forcing:
        return [S(["git", "switch", "-C", r["name"]] if r["switch"] else ["git", "branch", "-f", r["name"]])]
    return [S(["git", "switch", "-c", r["name"]] if r["switch"] else ["git", "branch", r["name"]])]


def b_switch(app, p):
    branches = local_branches(p["path"])
    r = app.ask(forced(app, "git switch"), [F("name", "Branch", current_branch(p["path"]), "combo", values=branches)])
    if not r or not r["name"]:
        return None
    return [S(["git", "switch", *(["--discard-changes"] if app.forcing else []), r["name"]])]


def b_merge(app, p):
    cur = current_branch(p["path"])
    others = [b for b in local_branches(p["path"]) if b != cur]
    r = app.ask("git merge", [F("name", f"Merge into '{cur}' from", others[0] if others else "", "combo", values=others)])
    if not r or not r["name"]:
        return None
    return [S(["git", "merge", r["name"]])]


def b_remote(app, p):
    cur = origin_url(p["path"])
    r = app.ask("git remote add origin", [F("url", "Remote URL", cur,
               hint="e.g. https://github.com/you/repo.git — leave blank to just list remotes.")])
    if not r:
        return None
    url = r["url"]
    if not url or url == cur:
        return [S(["git", "remote", "-v"])]
    if cur:
        if not messagebox.askyesno(APP_NAME, f"'origin' already points to:\n{cur}\n\nReplace it with:\n{url}?", parent=app):
            return None
        return [S(["git", "remote", "set-url", "origin", url]), S(["git", "remote", "-v"])]
    return [S(["git", "remote", "add", "origin", url]), S(["git", "remote", "-v"])]


def b_clone(app, p):
    default_parent = str(Path(p["path"]).parent) if p else app.config_data.get("default_parent", str(Path.home()))
    r = app.ask("git clone", [
        F("url", "Repository URL", hint="e.g. https://github.com/owner/repo.git"),
        F("parent", "Clone into folder", default_parent, "folder"),
        F("name", "Folder name", hint="Optional — defaults to the repository name."),
    ])
    if not r or not r["url"]:
        return None
    name = r["name"] or re.sub(r"\.git$", "", re.split(r"[/:]", r["url"].rstrip("/"))[-1])
    dest = Path(r["parent"]).expanduser() / name
    if dest.exists() and any(dest.iterdir()):
        messagebox.showerror(APP_NAME, f"{dest} already exists and is not empty.", parent=app)
        return None
    Path(r["parent"]).expanduser().mkdir(parents=True, exist_ok=True)

    def done(ok):
        if ok:
            app.add_project(dest)
    return [S(["git", "clone", r["url"], str(dest)], cwd=str(Path(r["parent"]).expanduser()))], done


def b_tag(app, p):
    r = app.ask(forced(app, "git tag"), [
        F("name", "Tag name", hint="e.g. v1.2.0 — leave blank to list existing tags."),
        F("message", "Message", hint="Optional annotation (defaults to the tag name)."),
        F("push", "Push the tag to GitHub", True, "check"),
    ])
    if not r:
        return None
    if not r["name"]:
        return [S(["git", "tag", "-n", "--sort=-creatordate"])]
    f = ["-f"] if app.forcing else []
    steps = [S(["git", "tag", "-a", *f, r["name"], "-m", r["message"] or r["name"]])]
    if r["push"]:
        steps.append(S(["git", "push", *(["--force"] if app.forcing else []), "origin", f"refs/tags/{r['name']}"]))
    return steps


def b_release(app, p):
    gh = parse_github(origin_url(p["path"]))
    if not gh:
        messagebox.showerror(APP_NAME, "This project has no GitHub remote named 'origin'.\n"
                             "Use 'Remote add origin' first.", parent=app)
        return None
    code, latest = git_out(["describe", "--tags", "--abbrev=0"], p["path"])
    r = app.ask("Create GitHub release", [
        F("tag", "Tag", latest if code == 0 and latest else "v1.0.0",
          hint="An existing tag is pushed first; a new tag is created on GitHub from the current branch."),
        F("title", "Title", hint="Defaults to the tag."),
        F("notes", "Release notes", kind="text"),
        F("generate", "Auto-generate notes from commits", True, "check"),
        F("draft", "Save as draft", False, "check"),
        F("prerelease", "Mark as pre-release", False, "check"),
    ])
    if not r or not r["tag"]:
        return None
    owner, repo = gh
    tag = r["tag"]
    tag_exists = git_out(["rev-parse", "-q", "--verify", f"refs/tags/{tag}"], p["path"])[0] == 0
    payload = {"tag_name": tag, "name": r["title"] or tag, "body": r["notes"], "draft": r["draft"],
               "prerelease": r["prerelease"], "generate_release_notes": r["generate"]}
    branch = current_branch(p["path"])
    if not tag_exists and branch:
        payload["target_commitish"] = branch
    steps = [S(["git", "push", "origin", f"refs/tags/{tag}"])] if tag_exists else []

    def create():
        data = github_api("POST", f"/repos/{owner}/{repo}/releases", get_github_token(), payload)
        return f"Release created: {data.get('html_url')}"
    steps.append(S(func=create, label=f"GitHub API: create release {tag} on {owner}/{repo}"))
    return steps


def b_log(app, p):
    return [S(["git", "log", "--oneline", "--graph", "--decorate", "-n", "40"])]


def b_diff(app, p):
    return [S(["git", "diff", "--stat"]), S(["git", "diff"])]


def b_stash(app, p):
    r = app.ask("git stash", [
        F("action", "Action", "push", "combo", values=["push", "pop", "apply", "list", "drop"]),
        F("message", "Message (push only)"),
    ])
    if not r:
        return None
    argv = ["git", "stash", r["action"] or "push"]
    if argv[2] == "push" and r["message"]:
        argv += ["-m", r["message"]]
    return [S(argv)]


def b_custom(app, p):
    r = app.ask("Run any command", [F("cmd", "Command", "git ",
               hint="Any git (or gh) command, e.g.  git log -p -2   or   git branch -d old-feature.\n"
                    "Commands that need an editor or interactive input are not supported.")])
    if not r:
        return None
    argv = split_args(r["cmd"])
    if not argv or argv == ["git"]:
        return None
    if argv[0] not in ("git", "gh"):
        argv = ["git", *argv]
    return [S(argv)]


def b_open_web(app, p):
    gh = parse_github(origin_url(p["path"]))
    if not gh:
        messagebox.showerror(APP_NAME, "This project has no GitHub remote named 'origin'.", parent=app)
    else:
        webbrowser.open(f"https://github.com/{gh[0]}/{gh[1]}")
    return None


def status_summary(projects):
    """One line per project: uncommitted changes and how far ahead/behind GitHub it is."""
    lines = []
    for p in projects:
        path = p["path"]
        _, changes = git_out(["status", "--porcelain"], path)
        parts = []
        n = len(changes.splitlines()) if changes else 0
        if n:
            parts.append(f"{n} uncommitted change{'s' if n != 1 else ''}")
        code, ab = git_out(["rev-list", "--left-right", "--count", "HEAD...@{u}"], path)
        if code == 0 and len(ab.split()) == 2:
            ahead, behind = (int(x) for x in ab.split())
            if ahead:
                parts.append(f"{ahead} commit{'s' if ahead != 1 else ''} to push")
            if behind:
                parts.append(f"{behind} commit{'s' if behind != 1 else ''} to pull")
        else:
            parts.append("no upstream branch")
        lines.append(f"{'✔' if not parts else '•'} {p['name']}: {', '.join(parts) or 'clean and in sync'}")
    return "\n".join(lines)


# force = (short hover text, warning shown in the confirmation popup)
COMMANDS = {
    "status":   dict(label="Status",            desc="git status — show what changed", build=b_status),
    "add":      dict(label="Add",               desc="git add — stage files for the next commit", build=b_add,
                     force=("git add -f — also add files your .gitignore excludes",
                            "Force-add stages files even when .gitignore excludes them — build output, "
                            "large files, or secrets such as .env files. Make sure you really want "
                            "them in the repository.")),
    "commit":   dict(label="Commit",            desc="git commit — save staged changes", build=b_commit),
    "push":     dict(label="Push",              desc="git push — upload commits to GitHub", build=b_push,
                     force=("git push --force-with-lease — overwrite the branch on GitHub",
                            "Force-push replaces the branch on GitHub with your local version. Commits on "
                            "GitHub that you don't have locally will be removed.\n\n"
                            "(--force-with-lease still refuses if someone else pushed since your last fetch.)")),
    "pull":     dict(label="Pull",              desc="git pull — download and merge changes", build=b_pull,
                     force=("Discard local work and match GitHub exactly (fetch + reset --hard)",
                            "Force-pull fetches from GitHub and runs git reset --hard to match it.\n\n"
                            "ALL uncommitted changes to tracked files and ANY local commits that are not "
                            "on GitHub will be permanently lost.")),
    "fetch":    dict(label="Fetch",             desc="git fetch --all --prune", build=b_fetch),
    "branch":   dict(label="Branch",            desc="git branch — create or list branches", build=b_branch,
                     force=("git switch -C / branch -f — recreate a branch that already exists",
                            "If a branch with this name already exists, it is reset to the current commit. "
                            "Commits that were only on the old branch may be lost.")),
    "switch":   dict(label="Switch branch",     desc="git switch — change the current branch", build=b_switch,
                     force=("git switch --discard-changes — switch and throw away local edits",
                            "Switching with force throws away ALL uncommitted changes to tracked files "
                            "in this project.")),
    "merge":    dict(label="Merge",             desc="git merge — merge another branch in", build=b_merge),
    "tag":      dict(label="Tag",               desc="git tag — create (and push) a version tag", build=b_tag,
                     force=("git tag -f — move an existing tag (and force-push it)",
                            "Force-tagging moves an existing tag to the current commit and force-pushes it "
                            "to GitHub. Anything that uses the old tag, such as a release, will now point "
                            "to a different commit.")),
    "release":  dict(label="Release",           desc="Create a GitHub release from a tag", build=b_release),
    "log":      dict(label="History",           desc="git log — recent commits", build=b_log),
    "diff":     dict(label="Diff",              desc="git diff — unstaged changes", build=b_diff),
    "stash":    dict(label="Stash",             desc="git stash — park changes temporarily", build=b_stash),
    "remote":   dict(label="Remote add origin", desc="git remote add origin <url>", build=b_remote),
    "init":     dict(label="Init",              desc="git init — make this folder a repository", build=b_init, needs_repo=False),
    "clone":    dict(label="Clone",             desc="git clone — copy a repository from a URL", build=b_clone,
                     needs_repo=False, needs_project=False),
    "open_web": dict(label="Open on GitHub",    desc="Open the repository page in your browser", build=b_open_web),
    "custom":   dict(label="Custom command…",   desc="Run any git command", build=b_custom, needs_repo=False),
}
COMMAND_ORDER = list(COMMANDS)
DEFAULT_VISIBLE = {"status", "add", "commit", "push", "pull", "branch", "switch",
                   "tag", "release", "log", "remote", "custom"}


def normalize_buttons(buttons):
    """Return a full [[key, visible], …] list in the saved order, adding any new commands."""
    out, seen = [], set()
    for item in buttons or []:
        try:
            key, vis = item[0], bool(item[1])
        except (TypeError, IndexError):
            continue
        if key in COMMANDS and key not in seen:
            out.append([key, vis])
            seen.add(key)
    for key in COMMAND_ORDER:
        if key not in seen:
            out.append([key, key in DEFAULT_VISIBLE])
    return out


# --------------------------------------------------------------------------- dialogs

class FormDialog(tk.Toplevel):
    """Small modal form. .result is a dict of values, or None if cancelled."""

    def __init__(self, parent, title, fields, intro=None, ok_text="OK", extra_button=None):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.result = None
        self.vars, self.texts = {}, {}
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        row, first = 0, None
        if intro:
            ttk.Label(body, text=intro, wraplength=460, justify="left").grid(
                row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
            row = 1
        for f in fields:
            kind, key = f["kind"], f["key"]
            if kind == "check":
                v = tk.BooleanVar(value=bool(f["default"]))
                ttk.Checkbutton(body, text=f["label"], variable=v).grid(row=row, column=1, columnspan=2, sticky="w", pady=2)
                self.vars[key] = v
            else:
                ttk.Label(body, text=f["label"]).grid(row=row, column=0, sticky="nw", padx=(0, 10), pady=4)
                if kind == "text":
                    w = tk.Text(body, width=52, height=6, wrap="word", undo=True)
                    w.insert("1.0", f["default"])
                    w.bind("<Control-Return>", self._ok)
                    w.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
                    self.texts[key] = w
                else:
                    v = tk.StringVar(value=f["default"])
                    if kind == "combo":
                        w = ttk.Combobox(body, textvariable=v, values=f.get("values", []), width=50)
                    else:
                        w = ttk.Entry(body, textvariable=v, width=52, show="•" if kind == "password" else "")
                    w.grid(row=row, column=1, columnspan=1 if kind == "folder" else 2, sticky="ew", pady=4)
                    w.bind("<Return>", self._ok)
                    if kind == "folder":
                        ttk.Button(body, text="Browse…", command=lambda v=v: self._browse(v)).grid(
                            row=row, column=2, padx=(6, 0))
                    self.vars[key] = v
                if first is None:
                    first = w
            if f.get("hint"):
                row += 1
                ttk.Label(body, text=f["hint"], foreground="gray", wraplength=440, justify="left").grid(
                    row=row, column=1, columnspan=2, sticky="w")
            row += 1
        body.columnconfigure(1, weight=1)

        bar = ttk.Frame(self, padding=(14, 0, 14, 14))
        bar.pack(fill="x")
        if extra_button:
            ttk.Button(bar, text=extra_button[0], command=extra_button[1]).pack(side="left")
        ttk.Button(bar, text=ok_text, command=self._ok).pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=6)
        self.bind("<Escape>", lambda e: self.destroy())

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        if first is not None:
            first.focus_set()
        self.wait_window(self)

    def _browse(self, var):
        d = filedialog.askdirectory(parent=self, initialdir=var.get() or str(Path.home()))
        if d:
            var.set(d)

    def _ok(self, *_):
        res = {k: (v.get() if isinstance(v, tk.BooleanVar) else v.get().strip()) for k, v in self.vars.items()}
        res.update({k: t.get("1.0", "end-1c").strip() for k, t in self.texts.items()})
        self.result = res
        self.destroy()
        return "break"


class SettingsDialog(tk.Toplevel):
    """Per-project settings: which buttons to show, in what order, plus the command log."""

    def __init__(self, app, project):
        super().__init__(app)
        self.app, self.project = app, project
        self.items = [list(x) for x in normalize_buttons(project["buttons"])]
        self.title(f"Settings — {project['name']}")
        self.transient(app)
        self.geometry("520x560")
        self.minsize(460, 460)

        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Project name").grid(row=0, column=0, sticky="w")
        self.name_var = tk.StringVar(value=project["name"])
        ttk.Entry(outer, textvariable=self.name_var).grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(outer, text=project["path"], foreground="gray").grid(row=1, column=1, columnspan=2, sticky="w")

        ttk.Label(outer, text="Command buttons — double-click to show/hide, use the arrows to reorder:").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(14, 4))
        self.lb = tk.Listbox(outer, activestyle="none", exportselection=False, height=16)
        self.lb.grid(row=3, column=0, columnspan=2, sticky="nsew")
        self.lb.bind("<Double-Button-1>", lambda e: self.toggle())
        self.lb.bind("<space>", lambda e: self.toggle())
        side = ttk.Frame(outer)
        side.grid(row=3, column=2, sticky="n", padx=(8, 0))
        for text, cmd in [("Show / hide", self.toggle), ("▲ Move up", lambda: self.move(-1)),
                          ("▼ Move down", lambda: self.move(1)), ("Show all", self.show_all),
                          ("Reset to default", self.reset)]:
            ttk.Button(side, text=text, command=cmd, width=16).pack(fill="x", pady=2)

        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(3, weight=1)

        bar = ttk.Frame(self, padding=(14, 0, 14, 14))
        bar.pack(fill="x")
        ttk.Button(bar, text="📜 View command log", command=lambda: LogViewer(app, project["name"])).pack(side="left")
        ttk.Button(bar, text="Remove project", command=self.remove).pack(side="left", padx=6)
        ttk.Button(bar, text="Save", command=self.save).pack(side="right")
        ttk.Button(bar, text="Cancel", command=self.destroy).pack(side="right", padx=6)
        self.bind("<Escape>", lambda e: self.destroy())
        self.refresh()

    def refresh(self, select=None):
        self.lb.delete(0, "end")
        for key, vis in self.items:
            self.lb.insert("end", f"{'☑' if vis else '☐'}   {COMMANDS[key]['label']}")
            self.lb.itemconfig("end", foreground="" if vis else "gray")
        if select is not None:
            self.lb.selection_set(select)
            self.lb.see(select)

    def _sel(self):
        s = self.lb.curselection()
        return s[0] if s else None

    def toggle(self):
        i = self._sel()
        if i is not None:
            self.items[i][1] = not self.items[i][1]
            self.refresh(i)

    def move(self, d):
        i = self._sel()
        if i is None or not 0 <= i + d < len(self.items):
            return
        self.items[i], self.items[i + d] = self.items[i + d], self.items[i]
        self.refresh(i + d)

    def show_all(self):
        for it in self.items:
            it[1] = True
        self.refresh()

    def reset(self):
        self.items = normalize_buttons(None)
        self.refresh()

    def save(self):
        self.project["buttons"] = self.items
        self.project["name"] = self.name_var.get().strip() or self.project["name"]
        self.app.save_config()
        self.app.refresh_project_list()
        self.app.select_project(self.project)
        self.destroy()

    def remove(self):
        if messagebox.askyesno(APP_NAME, f"Remove '{self.project['name']}' from the list?\n\n"
                               "The folder and repository on disk are not touched.", parent=self):
            self.app.remove_project(self.project)
            self.destroy()


class LogViewer(tk.Toplevel):
    """Shows every command that has been run, newest first."""

    def __init__(self, app, project_name=None):
        super().__init__(app)
        self.app = app
        self.title("Command log")
        self.geometry("940x620")

        top = ttk.Frame(self, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Show:").pack(side="left")
        names = ["All projects", "(global)"] + [p["name"] for p in app.config_data["projects"]]
        self.filter_var = tk.StringVar(value=project_name or "All projects")
        cb = ttk.Combobox(top, textvariable=self.filter_var, values=names, state="readonly", width=28)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.load())
        self.failed_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Failures only", variable=self.failed_only, command=self.load).pack(side="left", padx=6)
        ttk.Button(top, text="Clear log…", command=self.clear).pack(side="right")
        ttk.Button(top, text="Open log folder", command=lambda: open_path(str(APP_DIR))).pack(side="right", padx=6)
        ttk.Button(top, text="Refresh", command=self.load).pack(side="right")

        pw = ttk.PanedWindow(self, orient="vertical")
        pw.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        tf = ttk.Frame(pw)
        cols = ("time", "project", "command", "exit", "dur")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", selectmode="browse")
        for c, text, w, st in [("time", "Time", 150, False), ("project", "Project", 140, False),
                               ("command", "Command", 460, True), ("exit", "Exit", 50, False),
                               ("dur", "Seconds", 70, False)]:
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, stretch=st, anchor="w")
        self.tree.tag_configure("fail", foreground="#c0392b")
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.show)
        pw.add(tf, weight=3)

        df = ttk.Frame(pw)
        self.detail = tk.Text(df, wrap="none", font=tkfont.nametofont("TkFixedFont"), height=10)
        dsb = ttk.Scrollbar(df, orient="vertical", command=self.detail.yview)
        self.detail.configure(yscrollcommand=dsb.set)
        self.detail.pack(side="left", fill="both", expand=True)
        dsb.pack(side="right", fill="y")
        pw.add(df, weight=2)
        self.entries = []
        self.load()

    def load(self):
        entries = []
        try:
            with LOG_FILE.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        entries.append(json.loads(line))
                    except ValueError:
                        pass
        except FileNotFoundError:
            pass
        flt = self.filter_var.get()
        entries.reverse()
        if flt != "All projects":
            entries = [e for e in entries if e.get("project") == flt]
        if self.failed_only.get():
            entries = [e for e in entries if e.get("exit_code") != 0]
        self.entries = entries
        self.tree.delete(*self.tree.get_children())
        for i, e in enumerate(entries):
            self.tree.insert("", "end", iid=str(i), tags=("fail",) if e.get("exit_code") != 0 else (),
                             values=(e.get("time", "").replace("T", " "), e.get("project", ""), e.get("command", ""),
                                     e.get("exit_code", ""), e.get("duration", "")))
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", f"{len(entries)} command(s). Select one to see its output.")

    def show(self, _=None):
        sel = self.tree.selection()
        if not sel:
            return
        e = self.entries[int(sel[0])]
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", f"Time:     {e.get('time')}\nProject:  {e.get('project')}\n"
                                  f"Folder:   {e.get('cwd')}\nCommand:  {e.get('command')}\n"
                                  f"Exit:     {e.get('exit_code')}   ({e.get('duration')} s)\n"
                                  f"{'-' * 70}\n{e.get('output', '')}")

    def clear(self):
        if messagebox.askyesno(APP_NAME, "Delete the entire command log for all projects?", parent=self):
            with self.app.log_lock:
                try:
                    LOG_FILE.unlink()
                except FileNotFoundError:
                    pass
            self.load()


# --------------------------------------------------------------------------- main window

class App(tk.Tk):
    def __init__(self):
        # className sets the Linux WM_CLASS ("Git-project-manager") so the .desktop entry matches the window
        super().__init__(className="git-project-manager")
        self.title(APP_NAME)
        self.geometry("1120x720")
        self.minsize(860, 540)
        self.config_data = self.load_config()
        self.q = queue.Queue()
        self.log_lock = threading.Lock()
        self.busy = False
        self.current = None
        self.forcing = False   # True only while a forced command's steps are being built
        self.force_var = tk.BooleanVar(value=False)
        self.check_on_launch_var = tk.BooleanVar(value=bool(self.config_data.get("check_on_launch")))

        self._setup_style()
        self._set_icon()
        self._build_menu()
        self._build_ui()
        self.refresh_project_list()

        last = self.config_data.get("last")
        proj = next((p for p in self.config_data["projects"] if p["path"] == last), None)
        if proj or self.config_data["projects"]:
            self.select_project(proj or self.config_data["projects"][0])
        else:
            self.render_buttons()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(80, self._poll)
        if not shutil.which("git"):
            messagebox.showerror(APP_NAME, "Git was not found on your PATH.\nInstall it from https://git-scm.com and restart.")
        elif self.check_on_launch_var.get():
            self.after(400, self.check_all_status)

    # ---------- config
    def load_config(self):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except Exception:
            messagebox.showwarning(APP_NAME, f"Could not read {CONFIG_FILE}; starting with an empty project list.")
            data = {}
        data.setdefault("projects", [])
        for p in data["projects"]:
            p["buttons"] = normalize_buttons(p.get("buttons"))
        return data

    def save_config(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.config_data, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_FILE)

    # ---------- layout
    def _setup_style(self):
        style = ttk.Style(self)
        if sys.platform.startswith("linux"):
            style.theme_use("clam")
        base = tkfont.nametofont("TkDefaultFont")
        self.title_font = base.copy()
        self.title_font.configure(size=base.cget("size") + 6, weight="bold")
        self.head_font = base.copy()
        self.head_font.configure(size=base.cget("size") + 2, weight="bold")
        style.configure("Title.TLabel", font=self.title_font)
        style.configure("Heading.TLabel", font=self.head_font)
        style.configure("Cmd.TButton", padding=(8, 6))

    def _set_icon(self):
        """Use icon.png as the window/taskbar icon (next to the script, or bundled in a built binary)."""
        path = resource_path("icon.png")
        if os.path.isfile(path):
            try:
                self._icon = tk.PhotoImage(file=path)
                self.iconphoto(True, self._icon)
            except tk.TclError:
                pass

    def _build_menu(self):
        m = tk.Menu(self)
        fm = tk.Menu(m, tearoff=False)
        fm.add_command(label="New project…", command=self.new_project_dialog)
        fm.add_command(label="Import existing folder…", command=self.import_dialog)
        fm.add_command(label="Clone from URL…", command=lambda: self.run_command("clone", None))
        fm.add_separator()
        fm.add_command(label="Quit", command=self.on_close)
        m.add_cascade(label="File", menu=fm)
        gm = tk.Menu(m, tearoff=False)
        gm.add_command(label="GitHub login…", command=self.login_dialog)
        gm.add_command(label="Test GitHub login", command=self.test_login)
        gm.add_separator()
        gm.add_command(label="Check all status", command=self.check_all_status)
        gm.add_checkbutton(label="Check all status at launch", variable=self.check_on_launch_var,
                           command=self._toggle_launch_check)
        gm.add_separator()
        gm.add_command(label="Command log…", command=lambda: LogViewer(self))
        m.add_cascade(label="Git", menu=gm)
        pm = tk.Menu(m, tearoff=False)
        pm.add_command(label="Settings…", command=self.open_settings)
        pm.add_command(label="Open folder", command=self.open_folder)
        pm.add_command(label="Open on GitHub", command=lambda: self.run_command("open_web"))
        pm.add_command(label="Refresh", command=self.refresh_header)
        m.add_cascade(label="Project", menu=pm)
        self.config(menu=m)

    def _build_ui(self):
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(10, 3), relief="sunken").pack(side="bottom", fill="x")

        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)
        left = ttk.Frame(paned, padding=10)
        right = ttk.Frame(paned, padding=10)
        paned.add(left, weight=1)
        paned.add(right, weight=4)

        # left: projects
        ttk.Label(left, text="Projects", style="Heading.TLabel").pack(anchor="w")
        lf = ttk.Frame(left)
        lf.pack(fill="both", expand=True, pady=8)
        self.proj_list = tk.Listbox(lf, activestyle="none", exportselection=False, width=26)
        psb = ttk.Scrollbar(lf, orient="vertical", command=self.proj_list.yview)
        self.proj_list.configure(yscrollcommand=psb.set)
        self.proj_list.pack(side="left", fill="both", expand=True)
        psb.pack(side="right", fill="y")
        self.proj_list.bind("<<ListboxSelect>>", self._on_list_select)
        ttk.Button(left, text="＋  New project…", command=self.new_project_dialog).pack(fill="x", pady=2)
        ttk.Button(left, text="⇪  Import existing…", command=self.import_dialog).pack(fill="x", pady=2)
        ttk.Button(left, text="⤓  Clone from URL…", command=lambda: self.run_command("clone", None)).pack(fill="x", pady=2)
        ttk.Button(left, text="🔑  GitHub login…", command=self.login_dialog).pack(fill="x", pady=(12, 2))

        # right: header
        header = ttk.Frame(right)
        header.pack(fill="x")
        self.title_lbl = ttk.Label(header, text="No project selected", style="Title.TLabel")
        self.title_lbl.pack(side="left")
        ttk.Button(header, text="⚙ Settings", command=self.open_settings).pack(side="right")
        ttk.Button(header, text="📂 Open folder", command=self.open_folder).pack(side="right", padx=6)
        ttk.Button(header, text="↻ Refresh", command=self.refresh_header).pack(side="right")
        self.info_var = tk.StringVar()
        ttk.Label(right, textvariable=self.info_var, foreground="gray", justify="left").pack(anchor="w", pady=(4, 8))

        ch = ttk.Frame(right)
        ch.pack(fill="x", pady=(0, 4))
        ttk.Label(ch, text="Commands", style="Heading.TLabel").pack(side="left")
        fc = ttk.Checkbutton(ch, text="⚠ Force", variable=self.force_var, command=self.render_buttons)
        fc.pack(side="right")
        fc.bind("<Enter>", lambda e: self.status_var.set(
            "Force mode: the next forceable command runs with force after a warning. Turns off after one use."))
        fc.bind("<Leave>", lambda e: self.status_var.set("Running…" if self.busy else "Ready."))
        self.cmd_frame = ttk.LabelFrame(right, padding=8)
        self.cmd_frame.pack(fill="x")

        oh = ttk.Frame(right)
        oh.pack(fill="x", pady=(12, 4))
        ttk.Label(oh, text="Output", style="Heading.TLabel").pack(side="left")
        ttk.Button(oh, text="Clear", command=self.clear_console).pack(side="right")
        cf = ttk.Frame(right)
        cf.pack(fill="both", expand=True)
        self.console = tk.Text(cf, wrap="word", state="disabled", bg="#1e1f22", fg="#dcdcdc",
                               insertbackground="#dcdcdc", font=tkfont.nametofont("TkFixedFont"),
                               padx=8, pady=6, relief="flat")
        csb = ttk.Scrollbar(cf, orient="vertical", command=self.console.yview)
        self.console.configure(yscrollcommand=csb.set)
        self.console.pack(side="left", fill="both", expand=True)
        csb.pack(side="right", fill="y")
        self.console.tag_configure("cmd", foreground="#6cb6ff")
        self.console.tag_configure("proj", foreground="#e5c07b")
        self.console.tag_configure("ok", foreground="#7ec699")
        self.console.tag_configure("err", foreground="#ff7b72")

    # ---------- project list
    def refresh_project_list(self):
        self.proj_list.delete(0, "end")
        for p in self.config_data["projects"]:
            missing = not Path(p["path"]).is_dir()
            self.proj_list.insert("end", p["name"] + ("  ⚠" if missing else ""))
        if self.current in self.config_data["projects"]:
            i = self.config_data["projects"].index(self.current)
            self.proj_list.selection_clear(0, "end")
            self.proj_list.selection_set(i)

    def _on_list_select(self, _=None):
        s = self.proj_list.curselection()
        if s:
            self.select_project(self.config_data["projects"][s[0]])

    def select_project(self, p):
        self.current = p
        if p in self.config_data["projects"]:
            i = self.config_data["projects"].index(p)
            self.proj_list.selection_clear(0, "end")
            self.proj_list.selection_set(i)
            self.proj_list.see(i)
            self.config_data["last"] = p["path"]
            self.save_config()
        self.render_buttons()
        self.refresh_header()

    def add_project(self, path, name=None):
        path = str(Path(path).expanduser().resolve())
        for p in self.config_data["projects"]:
            if os.path.normcase(p["path"]) == os.path.normcase(path):
                self.select_project(p)
                return p
        p = {"name": name or Path(path).name, "path": path, "buttons": normalize_buttons(None)}
        self.config_data["projects"].append(p)
        self.config_data["projects"].sort(key=lambda x: x["name"].lower())
        self.save_config()
        self.refresh_project_list()
        self.select_project(p)
        return p

    def remove_project(self, p):
        self.config_data["projects"].remove(p)
        self.current = None
        self.save_config()
        self.refresh_project_list()
        if self.config_data["projects"]:
            self.select_project(self.config_data["projects"][0])
        else:
            self.render_buttons()
            self.refresh_header()

    def refresh_header(self):
        p = self.current
        if not p:
            self.title_lbl.config(text="No project selected")
            self.info_var.set("Create, import or clone a project to get started.")
            return
        self.title_lbl.config(text=p["name"])
        path = p["path"]
        if not Path(path).is_dir():
            self.info_var.set(f"⚠ Folder not found: {path}")
        elif git_out(["rev-parse", "--git-dir"], path)[0] != 0:
            self.info_var.set(f"{path}\nNot a git repository yet — use Init.")
        else:
            branch = current_branch(path) or "(detached)"
            remote = origin_url(path) or "none"
            _, changes = git_out(["status", "--porcelain"], path)
            n = len(changes.splitlines()) if changes else 0
            self.info_var.set(f"{path}\nBranch: {branch}   ·   Remote: {remote}   ·   "
                              f"{n} uncommitted change{'s' if n != 1 else ''}")

    def render_buttons(self):
        for w in self.cmd_frame.winfo_children():
            w.destroy()
        p = self.current
        if not p:
            ttk.Label(self.cmd_frame, text="Select or add a project on the left.").grid(row=0, column=0, sticky="w")
            return
        visible = [k for k, v in p["buttons"] if v]
        if not visible:
            ttk.Label(self.cmd_frame, text="All buttons are hidden — open ⚙ Settings to show some.").grid(row=0, column=0, sticky="w")
            return
        cols = 4
        forcing = self.force_var.get()
        for c in range(cols):
            self.cmd_frame.columnconfigure(c, weight=1, uniform="cmd")
        for i, key in enumerate(visible):
            cmd = COMMANDS[key]
            can_force = "force" in cmd
            b = ttk.Button(self.cmd_frame, text=("⚠ " if forcing and can_force else "") + cmd["label"],
                           style="Cmd.TButton", command=lambda k=key: self.run_command(k))
            if forcing and not can_force:
                b.state(["disabled"])
            b.grid(row=i // cols, column=i % cols, sticky="ew", padx=3, pady=3)
            desc = cmd["force"][0] if forcing and can_force else cmd["desc"]
            b.bind("<Enter>", lambda e, d=desc: self.status_var.set(d))
            b.bind("<Leave>", lambda e: self.status_var.set("Running…" if self.busy else "Ready."))

    # ---------- running commands
    def ask(self, title, fields, intro=None, **kw):
        return FormDialog(self, title, fields, intro=intro, **kw).result

    def run_command(self, key, p="current"):
        if p == "current":
            p = self.current
        cmd = COMMANDS[key]
        if self.busy:
            messagebox.showinfo(APP_NAME, "Please wait for the current command to finish.")
            return
        if cmd.get("needs_project", True):
            if not p:
                messagebox.showinfo(APP_NAME, "Select a project first.")
                return
            if not Path(p["path"]).is_dir():
                messagebox.showerror(APP_NAME, f"The folder no longer exists:\n{p['path']}")
                return
            if cmd.get("needs_repo", True) and git_out(["rev-parse", "--git-dir"], p["path"])[0] != 0:
                if messagebox.askyesno(APP_NAME, f"'{p['name']}' is not a git repository yet.\nRun git init now?"):
                    self.run_steps(p, b_init(self, p), lambda ok: self.refresh_header())
                return
        force = bool(self.force_var.get() and cmd.get("force"))
        if force:
            where = f"\n\nProject: {p['name']}" if p else ""
            if not messagebox.askyesno(f"Force {cmd['label']}?",
                                       f"{cmd['force'][1]}{where}\n\nThis cannot be undone. Continue?",
                                       icon="warning", default="no"):
                return
        self.forcing = force
        try:
            result = cmd["build"](self, p)
        finally:
            self.forcing = False
        if not result:
            return
        if force:  # force mode is one-shot, so the next click is never forced by accident
            self.force_var.set(False)
            self.render_buttons()
        steps, on_done = result if isinstance(result, tuple) else (result, None)

        def finished(ok):
            self.refresh_header()
            if on_done:
                on_done(ok)
        self.run_steps(p, steps, finished)

    def run_steps(self, project, steps, on_done=None, stop_on_error=True):
        if self.busy:
            messagebox.showinfo(APP_NAME, "Please wait for the current command to finish.")
            return
        self.busy = True
        self.status_var.set("Running…")
        threading.Thread(target=self._worker, args=(project, steps, on_done, stop_on_error), daemon=True).start()

    def _worker(self, project, steps, on_done, stop_on_error=True):
        ok = True
        for step in steps:
            proj = step.get("project") or project
            pname = proj["name"] if proj else "(global)"
            cwd = step["cwd"] or (proj["path"] if proj else str(Path.home()))
            label = step["label"] or quote_cmd(step["argv"])
            self.q.put(("text", (f"\n{pname} ", "proj")))
            self.q.put(("text", (f"$ {label}\n", "cmd")))
            t0 = time.time()
            if step["func"]:
                try:
                    out, code = (step["func"]() or ""), 0
                except Exception as e:
                    out, code = f"{e}", 1
                if out:
                    out = out if out.endswith("\n") else out + "\n"
                    self.q.put(("text", (out, None if code == 0 else "err")))
            else:
                code, out = self._stream(step["argv"], cwd, step["stdin"])
            dur = time.time() - t0
            self.q.put(("text", (f"[{'✔' if code == 0 else '✖'} exit {code} · {dur:.1f}s]\n", "ok" if code == 0 else "err")))
            if code != 0 and re.search(r"could not read Username|terminal prompts disabled|Authentication failed", out):
                self.q.put(("text", ("Git needs your GitHub login. Use 🔑 GitHub login… and try again.\n", "err")))
            self.write_log(pname, cwd, label, code, dur, out)
            if code != 0:
                ok = False
                if stop_on_error:
                    break
        self.q.put(("done", (ok, on_done)))

    def _stream(self, argv, cwd, stdin):
        if not os.path.isdir(cwd):
            msg = f"Folder does not exist: {cwd}\n"
            self.q.put(("text", (msg, "err")))
            return 1, msg
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", env=git_env(), creationflags=NO_WINDOW, bufsize=1,
            )
        except FileNotFoundError:
            msg = f"Command not found: {argv[0]}\n"
            self.q.put(("text", (msg, "err")))
            return 127, msg
        except Exception as e:
            msg = f"{e}\n"
            self.q.put(("text", (msg, "err")))
            return 1, msg
        if stdin is not None:
            try:
                proc.stdin.write(stdin)
                proc.stdin.close()
            except OSError:
                pass
        chunks = []
        for line in proc.stdout:
            chunks.append(line)
            self.q.put(("text", (line, None)))
        proc.wait()
        return proc.returncode, "".join(chunks)

    def write_log(self, project, cwd, command, code, dur, output):
        entry = {"time": datetime.datetime.now().isoformat(timespec="seconds"), "project": project,
                 "cwd": cwd, "command": command, "exit_code": code, "duration": round(dur, 2),
                 "output": output[-MAX_LOGGED_OUTPUT:]}
        with self.log_lock:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "text":
                    self.console_write(*payload)
                elif kind == "done":
                    ok, cb = payload
                    self.busy = False
                    self.status_var.set("Done." if ok else "Finished with errors — see output.")
                    if cb:
                        try:
                            cb(ok)
                        except Exception as e:
                            self.console_write(f"{e}\n", "err")
        except queue.Empty:
            pass
        finally:
            self.after(80, self._poll)

    def console_write(self, text, tag=None):
        self.console.configure(state="normal")
        self.console.insert("end", text, tag or ())
        if int(self.console.index("end-1c").split(".")[0]) > 6000:
            self.console.delete("1.0", "1000.0")
        self.console.see("end")
        self.console.configure(state="disabled")

    def clear_console(self):
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    # ---------- dialogs
    def open_settings(self):
        if self.current:
            SettingsDialog(self, self.current)
        else:
            messagebox.showinfo(APP_NAME, "Select a project first.")

    def open_folder(self):
        if self.current and Path(self.current["path"]).is_dir():
            open_path(self.current["path"])

    def import_dialog(self):
        folder = filedialog.askdirectory(title="Choose an existing project folder",
                                         initialdir=self.config_data.get("default_parent", str(Path.home())))
        if not folder:
            return
        is_repo = git_out(["rev-parse", "--git-dir"], folder)[0] == 0
        p = self.add_project(folder)
        if not is_repo and messagebox.askyesno(APP_NAME, "This folder is not a git repository yet.\nRun git init now?"):
            self.run_steps(p, b_init(self, p), lambda ok: self.refresh_header())

    def new_project_dialog(self):
        if self.busy:
            messagebox.showinfo(APP_NAME, "Please wait for the current command to finish.")
            return
        default_parent = self.config_data.get("default_parent") or str(
            Path.home() / "Projects" if (Path.home() / "Projects").is_dir() else Path.home())
        r = self.ask("New project", [
            F("name", "Project name", hint="Used for the folder and GitHub repository name."),
            F("parent", "Create inside", default_parent, "folder"),
            F("description", "Description"),
            F("gitignore", ".gitignore", "Python", "combo", values=list(GITIGNORES)),
            F("readme", "Create a README.md", True, "check"),
            F("github", "Create a matching GitHub repository and push to it", True, "check"),
            F("private", "Make the GitHub repository private", True, "check"),
        ], intro="Creates a new folder with a git repository and a first commit, "
                 "and (optionally) a repository on GitHub. The GitHub part needs 🔑 GitHub login first.",
            ok_text="Create")
        if not r:
            return
        slug = slugify(r["name"])
        if not slug:
            messagebox.showerror(APP_NAME, "Please enter a project name.")
            return
        dest = Path(r["parent"]).expanduser() / slug
        if dest.exists() and any(dest.iterdir()):
            messagebox.showerror(APP_NAME, f"{dest} already exists and is not empty.\nUse Import instead.")
            return
        cwd = str(dest)

        def scaffold():
            dest.mkdir(parents=True, exist_ok=True)
            if r["readme"]:
                (dest / "README.md").write_text(f"# {r['name']}\n\n{r['description']}\n", encoding="utf-8")
            gi = GITIGNORES.get(r["gitignore"], "")
            if gi:
                (dest / ".gitignore").write_text(gi, encoding="utf-8")
            return f"Created {dest}"

        steps = [S(func=scaffold, label=f"create folder {dest}"),
                 S(["git", "init", "-b", "main"], cwd=cwd),
                 S(["git", "add", "-A"], cwd=cwd),
                 S(["git", "commit", "--allow-empty", "-m", "Initial commit"], cwd=cwd)]
        if r["github"]:
            def create_remote():
                data = github_api("POST", "/user/repos", get_github_token(),
                                  {"name": slug, "description": r["description"], "private": r["private"]})
                url = data["clone_url"]
                code, out = git_out(["remote", "add", "origin", url], cwd)
                if code != 0:
                    raise RuntimeError(out)
                return f"Created {data.get('html_url')}\nAdded remote origin → {url}"
            steps += [S(func=create_remote, label=f"GitHub API: create repository '{slug}'"),
                      S(["git", "push", "-u", "origin", "main"], cwd=cwd)]

        self.config_data["default_parent"] = r["parent"]
        self.save_config()

        def done(ok):
            if (dest / ".git").exists():
                self.add_project(dest, name=r["name"])
        self.run_steps({"name": r["name"], "path": cwd}, steps, done)

    def login_dialog(self):
        if self.busy:
            messagebox.showinfo(APP_NAME, "Please wait for the current command to finish.")
            return
        home = str(Path.home())
        _, name = git_out(["config", "--global", "user.name"], home)
        _, email = git_out(["config", "--global", "user.email"], home)
        c, helper = git_out(["config", "--global", "credential.helper"], home)
        options = credential_helper_options()
        fields = [
            F("name", "Your name", name if name and "\n" not in name else "", hint="Shown as the author of your commits."),
            F("email", "Your email", email if email and "\n" not in email else ""),
            F("user", "GitHub username", self.config_data.get("github_user", "")),
            F("token", "Personal access token", kind="password",
              hint="Leave blank to keep the token that is already saved."),
            F("helper", "Store credentials with", helper if c == 0 and helper else options[0], "combo", values=options,
              hint={"store": "'store' saves the token in ~/.git-credentials (plain text, readable only by you).",
                    "manager": "'manager' uses Git Credential Manager (Windows Credential Manager).",
                    "osxkeychain": "'osxkeychain' saves the token in your macOS Keychain."}.get(options[0], "")),
        ]
        if shutil.which("gh"):
            fields.append(F("gh", "Also log in the GitHub CLI (gh) with this token", True, "check"))
        r = self.ask("GitHub login", fields, ok_text="Save",
                     intro="Saves your GitHub login in git's credential helper so push, pull and clone "
                           "never ask for a username or password. GitHub needs a personal access token "
                           "(classic) with the 'repo' and 'workflow' scopes instead of your password.",
                     extra_button=("Create a token on GitHub ↗", lambda: webbrowser.open(TOKEN_URL)))
        if not r:
            return
        steps = []
        for key, cfg in (("name", "user.name"), ("email", "user.email")):
            if r[key]:
                steps.append(S(["git", "config", "--global", cfg, r[key]], cwd=home))
        if r["helper"]:
            steps.append(S(["git", "config", "--global", "credential.helper", r["helper"]], cwd=home))
        if r["user"]:
            self.config_data["github_user"] = r["user"]
            self.save_config()
        if r["token"]:
            if not r["user"]:
                messagebox.showerror(APP_NAME, "Please enter your GitHub username together with the token.")
                return
            steps.append(S(["git", "credential", "approve"], cwd=home,
                           stdin=f"protocol=https\nhost=github.com\nusername={r['user']}\npassword={r['token']}\n\n",
                           label="git credential approve   (saving your GitHub token — hidden)"))
            if r.get("gh"):
                steps.append(S(["gh", "auth", "login", "--with-token"], cwd=home, stdin=r["token"] + "\n",
                               label="gh auth login --with-token   (token hidden)"))
        steps.append(S(func=self._verify_login, label="GitHub API: check login"))
        self.run_steps(None, steps)

    @staticmethod
    def _verify_login():
        data = github_api("GET", "/user", get_github_token())
        return f"✔ GitHub accepted your token — logged in as {data.get('login')}"

    def check_all_status(self):
        """Fetch + status for every project, then a one-line summary per project."""
        if self.busy:
            messagebox.showinfo(APP_NAME, "Please wait for the current command to finish.")
            return
        repos = [p for p in self.config_data["projects"]
                 if Path(p["path"]).is_dir() and git_out(["rev-parse", "--git-dir"], p["path"])[0] == 0]
        if not repos:
            self.console_write("\nCheck all status: no projects with a git repository yet.\n", "err")
            return
        steps = []
        for p in repos:
            if origin_url(p["path"]):
                steps.append(S(["git", "fetch", "--quiet"], project=p))
            steps.append(S(["git", "status", "-sb"], project=p))
        steps.append(S(func=lambda: status_summary(repos), label="summary of all projects"))
        self.run_steps(None, steps, lambda ok: self.refresh_header(), stop_on_error=False)

    def _toggle_launch_check(self):
        self.config_data["check_on_launch"] = bool(self.check_on_launch_var.get())
        self.save_config()

    def test_login(self):
        self.run_steps(None, [S(func=self._verify_login, label="GitHub API: check login")])

    def on_close(self):
        if self.busy and not messagebox.askyesno(APP_NAME, "A command is still running. Quit anyway?"):
            return
        self.destroy()


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-DPI screens
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
