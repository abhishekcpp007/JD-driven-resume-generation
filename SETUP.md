# Setup & Run — All CLI Commands

Copy-paste this file top-to-bottom. Every command runs from the project root: `/Users/apple/Resume_Automate`.

---

## 1. One-time setup

```bash
# 1a. Python dependencies
pip3 install flask jinja2 google-generativeai python-dotenv

# 1b. LaTeX compiler (used by build_resume.py)
brew install tectonic

# 1c. Gemini API key (get one at https://aistudio.google.com/apikey)
echo 'GEMINI_API_KEY=YOUR_KEY_HERE' > .env

# 1d. Make sure LaTeX CLI tools are on PATH in current shell
eval "$(/usr/libexec/path_helper)"
```

---

## 2. Run the web app

```bash
python3 app.py
```

Open **http://127.0.0.1:5000/** in your browser.

To stop: `Ctrl+C` in the terminal, or:

```bash
lsof -ti :5000 | xargs kill
```

---

## 3. Generate a resume from the CLI (no browser)

Uses `base_resume.md`, `job_description.txt`, and `config.json` in the project root.

```bash
# With Gemini (uses GEMINI_API_KEY from .env, hits your quota)
python3 build_resume.py

# Dry run (skips Gemini, uses fixtures/dry_run.json — great when quota is exhausted)
python3 build_resume.py --dry-run
```

Output goes to `Generated_Resume/resume_<template>.pdf`.

---

## 4. Pick a template

Edit `config.json` to set which template is used:

```bash
# ATS-optimised (recommended)
echo '{"template": "ats_pro"}' > config.json

# Other built-in templates
echo '{"template": "jakes_classic"}'      > config.json
echo '{"template": "jakes_multicols"}'    > config.json
echo '{"template": "vaishanth_modern"}'   > config.json
echo '{"template": "deedy_standard"}'     > config.json
echo '{"template": "elegant_tabularx"}'   > config.json
```

Or render every template in one go:

```bash
for t in ats_pro jakes_classic jakes_multicols deedy_standard vaishanth_modern elegant_tabularx; do
  echo "{\"template\": \"$t\"}" > config.json
  python3 build_resume.py --dry-run >/dev/null 2>&1
  pages=$(mdls -name kMDItemNumberOfPages -raw "Generated_Resume/resume_$t.pdf")
  echo "$t -> Generated_Resume/resume_$t.pdf ($pages page)"
done
echo '{"template": "ats_pro"}' > config.json
```

---

## 5. Open the generated PDF

```bash
open Generated_Resume/resume_ats_pro.pdf      # macOS Preview
# or any specific template:
open Generated_Resume/resume_deedy_standard.pdf
```

---

## 6. Hit the API directly (server must be running)

```bash
# Dry-run generate via the API
curl -s -X POST http://127.0.0.1:5000/api/generate \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c '
import json
print(json.dumps({
  "base_resume": open("base_resume.md").read(),
  "job_description": open("job_description.txt").read(),
  "template": "ats_pro",
  "dry_run": True
}))')" | python3 -m json.tool

# Real generate (uses Gemini quota)
curl -s -X POST http://127.0.0.1:5000/api/generate \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c '
import json
print(json.dumps({
  "base_resume": open("base_resume.md").read(),
  "job_description": open("job_description.txt").read(),
  "template": "ats_pro"
}))')" | python3 -m json.tool
```

The response includes `ats_score`, `matched_keywords`, `missing_keywords`, `ai_status`, `page_count`, optional `warning`.

---

## 7. Verify ATS score & page count for a finished PDF

```bash
# Page count (macOS Spotlight)
mdls -name kMDItemNumberOfPages -raw Generated_Resume/resume_ats_pro.pdf

# Re-score an already-rendered .tex against the current JD
python3 - <<'PY'
from build_resume import load_keywords_db, score_resume
db = load_keywords_db()
jd = open("job_description.txt").read()
tex = open("Generated_Resume/resume_ats_pro.tex").read()
res = score_resume(tex, jd, db)
print("Score :", res["ats_score"], "%")
print("Hits  :", len(res["matched_keywords"]))
print("Miss  :", len(res["missing_keywords"]))
print("Missing JD keywords:", res["missing_keywords"])
PY
```

---

## 8. Common troubleshooting

```bash
# Tectonic not found
which tectonic || brew install tectonic

# Re-add LaTeX tools to PATH for this shell
eval "$(/usr/libexec/path_helper)"

# Port 5000 already in use
lsof -ti :5000 | xargs kill

# Gemini quota exhausted -> use dry run, or wait for quota reset
python3 build_resume.py --dry-run

# View Flask logs in real time (when started in background)
tail -f /tmp/resume_app.log
```

---

## 9. Project layout cheat sheet

```
app.py                    # Flask server (port 5000)
build_resume.py           # AI -> Jinja2 -> Tectonic pipeline + ATS scoring
base_resume.md            # Your base resume (Markdown)
job_description.txt       # Target JD
config.json               # Active template
tech_keywords.json        # ATS keyword DB (roles + aliases)
fixtures/dry_run.json     # Fallback structured JSON for --dry-run
templates/                # LaTeX templates (.tex) + deedy-resume.cls + fonts/
static/                   # script.js, style.css
Generated_Resume/         # Output: resume_<template>.pdf + .tex
.env                      # GEMINI_API_KEY=...
```
