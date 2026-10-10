# Contributing to OPD Saathi

This guide takes you from nothing to pushing your own changes. Follow it top to bottom the first time. After that you only need [Everyday workflow](#3-everyday-workflow).

You do **not** need Python, Node.js or a database on your computer to run the app. Docker runs all of it.

---

## 1. Get access (repository owner does this once per person)

The repository is **private**, so you can't clone it until the owner invites you.

**Owner:**
1. Open <https://github.com/Shashi1543-07/OPD-Sathi> → **Settings** → **Collaborators** → **Add people**.
2. Type the teammate's GitHub username and click **Add**. Write access is the default, and it is enough to push.

**Teammate:**
1. Open the invitation email from GitHub (or <https://github.com/notifications>) and click **Accept invitation**.
2. Until you accept, `git clone` says *"Repository not found"*.

---

## 2. One-time setup on your computer

### 2.1 Install the tools

| Tool | Why | Where |
|---|---|---|
| **Git** | Download and upload code | <https://git-scm.com/downloads>. On Windows, keep every default option. They include Git Credential Manager, which handles the GitHub login for you. |
| **Docker Desktop** | Runs the database, API and web app | <https://www.docker.com/products/docker-desktop/>. On Windows, let it install/enable **WSL 2** if it asks, then restart the PC. |
| **VS Code** (optional) | Editing code | <https://code.visualstudio.com/> |
| **Chrome or Edge** | Voice features only work in these | — |

To check that they installed, open a **new** PowerShell window and run:

```powershell
git --version
docker --version
```

Both should print a version number.

### 2.2 Tell Git who you are

Use the **same email as your GitHub account**, so your commits show up under your name:

```powershell
git config --global user.name  "Your Name"
git config --global user.email "you@example.com"
```

### 2.3 Clone the repository

Pick a folder (for example `Documents`) and run:

```powershell
cd $HOME\Documents
git clone https://github.com/Shashi1543-07/OPD-Sathi.git
cd OPD-Sathi
```

The first time, a browser window opens asking you to **sign in to GitHub**. Sign in with the account that was invited. Git remembers the login, so you won't be asked again.

> **Do not change Git's line-ending settings.** The repository has a `.gitattributes` file that keeps the Linux scripts (`*.sh`, `Dockerfile`, `*.conf`) in the format Docker needs, even on Windows.

### 2.4 Run the app

1. Start **Docker Desktop** and wait until it says **Engine running**.
2. In PowerShell, inside the `OPD-Sathi` folder:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\start-demo.ps1
   ```

   - The first line only allows scripts in this one window. It does not change any system setting.
   - The **first run takes 5–10 minutes** because it downloads and builds everything. Later runs take about a minute. Add `-NoBuild` to skip rebuilding when you haven't changed code.
   - The first run creates your own `.env` file with random local passwords.

3. When it finishes, it prints:
   - the **patient kiosk link** (open it once to activate the kiosk; it also opens automatically),
   - the **staff workspace** link: <http://localhost:5173/staff>,
   - the **admin, doctor and nurse logins**,
   - **API docs**: <http://localhost:8000/docs>.

To stop the app, run `docker compose down`. Your data is kept. `docker compose down -v` also **deletes** the local database.

**macOS / Linux:** install PowerShell (`brew install powershell` on macOS, or see [Microsoft's guide](https://learn.microsoft.com/powershell/scripting/install/installing-powershell)), then run `pwsh ./start-demo.ps1`.

### 2.5 (Optional) Turn on AI and natural voices

The app works without any keys: tap-to-answer, browser voice, and rules-based safety alerts all run. For AI summaries and Indian-language voices:

1. Get your own free key at <https://aistudio.google.com/apikey> (Gemini). Optionally get one at <https://dashboard.sarvam.ai> (Sarvam voices).
2. Open `.env` in the project folder and fill in:
   ```
   GEMINI_API_KEY=your-key-here
   SARVAM_API_KEY=your-key-here
   ```
3. Run `.\start-demo.ps1` again.

> **Never commit `.env` or paste API keys into code, commits, issues or chat.** `.env` is already in `.gitignore`. GitHub's CI also scans every push for secrets and fails if it finds one.

---

## 3. Everyday workflow

Do your work on a **branch**, then open a **Pull Request (PR)**. `main` is protected: GitHub **rejects direct pushes to `main`** (*"protected branch hook declined"*). Changes only go in through a PR once its 3 CI checks pass.

```powershell
# 1. Start from the latest code
git checkout main
git pull

# 2. Make a branch for your change (use your name + a short description)
git checkout -b rohan/fix-queue-refresh

# 3. ...edit code, then run .\start-demo.ps1 and check your change works...

# 4. See what changed. Make sure .env is NOT in this list.
git status

# 5. Save your change
git add .
git commit -m "Fix queue not refreshing after a patient is called"

# 6. Upload your branch (the first push of a new branch needs -u origin <branch>)
git push -u origin rohan/fix-queue-refresh
```

7. Git prints a link like `https://github.com/Shashi1543-07/OPD-Sathi/pull/new/rohan/fix-queue-refresh`. Open it and click **Create pull request**.
8. Wait for the **CI checks** to turn green (about 10–15 minutes). If one fails, click **Details** to see why, fix it, then `git add .`, `git commit` and `git push` again. The PR updates automatically.
9. The owner reviews and clicks **Merge**.
10. Then update your own copy:
    ```powershell
    git checkout main
    git pull
    git branch -d rohan/fix-queue-refresh
    ```

**Pulling in other people's changes while you work:**

```powershell
git checkout main
git pull
git checkout your-branch
git merge main
```

If Git reports a **merge conflict**, open the listed files in VS Code. It shows *Accept Current / Accept Incoming / Accept Both* buttons. Pick the right result, save, then run `git add .` and `git commit`.

**After pulling changes**, run `.\start-demo.ps1` without `-NoBuild` so Docker rebuilds with the new code.

---

## 4. (Optional) Run the automated checks before pushing

CI runs these on every push anyway, so this step is optional. Running them locally finds problems faster. You need **Node.js 24** (<https://nodejs.org>) and **Python 3.12** (<https://www.python.org/downloads/>).

**Web app** (`apps/web`):

```powershell
cd apps/web
npm ci
npm run lint
npm run typecheck
npm test
cd ../..
```

**API** (`services/api`):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install --require-hashes -r services/api/requirements.lock.txt
ruff check services/api/app services/api/tests services/api/scripts
mypy --strict services/api/app
```

**If you change one of these, regenerate the matching file and commit it too.** CI checks that they're up to date:

| If you changed… | Run (from the project root, with `.venv` active) |
|---|---|
| An API endpoint or response model | `python services/api/scripts/write_openapi.py` then `npm --prefix apps/web run types` |
| The case-sheet data model | `$env:PYTHONPATH="services/api"; python services/api/scripts/write_case_sheet_schema.py` |
| Fixed spoken prompts / question bank text | `$env:PYTHONPATH="services/api"; python services/api/scripts/generate_prompt_audio.py` |
| Interview flow or case-sheet fields (golden test cases) | `$env:PYTHONPATH="services/api"; python eval/interview/golden/generate.py` |

---

## 5. Troubleshooting

| Problem | Fix |
|---|---|
| `git clone` says **"Repository not found"** | You haven't accepted the invitation yet, or you signed in with a different GitHub account. See [step 1](#1-get-access-repository-owner-does-this-once-per-person). |
| `git push` says **"Permission denied"** or **403** | Git saved the wrong GitHub account. Open Windows **Credential Manager** → **Windows Credentials**, remove the `git:https://github.com` entry, then push again and sign in with the invited account. |
| `git push` says **"protected branch hook declined"** | You tried to push to `main`. Move your work to a branch: `git checkout -b yourname/topic`, then `git push -u origin yourname/topic` and open a PR. Then reset your local `main` with `git checkout main` and `git reset --hard origin/main`. |
| `git push` says **"rejected … fetch first"** | Someone pushed before you. Run `git pull`, then `git push` again. |
| **"running scripts is disabled on this system"** | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first, in the same window. |
| **"Docker Desktop is not running"** / "Cannot connect to the Docker daemon" | Start Docker Desktop, wait for **Engine running**, then run the script again. |
| **"port is already allocated"** (5432, 6379 or 8000) | Another program is using that port, often a locally installed PostgreSQL or Redis. Stop that program (Windows: **Services** → stop *postgresql*), or quit the other project's containers. |
| Database errors on the **very first** run, or `\r: command not found` | Run `docker compose down -v` (this deletes only your local demo data), make sure you didn't change Git's line-ending settings, then run `.\start-demo.ps1` again. If it still fails, delete the folder and clone again. |
| Page won't load | Wait 20 seconds and refresh. If it still fails, run the script again. |
| Forgot the demo passwords | Run the script again (it prints them), or look in `.env` for `DEMO_ADMIN_PASSWORD` / `DEMO_STAFF_PASSWORD`. |
| Kiosk asks for an activation code | Run the script again and open the new activation link it prints. |

---

## Ground rules

- **Synthetic data only.** Never enter real patient information, not even when testing.
- **No secrets in Git.** Keys and passwords live only in your own `.env`.
- The app collects a patient history for clinicians. It must **not** diagnose or recommend treatment, so keep any wording you add to that standard.
