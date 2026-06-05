import os
import json
import re
import shutil
import subprocess
import sys
import time
from jinja2 import Environment, FileSystemLoader
import google.generativeai as genai
from dotenv import load_dotenv


KEYWORDS_DB_PATH = "tech_keywords.json"
FIXTURE_PATH = os.path.join("fixtures", "dry_run.json")

JD_STOP_PROPER_NOUNS = {
    # Pronouns / determiners
    "We", "You", "Our", "Your", "My", "Their", "The", "This", "That", "These", "Those", "A", "An",
    # Company suffixes
    "Inc", "LLC", "Corp", "Ltd", "Co", "Pvt", "Pte", "GmbH", "Company", "Team", "Group",
    # JD section headers
    "Role", "Position", "Job", "Description", "Responsibilities", "Requirements",
    "Qualifications", "Benefits", "Apply", "About", "Mission", "Vision", "Salary",
    "Overview", "Summary", "Skills", "Education", "Experience", "Profile", "Note",
    # Common sentence-start verbs / qualifiers (capitalized at start of line)
    "Looking", "Must", "Should", "Will", "Can", "Could", "Would", "Strong", "Hands",
    "Familiarity", "Knowledge", "Working", "Proven", "Good", "Great", "Excellent",
    "Solid", "Deep", "Proficient", "Expert", "Experienced", "Demonstrated", "Build",
    "Develop", "Maintain", "Design", "Implement", "Create", "Drive", "Lead", "Own",
    "Collaborate", "Work", "Join", "Help", "Ensure", "Deliver", "Ship", "Required",
    "Preferred", "Plus", "Bonus", "Nice", "Ideal", "Suitable", "Self", "Highly",
    # Boilerplate
    "Equal", "Opportunity", "Employer", "EOE",
}


def verify_and_configure_api():
    """Configures the AI engine using API key from .env."""
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("Set GEMINI_API_KEY in .env file or environment variable")
    print("🔓 Using API key from environment...")
    genai.configure(api_key=api_key)


