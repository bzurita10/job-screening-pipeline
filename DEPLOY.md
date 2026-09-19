# Deploy & publish guide

Two parts: **A.** get it running on your machine, **B.** republish to GitHub
without leaking personal data. Part B matters — the original public repo exposed
real data because the web uploader ignored `.gitignore`. Use GitHub Desktop.

---

## A. Run it on your machine (~5 min)

1. **Unzip** to a permanent location, e.g. `C:\Users\bzuri\job-screening-pipeline-clean`.
   Don't run it from inside the Downloads folder.
2. **Fill your contacts.** Open `profile.yaml`, replace the three placeholders:
   `<<your phone>>`, `<<your email>>`, `<<your linkedin url>>`. (The ATS lint
   flags missing contacts until you do.)
3. **Run setup.** Double-click **`setup.bat`**, or in a terminal:
   ```
   cd C:\Users\bzuri\job-screening-pipeline-clean
   setup.bat
   ```
   It installs dependencies, checks your API key, and runs `scan.py --check`.
4. **If the key isn't set**, set it once and reopen the terminal:
   ```
   setx ANTHROPIC_API_KEY "sk-ant-..."
   ```
5. **Verify** with the 5 steps in `SMOKETEST.md`. Expect **Deel** and
   **Rippling** to be the two slugs most likely to need a fix on `--check`.

That's the whole local deploy. Daily use is just `python scan.py`.

---

## B. Republish to GitHub safely

You deleted the old repo (correct — its history held real data). Publish a
fresh one **with GitHub Desktop**, which respects `.gitignore`. Do **not** use
the web "upload files" / drag-and-drop uploader — that ignores `.gitignore` and
is what leaked data last time.

### Before you publish — confirm what's ignored
`profile.yaml`, `data/`, `output/`, `digests/`, `logs/`, and `jobs/inbox`
are git-ignored. The synthetic `profile.example.yaml` (fictional "Jordan
Rivera") is what ships instead of your real profile.

Quick sanity check in a terminal, from the folder:
```
git init
git check-ignore profile.yaml data output
```
Each path it prints back is confirmed ignored. If `profile.yaml` is **not**
printed, stop — do not publish until it is.

### Publish
1. Open **GitHub Desktop** → File → **Add local repository** → pick the folder
   (or **Create repository** here if `git init` above made an empty one).
2. In the **Changes** tab, **read the file list before the first commit.**
   `profile.yaml`, `data/`, and `output/` must **not** appear. If they do,
   `.gitignore` isn't taking effect — fix that first.
3. Commit (e.g. "Initial commit"), then **Publish repository**.
4. **Tick "Keep this code private"** for the first publish. You can flip it to
   public later once you've eyeballed the repo on github.com.

### After publishing — verify the first commit
On github.com, open the repo and confirm `profile.yaml`, `data/`, and `output/`
are absent from the file tree. Click into the commit and skim it once more.

### Repo settings (optional polish)
- **About:** "Python + Claude API pipeline that researches job postings, scores
  fit against a structured candidate profile, and auto-generates tailored
  resumes and cover letters."
- **Topics:** `python`, `claude`, `anthropic-api`, `job-search`, `automation`

This repo becomes portfolio project #1 — a working link from your LinkedIn.
