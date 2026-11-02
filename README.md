# Git Project Manager

A desktop app (Python + Tkinter, no extra packages) for maintaining your GitHub projects.

## Run it

```
python git_project_manager.py
```

Requirements: Python 3.8+ with Tkinter, and Git.
- **Windows / macOS (python.org installer):** Tkinter is included.
- **Linux:** `sudo apt install python3-tk` (or your distro's equivalent).

## First-time setup: GitHub login

1. Click **🔑 GitHub login…** and then **Create a token on GitHub ↗**. Create a *classic* token with the `repo` and `workflow` scopes.
2. Enter your name, email, GitHub username and the token, then click **Save**.

The token is stored in Git's credential helper. On Windows that's Credential Manager, on macOS it's the Keychain, and on Linux it's `~/.git-credentials`. After that, `push`, `pull` and `clone` never ask for a username or password. **Git ▸ Test GitHub login** checks that the token works.

## What it does

| Area | How |
|---|---|
| Import an existing folder/repo | **Import existing…** (offers `git init` if needed) |
| New project + GitHub repo | **New project…** creates the folder, README, .gitignore, first commit, the GitHub repo (via the GitHub API), and pushes |
| Clone | **Clone from URL…** (the clone is added to the list automatically) |
| Command buttons | Status, Add, Commit, Push, Pull, Fetch, Branch, Switch, Merge, Tag, Release, History, Diff, Stash, Remote add origin, Init, Clone, Open on GitHub, **Custom command…** (any git/gh command) |
| Per-project settings | **⚙ Settings** to show/hide buttons, reorder them, rename or remove the project |
| Command log | **⚙ Settings ▸ View command log** (this project), or **Git ▸ Command log…** (all projects) |

Commands run in the background, and their output streams into the Output pane. A multi-step action stops at the first failing step.

## Notes

- **Release** creates a GitHub release through the GitHub API, and pushes the tag first if it exists locally.
- Commands that need an editor or typed input (e.g. `git rebase -i`) aren't supported, because nothing can wait on a prompt in the background.
- Settings and the log are stored in `~/.git_project_manager/`. Your token is never written to the log.