def load_keywords_db():
    if os.path.exists(KEYWORDS_DB_PATH):
        with open(KEYWORDS_DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_data_files():
    if not os.path.exists("base_resume.md"):
        raise FileNotFoundError("Missing 'base_resume.md'")
    if not os.path.exists("job_description.txt"):
        raise FileNotFoundError("Missing 'job_description.txt'")

    with open("base_resume.md", "r", encoding="utf-8") as f:
        profile_markdown = f.read()

    with open("job_description.txt", "r", encoding="utf-8") as f:
        job_description = f.read()

    template = "jakes_classic"
    if os.path.exists("config.json"):
        with open("config.json", "r") as f:
            template = json.load(f).get("template", "jakes_classic")

    return {"template": template, "jd": job_description, "profile": profile_markdown}


def escape_latex_string(text):
    if not isinstance(text, str):
        return text
    if text.startswith("http"):
        return text
    special_chars = {
        '&': r'\&', '%': r'\%', '$': r'\$',
        '#': r'\#', '_': r'\_', '~': r'\textasciitilde{}',
        '^': r'\textasciicircum{}'
    }
    for char, escaped in special_chars.items():
        text = text.replace(char, escaped)
    return text


def sanitize_for_latex(data):
    if isinstance(data, dict):
        return {k: sanitize_for_latex(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [sanitize_for_latex(item) for item in data]
    elif isinstance(data, str):
        return escape_latex_string(data)
    else:
        return data


def _tokenize(text):
    """Lowercase tokens; keep word chars + # + + + . (for C#, C++, Node.js)."""
    return set(re.findall(r"[a-z0-9#+.]+", text.lower()))


def detect_relevant_roles(jd_text, keywords_db, top_n=2, min_score=3):
    """Return top-n roles whose keywords appear most often in the JD as
    (role_name, score, flat_keyword_list). Falls back to the single
    best-scoring role if nothing meets min_score."""
    jd_tokens = _tokenize(jd_text)
    scored = []
    for role_name, role_data in keywords_db.get("roles", {}).items():
        flat = [kw for cv in role_data.values() if isinstance(cv, list) for kw in cv]
        score = sum(1 for kw in flat if _tokenize(kw) & jd_tokens)
        scored.append((role_name, score, flat))
    scored.sort(key=lambda x: -x[1])
    qualifying = [s for s in scored if s[1] >= min_score]
    return qualifying[:top_n] if qualifying else scored[:1]


def build_prompt(user_data, keywords_db):
    matched_roles = detect_relevant_roles(user_data['jd'], keywords_db)
    action_verbs = ", ".join(keywords_db.get("ats_action_verbs", []))
    metrics = ", ".join(keywords_db.get("ats_metrics_phrases", []))
    soft_skills = ", ".join(keywords_db.get("soft_skills", []))

    role_keywords = ""
    for role_name, score, flat in matched_roles:
        role_keywords += (
            f"\n    Matched Role [{role_name}] (score={score}) Keywords: "
            f"{', '.join(flat[:80])}"
        )

    keywords_context = f"""
    ATS KEYWORD DATABASE (use these to maximize ATS score):
    Action Verbs (START bullet points with these): {action_verbs}
    Metrics Phrases (INCLUDE quantified results): {metrics}
    Soft Skills (weave into experience bullets naturally): {soft_skills}
    {role_keywords}
    """

    return f"""
    You are an elite ATS resume builder engine. Extract text profile data from the Markdown Profile,
    align keywords perfectly with the Job Description (JD), and return a single valid JSON block.
    {keywords_context}
    Rules:
    1. Cleanly parse first_name and last_name.
    2. Enhance bullet points in experience and projects to focus on metrics, scale, and tools listed in the JD.
    3. CRITICAL SINGLE-PAGE CONSTRAINT: The rendered PDF must fit on ONE Letter-size page. Enforce these hard limits:
       - Summary: at most 3 short lines (roughly 60 words / 400 characters).
       - Each experience bullet: ONE line in the PDF (target ~120 characters, hard cap 160).
       - Experience: at most 5 bullets per job; for older / less relevant jobs, 3 bullets.
       - Projects: at most 2 projects unless extremely relevant; at most 3 bullets each.
       - Skills: at most 6 skill categories, each one line (categories with too many items should be trimmed to the most JD-relevant 8-10).
       - Education: keep to 1-2 entries.
       - Drop sections (certifications, honors, leadership, soft_skills, coursework) if the candidate has nothing JD-relevant in them.
       Choose ruthless brevity over completeness. Single-page resume is the goal — not exhaustive history.
    4. CRITICAL: Maximize the ATS score (target 90-100%). Identify ALL technical keywords, tools, methodologies, and soft skills from the Job Description and seamlessly integrate them into the resume's skills list, experience bullets, and project descriptions.
    5. Rewrite the summary to explicitly reflect the exact Job Title and primary requirements from the JD.
    6. CRITICAL: DO NOT use any Markdown formatting (no **bold**, no *italics*, no markdown lists). Return ONLY plain text strings for all JSON values. Do not use asterisks or any other symbols for emphasis.
    7. START every bullet point with a strong ATS action verb from the database above.
    8. INCLUDE at least one quantified metric or result in every experience bullet point.
    9. MATCH the skills_categories items EXACTLY to the keywords found in the Job Description.
    10. EXACT PHRASE MIRRORING: Copy key phrases from the JD VERBATIM into bullet points instead of paraphrasing. If the JD says "RESTful API development", use that exact phrase, not "built REST APIs".
    11. KEYWORD REPETITION: Mention critical keywords (like Python, SQL, Docker) in BOTH the skills section AND at least one experience/project bullet. ATS systems count frequency.
    12. SKILLS CATEGORIES: Always generate at least 3 skill categories: "Languages", "Frameworks and Tools", "Methodologies". Add more if the JD warrants it (e.g., "Cloud and DevOps", "Databases").
    13. KEYWORD EXPANSION: Beyond the matched-role keywords above, scan the JD for ANY technology, tool, framework, methodology, or platform mentioned that is NOT in the matched-role list — ADD it. THEN add closely related/adjacent technology the candidate's profile plausibly supports. Examples:
        - JD says "Postgres" -> ALSO include "PostgreSQL", "SQL", "RDBMS", "Query Optimization" if profile mentions any DB work.
        - JD says "AWS" -> ALSO include relevant adjacent services (EC2, S3, Lambda, CloudWatch, IAM) IF candidate profile shows cloud experience.
        - JD says "React" -> ALSO include "JSX", "Hooks", "Component Lifecycle".
        - JD says "CI/CD" -> ALSO include "GitHub Actions" or "Jenkins" IF candidate has used either.
        Do NOT fabricate skills the candidate has no evidence for — only expand along directions supported by the profile.
    14. CANDIDATE TRUTHFULNESS: Never invent employers, dates, degrees, or fundamentally new skill domains. Expansion means re-labeling, grouping, and surfacing related tech, not lying.
    15. EMIT KEYWORDS USED: At the end of your JSON include a field "keywords_used": [...] listing EVERY technical keyword, tool, framework, methodology, and JD-specific term you wove into ANY part of the resume (skills, summary, experience bullets, project bullets). The list is used by the post-generation ATS scorer — be exhaustive.

    Return ONLY a raw valid JSON block matching this exact schema layout structure:
    {{
      "first_name": "", "last_name": "", "email": "", "phone": "", "location": "",
      "linkedin": "", "github": "", "portfolio": "", "summary": "",
      "education": [{{ "institution": "", "degree": "", "date": "", "location": "", "grade": "", "details": "" }}],
      "experience": [{{ "company": "", "title": "", "location": "", "date": "", "bullets": [] }}],
      "projects": [{{ "title": "", "subtitle": "", "tech_stack": "", "date": "", "bullets": [], "url": "" }}],
      "skills_categories": [{{ "name": "Languages", "items": [] }}, {{ "name": "Frameworks & Tools", "items": [] }}],
      "coursework": [], "interests": [], "soft_skills": [], "certifications": [],
      "honors": [{{ "date": "", "title": "" }}],
      "por": [{{ "title": "", "organization": "", "date": "", "bullets": [] }}],
      "leadership": [{{ "organization": "", "date": "", "role": "", "location": "", "bullets": [] }}],
      "keywords_used": []
    }}

    Profile:
    {user_data['profile']}

    Job Description:
    {user_data['jd']}
    """


def _builtin_dry_run_fixture(user_data):
    """Minimal hard-coded fixture used when fixtures/dry_run.json is missing."""
    return {
        "first_name": "Sample",
        "last_name": "Candidate",
        "email": "sample@example.com",
        "phone": "+1 555 000 0000",
        "location": "Remote",
        "linkedin": "linkedin.com/in/sample-candidate",
        "github": "github.com/sample-candidate",
        "portfolio": "",
        "summary": "Backend engineer with experience in Python, SQL, REST APIs, and cloud platforms. (Dry-run fixture — replace with fixtures/dry_run.json for production-quality output.)",
        "education": [{
            "institution": "Sample University",
            "degree": "B.S. Computer Science",
            "date": "2020 - 2024",
            "location": "Remote",
            "grade": "",
            "details": "",
        }],
        "experience": [{
            "company": "Sample Co.",
            "title": "Software Engineer",
            "location": "Remote",
            "date": "2024 - Present",
            "bullets": [
                "Designed and shipped Python services with PostgreSQL backing, reducing query latency by 40%.",
                "Built CI/CD pipelines using GitHub Actions and Docker, cutting deploy time by 60%.",
                "Collaborated cross-functionally with product and design to deliver REST APIs serving 10k+ daily requests.",
            ],
        }],
        "projects": [{
            "title": "Resume Automate",
            "subtitle": "",
            "tech_stack": "Python, Flask, LaTeX, Gemini API",
            "date": "2026",
            "bullets": [
                "Engineered a Flask + Jinja2 + Tectonic pipeline that compiles ATS-optimized resumes from a Markdown profile and Job Description.",
                "Integrated Gemini for JD-aware keyword expansion and added a deterministic ATS-coverage scorer.",
            ],
            "url": "",
        }],
        "skills_categories": [
            {"name": "Languages", "items": ["Python", "SQL", "JavaScript"]},
            {"name": "Frameworks and Tools", "items": ["Flask", "PostgreSQL", "Docker", "GitHub Actions", "AWS"]},
            {"name": "Methodologies", "items": ["REST APIs", "CI/CD", "Agile"]},
        ],
        "coursework": [],
        "interests": [],
        "soft_skills": ["Communication", "Problem Solving", "Collaboration"],
        "certifications": [],
        "honors": [],
        "por": [],
        "leadership": [],
        "keywords_used": [
            "Python", "SQL", "PostgreSQL", "REST APIs", "Flask",
            "Docker", "GitHub Actions", "CI/CD", "AWS",
        ],
    }


def load_dry_run_fixture(user_data):
    if os.path.exists(FIXTURE_PATH):
        with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return _builtin_dry_run_fixture(user_data)


def process_resume_with_ai(user_data, keywords_db, dry_run=False):
    """Returns (structured_json, ai_status). ai_status is 'dry_run', 'ok', or 'fallback'."""
    if dry_run:
        print("🧪 Dry run: using fixture, skipping Gemini call.")
        return load_dry_run_fixture(user_data), "dry_run"

    print("🤖 Analyzing requirements via Gemini 2.0 Flash...")
    model = genai.GenerativeModel('gemini-1.5-flash')
    prompt = build_prompt(user_data, keywords_db)

    max_retries = 3
    last_err = None
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            clean_text = re.sub(r'^```json\s*|```$', '', response.text.strip(), flags=re.MULTILINE).strip()
            try:
                return json.loads(clean_text), "ok"
            except json.JSONDecodeError as e:
                print(f"❌ Failed to parse AI response as JSON: {e}")
                print(f"Raw response:\n{clean_text[:500]}")
                last_err = e
                break
        except Exception as e:
            last_err = e
            if '429' in str(e) or 'ResourceExhausted' in str(type(e).__name__):
                wait_time = 60 * (attempt + 1)
                print(f"⏳ Rate limited. Waiting {wait_time}s before retry ({attempt+1}/{max_retries})...")
                time.sleep(wait_time)
            else:
                break

    print(f"⚠️ AI generation failed ({last_err}). Falling back to dry-run fixture.")
    return load_dry_run_fixture(user_data), "fallback"


def strip_latex(rendered_tex):
    """Best-effort: return only the visible text of a rendered LaTeX document."""
    body = rendered_tex
    if r"\begin{document}" in body:
        body = body.split(r"\begin{document}", 1)[1]
    if r"\end{document}" in body:
        body = body.split(r"\end{document}", 1)[0]
    body = re.sub(r"(?<!\\)%.*", "", body)
    # \href{url}{visible} -> visible
    body = re.sub(r"\\href\s*\{[^}]*\}\s*\{([^}]*)\}", r" \1 ", body)
    # Content-preserving commands -> content (with surrounding spaces so adjacent
    # \cmd tokens don't greedily absorb the inner letters on the next pass)
    body = re.sub(r"\\(?:textbf|textit|emph|underline|small|large|Large|LARGE|Huge|huge|scshape|raisebox|textbullet|noindent)\s*\{([^}]*)\}", r" \1 ", body)
    # Remaining commands with optional args -> space
    body = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", " ", body)
    # LaTeX punctuation that doesn't belong in extracted text
    body = re.sub(r"[{}\\$&%#^~]", " ", body)
    body = body.replace(" ", " ")
    return re.sub(r"\s+", " ", body).strip()


def _kw_in_text_regex(kw):
    """Build a word-boundary regex for kw. For multi-word keywords, allow flexible
    whitespace between tokens. Escapes special chars."""
    parts = re.split(r"\s+", kw.strip())
    pattern = r"\s+".join(re.escape(p) for p in parts)
    # Word boundary on each side — but \b doesn't fire next to '+', '#', '.' so
    # use lookaround that treats these chars + alphanumerics as word.
    return re.compile(r"(?<![A-Za-z0-9+#.])" + pattern + r"(?![A-Za-z0-9+#.])",
                      re.IGNORECASE)


def _contains_kw(text, kw):
    return bool(_kw_in_text_regex(kw).search(text))


def extract_jd_keywords(jd_text, keywords_db):
    """Return a set of keywords that the JD appears to require.

    Seed = role keywords + cert keywords + soft skills from tech_keywords.json
    that literally appear in the JD (word-bounded, case-insensitive). Augmented
    with capitalized multi-word proper nouns scraped from the JD that look
    like tech names, minus a small stop list."""
    seed = set()
    for role_data in keywords_db.get("roles", {}).values():
        for cat_val in role_data.values():
            if isinstance(cat_val, list):
                seed.update(cat_val)
    for cat_val in keywords_db.get("certifications_keywords", {}).values():
        if isinstance(cat_val, list):
            seed.update(cat_val)
    seed.update(keywords_db.get("soft_skills", []))

    in_jd = {kw for kw in seed if kw and _contains_kw(jd_text, kw)}

    # Proper-noun extraction: tech-shaped tokens, no newlines, no trailing punctuation.
    proper_noun_re = re.compile(
        r"\b([A-Z][a-zA-Z0-9+#]+(?:[ \-/][A-Z][a-zA-Z0-9+#]+){0,2})\b"
    )
    for m in proper_noun_re.finditer(jd_text):
        token = m.group(1).strip().strip(".,;:()")
        if not token or len(token) <= 2:
            continue
        parts = token.split()
        if any(p in JD_STOP_PROPER_NOUNS for p in parts):
            continue
        in_jd.add(token)

    return in_jd


def score_resume(rendered_tex, jd_text, keywords_db):
    visible = strip_latex(rendered_tex)
    jd_keywords = extract_jd_keywords(jd_text, keywords_db)
    aliases_db = keywords_db.get("common_aliases", {})
    matched, missing = [], []
    for kw in sorted(jd_keywords, key=lambda s: s.lower()):
        if _contains_kw(visible, kw):
            matched.append(kw)
            continue
        # Alias-aware: if any alias of kw is in the resume, count as matched.
        if any(_contains_kw(visible, alias)
               for alias in _alias_candidates(kw, aliases_db) if alias != kw):
            matched.append(kw)
        else:
            missing.append(kw)
    total = len(matched) + len(missing)
    score = round(100.0 * len(matched) / total) if total else 0
    return {
        "ats_score": score,
        "matched_keywords": matched,
        "missing_keywords": missing,
        "total_jd_keywords": total,
    }


def render_template(structured_json, template_name):
    jinja_env = Environment(
        block_start_string='[%', block_end_string='%]',
        variable_start_string='<<', variable_end_string='>>',
        comment_start_string='<#', comment_end_string='#>',
        loader=FileSystemLoader('templates')
    )
    template_filename = f"{template_name}.tex"
    if not os.path.exists(os.path.join("templates", template_filename)):
        print(f"⚠️ Warning: '{template_filename}' not found. Defaulting to 'jakes_classic.tex'")
        template_filename = "jakes_classic.tex"
    latex_template = jinja_env.get_template(template_filename)
    return latex_template.render(data=structured_json)


def count_pdf_pages(pdf_path):
    """Count pages in a PDF. Tries macOS mdls, then pdfinfo, then a raw regex
    over the PDF bytes (only works for uncompressed PDFs). Returns 0 on failure."""
    if not os.path.exists(pdf_path):
        return 0
    # macOS Spotlight metadata
    try:
        out = subprocess.run(
            ["mdls", "-name", "kMDItemNumberOfPages", "-raw", pdf_path],
            capture_output=True, text=True, timeout=5,
        )
        n = out.stdout.strip()
        if n.isdigit() and int(n) > 0:
            return int(n)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # pdfinfo from poppler
    try:
        out = subprocess.run(
            ["pdfinfo", pdf_path], capture_output=True, text=True, timeout=5,
        )
        m = re.search(r"^Pages:\s*(\d+)", out.stdout, re.MULTILINE)
        if m:
            return int(m.group(1))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Raw regex over uncompressed PDF bytes
    try:
        with open(pdf_path, "rb") as f:
            data = f.read()
        n = len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", data))
        return n
    except OSError:
        return 0


def compile_pdf(rendered_output, template_name):
    output_dir = "Generated_Resume"
    os.makedirs(output_dir, exist_ok=True)

    cls_src = os.path.join("templates", "deedy-resume.cls")
    if os.path.exists(cls_src):
        shutil.copy(cls_src, output_dir)

    fonts_src = os.path.join("templates", "fonts")
    fonts_dest = os.path.join(output_dir, "fonts")
    if os.path.exists(fonts_src) and not os.path.exists(fonts_dest):
        shutil.copytree(fonts_src, fonts_dest)

    output_base = f"resume_{template_name}"
    output_tex_path = os.path.join(output_dir, f"{output_base}.tex")
    with open(output_tex_path, "w", encoding="utf-8") as f:
        f.write(rendered_output)

    print(f"Step 4: Compiling PDF '{output_base}.pdf' using Tectonic...")
    result = subprocess.run(["tectonic", output_tex_path])
    output_pdf_path = os.path.join(output_dir, f"{output_base}.pdf")
    if result.returncode != 0:
        raise RuntimeError("Tectonic compilation failed")
    print(f"\n🎉 Success! Optimized resume ready: '{output_pdf_path}'")
    return output_pdf_path


TARGET_ATS_SCORE = 95
MAX_BOOST_ITEMS_PER_PASS = 12
MAX_FIT_ATTEMPTS = 5


_MATCH_STOP_WORDS = {
    "the", "and", "of", "a", "an", "in", "for", "to", "with", "or",
    "on", "by", "as", "is", "be",
}


def _match_tokens(text):
    """Tokens used for fuzzy keyword matching against the profile."""
    return [
        t for t in re.findall(r"[a-z0-9+#.]+", text.lower())
        if t not in _MATCH_STOP_WORDS
    ]


def _stem(token):
    """Crude singular/plural normalization."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _alias_candidates(kw, aliases_db):
    """Return kw plus any aliases defined for it (forward or reverse)."""
    out = {kw}
    for canonical, alias_list in aliases_db.items():
        if kw == canonical:
            out.update(alias_list)
        elif kw in alias_list:
            out.add(canonical)
            out.update(alias_list)
    return out


def _missing_supported_by_profile(missing_kws, profile_text, keywords_db=None):
    """Return missing JD keywords the candidate's profile plausibly supports.
    Match rules (any of):
      1. Word-boundary substring of the keyword in the profile.
      2. Word-boundary substring of an alias (from keywords_db.common_aliases)
         in the profile.
      3. Token-stem subset: every meaningful (de-pluralized) token of the
         keyword appears as a stem-token in the profile.
    """
    aliases_db = (keywords_db or {}).get("common_aliases", {})
    profile_stems = {_stem(t) for t in _match_tokens(profile_text)}
    supported = []
    for kw in missing_kws:
        if _contains_kw(profile_text, kw):
            supported.append(kw)
            continue
        if any(_contains_kw(profile_text, alias) for alias in _alias_candidates(kw, aliases_db) if alias != kw):
            supported.append(kw)
            continue
        kw_tokens = _match_tokens(kw)
        if not kw_tokens:
            continue
        kw_stems = {_stem(t) for t in kw_tokens}
        if kw_stems.issubset(profile_stems):
            supported.append(kw)
    return supported


def _inject_additional_skills(structured, keywords, max_items=MAX_BOOST_ITEMS_PER_PASS):
    """Append profile-supported keywords to an 'Additional Relevant Skills'
    category in structured.skills_categories. Avoids duplicates already present
    anywhere in skills_categories."""
    if not keywords:
        return structured
    cats = list(structured.get("skills_categories", []) or [])
    seen = set()
    for c in cats:
        for item in c.get("items", []) or []:
            if isinstance(item, str):
                seen.add(item.lower())
    extras = []
    for kw in keywords:
        if kw.lower() in seen:
            continue
        extras.append(kw)
        seen.add(kw.lower())
        if len(extras) >= max_items:
            break
    if not extras:
        return structured
    target = next(
        (c for c in cats if isinstance(c, dict) and c.get("name", "").lower().startswith("additional")),
        None,
    )
    if target is None:
        cats.append({"name": "Additional Relevant Skills", "items": extras})
    else:
        target["items"] = list(target.get("items", []) or []) + extras
    structured["skills_categories"] = cats
    return structured


def _trim_for_fit(structured, attempt):
    """Progressive trimming to fit a single page. `attempt` is 0-indexed.

    Returns True if anything was trimmed (caller should retry render),
    False if no further trimming is possible at this level."""
    changed = False

    def cap_bullets(section_key, per_entry):
        nonlocal changed
        items = structured.get(section_key) or []
        for entry in items:
            bullets = entry.get("bullets") or []
            if len(bullets) > per_entry:
                entry["bullets"] = bullets[:per_entry]
                changed = True

    if attempt == 0:
        cap_bullets("experience", 4)
        cap_bullets("projects", 3)
        # Drop ancillary lists that aren't core
        for key in ("coursework", "interests", "soft_skills"):
            if structured.get(key):
                structured[key] = []
                changed = True
    elif attempt == 1:
        cap_bullets("experience", 3)
        cap_bullets("projects", 2)
        if structured.get("leadership"):
            structured["leadership"] = []
            changed = True
        if structured.get("por"):
            structured["por"] = []
            changed = True
    elif attempt == 2:
        # Trim projects to 1 and honors to 1
        projects = structured.get("projects") or []
        if len(projects) > 1:
            structured["projects"] = projects[:1]
            changed = True
        honors = structured.get("honors") or []
        if len(honors) > 1:
            structured["honors"] = honors[:1]
            changed = True
        certs = structured.get("certifications") or []
        if len(certs) > 2:
            structured["certifications"] = certs[:2]
            changed = True
    elif attempt == 3:
        cap_bullets("experience", 2)
        # Tighten summary
        summary = structured.get("summary") or ""
        if len(summary) > 280:
            cut = summary[:280].rsplit(" ", 1)[0]
            structured["summary"] = cut.rstrip(",;.") + "."
            changed = True
    elif attempt >= 4:
        # Last resort: drop projects, honors, certifications
        for key in ("projects", "honors", "certifications"):
            if structured.get(key):
                structured[key] = []
                changed = True
    return changed


def _score_and_boost_loop(structured, profile_text, jd_text, keywords_db, template):
    """Iteratively render -> score -> inject profile-supported missing
    keywords into Additional Skills until score >= TARGET_ATS_SCORE or no
    further safe boost is possible.

    Returns (structured, last_score_result)."""
    last_score = None
    for _ in range(3):  # at most 3 boost passes
        rendered = render_template(sanitize_for_latex(structured), template)
        last_score = score_resume(rendered, jd_text, keywords_db)
        if last_score["ats_score"] >= TARGET_ATS_SCORE:
            return structured, last_score
        supported = _missing_supported_by_profile(
            last_score["missing_keywords"], profile_text, keywords_db
        )
        if not supported:
            return structured, last_score
        before_cats = json.dumps(structured.get("skills_categories", []), sort_keys=True)
        _inject_additional_skills(structured, supported)
        after_cats = json.dumps(structured.get("skills_categories", []), sort_keys=True)
        if before_cats == after_cats:
            # nothing new added (all duplicates) -> stop
            return structured, last_score
    return structured, last_score


def _fit_to_one_page(structured, template_name, jd_text, keywords_db):
    """Render -> compile -> check pages. If overflow, trim and retry.
    Returns (pdf_path, page_count, final_rendered, final_score)."""
    rendered = render_template(sanitize_for_latex(structured), template_name)
    pdf_path = compile_pdf(rendered, template_name)
    pages = count_pdf_pages(pdf_path)
    attempt = 0
    while pages > 1 and attempt < MAX_FIT_ATTEMPTS:
        print(f"⚠️  Resume is {pages} pages. Trimming (attempt {attempt + 1})...")
        if not _trim_for_fit(structured, attempt):
            print(f"   No further trimming possible at attempt {attempt}.")
            break
        rendered = render_template(sanitize_for_latex(structured), template_name)
        pdf_path = compile_pdf(rendered, template_name)
        pages = count_pdf_pages(pdf_path)
        attempt += 1
    final_score = score_resume(rendered, jd_text, keywords_db)
    return pdf_path, pages, rendered, final_score


def generate_resume_from_files(dry_run=False):
    """Returns a dict with pdf_path, ats_score, matched_keywords,
    missing_keywords, ai_status, template, page_count."""
    raw_config = load_data_files()
    keywords_db = load_keywords_db()

    if not dry_run:
        verify_and_configure_api()

    raw_structured_json, ai_status = process_resume_with_ai(
        raw_config, keywords_db, dry_run=dry_run
    )

    # Score-boost loop: deterministically surface JD keywords the candidate's
    # profile already contains so the resume covers them.
    structured_json, boost_score_result = _score_and_boost_loop(
        raw_structured_json,
        profile_text=raw_config["profile"],
        jd_text=raw_config["jd"],
        keywords_db=keywords_db,
        template=raw_config["template"],
    )
    if boost_score_result:
        print(
            f"📊 Post-boost score: {boost_score_result['ats_score']}%  "
            f"({len(boost_score_result['matched_keywords'])} matched / "
            f"{len(boost_score_result['missing_keywords'])} missing)"
        )

    # Fit-to-one-page loop: compile -> check pages -> trim -> retry.
    print("Step 3: Compiling structured JSON data payload into LaTeX via Jinja2 engine...")
    pdf_path, pages, _rendered, score_result = _fit_to_one_page(
        structured_json, raw_config["template"], raw_config["jd"], keywords_db
    )

    if pages > 1:
        print(f"⚠️  Could not fit on one page after trimming (final: {pages} pages).")
    else:
        print(f"📄 Page-count check: {pages} page.")
    print(
        f"📊 Final ATS Score: {score_result['ats_score']}%  "
        f"({len(score_result['matched_keywords'])} matched / "
        f"{len(score_result['missing_keywords'])} missing)"
    )

    return {
        "pdf_path": pdf_path,
        "ats_score": score_result["ats_score"],
        "matched_keywords": score_result["matched_keywords"],
        "missing_keywords": score_result["missing_keywords"],
        "ai_status": ai_status,
        "template": raw_config["template"],
        "page_count": pages,
    }


def main():
    dry = "--dry-run" in sys.argv
    result = generate_resume_from_files(dry_run=dry)
    print(
        f"\nATS Score: {result['ats_score']}%  "
        f"matched={len(result['matched_keywords'])}  "
        f"missing={len(result['missing_keywords'])}  "
        f"ai_status={result['ai_status']}"
    )


if __name__ == "__main__":
    main()
