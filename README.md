# Git Project Manager

A desktop app (Python + Tkinter, no extra packages) for maintaining your GitHub projects.

## Run it

Keep all files in one folder, then:

- **Windows:** double-click `start.vbs` (opens the app with no console window).
- **Linux / macOS:** run `./start.sh`. The first time, make it executable with `chmod +x start.sh`.
- **Anywhere:** `python git_project_manager.py`.

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
| Check all status | **Git ▸ Check all status** runs fetch + status on every project and ends with a one-line summary each (uncommitted changes, commits to push/pull). Tick **Git ▸ Check all status at launch** to run it every time the app opens |
| Force mode | Tick **⚠ Force** above the buttons. Forceable commands (Add, Push, Pull, Branch, Switch branch, Tag) get a ⚠ and everything else is greyed out. Clicking one shows a warning popup first, and Force switches itself off after one use |
| Downloads | The **Downloads** tab next to Output lists each release's files and the build artifacts from GitHub Actions. Select a file and click **⬇ Download** (or double-click it) |
| Build notifications | While a GitHub Actions build runs, the project gets a ⏳ in the list, the Downloads tab shows a "building" banner, and a pop-up appears when it starts and when it finishes or fails. Turn it off with **Git ▸ Watch GitHub builds** |
| Per-project settings | **⚙ Settings** to show/hide buttons, reorder them, rename or remove the project |
| Command log | **⚙ Settings ▸ View command log** (this project), or **Git ▸ Command log…** (all projects) |

Commands run in the background, and their output streams into the Output pane, with the project name in front of each command (`website $ git push`). A multi-step action stops at the first failing step.

## Building binaries and installers

`icon.png` in the project folder is used for the app window, the .exe, the Windows installer and the Linux menu entry.

**Automatically (GitHub Actions):** push a version tag such as `v1.2.0` (the app's **Tag** button with *Push the tag* ticked does this). `.github/workflows/build.yml` then builds everything and attaches it to the GitHub release for that tag:

| File | What it is |
|---|---|
| `GitProjectManager-<version>-windows-setup.exe` | Windows installer (Start menu shortcut, optional desktop icon, uninstaller; no admin rights needed) |
| `GitProjectManager-<version>-windows-portable.exe` | Windows single-file app, no install |
| `git-project-manager_<version>_amd64.deb` | Debian/Ubuntu package (`sudo apt install ./git-project-manager_*.deb`) |
| `git-project-manager-<version>-linux-amd64` | Linux single-file app |

You can also run the workflow by hand from the **Actions** tab. Its files then appear as downloads on the run page instead of in a release.

**Locally:**
- **Linux:** `bash packaging/build_linux.sh` (needs Python with Tkinter and `python3-venv`).
- **Windows:** `powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1` (needs Python from python.org and Inno Setup 6: `winget install JRSoftware.InnoSetup`).

Output goes to `dist/`. The `build/` and `dist/` folders are generated, so add them to your `.gitignore`.

## Notes

- **Release** creates a GitHub release through the GitHub API, and pushes the tag first if it exists locally.
- The tag box in **Tag** and **Release** is a dropdown of existing tags and releases, grouped by naming scheme (e.g. `desktop-v…`, `android-v…`), with the next patch, minor and major version suggested for each group. You can still type any tag.
- Commands that need an editor or typed input (e.g. `git rebase -i`) aren't supported, because nothing can wait on a prompt in the background.
- Settings and the log are stored in `~/.git_project_manager/`. Your token is never written to the log.
