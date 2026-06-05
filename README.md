# Resume Automate

A Flask web app that uses Google Gemini to tailor your resume to a specific job description and compile it to PDF via LaTeX templates.

## Prerequisites

- **Python 3.9+**
- **Tectonic** (LaTeX engine) — `brew install tectonic`
- **Google Gemini API key** — get one at https://aistudio.google.com/apikey

## Setup

From the project root (`/Users/apple/Resume_Automate`):

```bash
# 1. Install Python dependencies
pip3 install flask jinja2 google-generativeai python-dotenv

# 2. Install Tectonic (LaTeX compiler used by build_resume.py)
brew install tectonic

# 3. Create a .env file with your Gemini API key
echo "GEMINI_API_KEY=your_api_key_here" > .env
```

## Run

```bash
python3 app.py
```

Then open **http://127.0.0.1:5000/** in your browser.

## Using the app

1. Paste / edit your **base resume** in Markdown on the left.
2. Paste the **job description** on the right.
3. Pick a **template** (e.g. `jakes_classic`, `vaishanth_modern`, `elegant_tabularx`, `deedy_standard`).
4. Click **Generate** — the AI rewrites the resume to match the JD and compiles a PDF.
5. The PDF is saved in `Generated_Resume/` and previewed in the browser.

## Project layout

```
app.py                 # Flask server
build_resume.py        # AI + LaTeX pipeline (Gemini -> Jinja2 -> Tectonic)
base_resume.md         # Your base resume (Markdown)
job_description.txt    # Target job description
config.json            # Selected template
templates/             # LaTeX templates + Jinja2
static/                # JS/CSS for the web UI
Generated_Resume/      # Output PDFs
tech_keywords.json     # ATS keyword database for prompt enrichment
```

## Endpoints

| Method | Path                  | Purpose                                    |
|--------|-----------------------|--------------------------------------------|
| GET    | `/`                   | Web UI                                     |
| GET    | `/api/data`           | Returns current `base_resume`, `jd`, `template` |
| POST   | `/api/generate`       | Saves inputs, runs AI + LaTeX pipeline, returns PDF URL |
| GET    | `/api/pdf/<filename>` | Serves a generated PDF                     |

## Troubleshooting

- **`Set GEMINI_API_KEY in .env file`** — create `.env` with `GEMINI_API_KEY=...` in the project root.
- **`tectonic: command not found`** — install with `brew install tectonic`.
- **Rate limit (429)** — the AI call auto-retries up to 3 times with backoff (60s, 120s, 180s).
- **Port already in use** — another `python3 app.py` is running. Stop it (`lsof -i :5000` then `kill <pid>`) or change the port in `app.py:73`.
