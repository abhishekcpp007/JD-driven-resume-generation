from flask import Flask, render_template, request, jsonify, send_file
import os
import json
from build_resume import generate_resume_from_files

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/data", methods=["GET"])
def get_data():
    base_resume = ""
    job_description = ""
    template = "jakes_classic"
    
    if os.path.exists("base_resume.md"):
        with open("base_resume.md", "r", encoding="utf-8") as f:
            base_resume = f.read()
            
    if os.path.exists("job_description.txt"):
        with open("job_description.txt", "r", encoding="utf-8") as f:
            job_description = f.read()
            
    if os.path.exists("config.json"):
        with open("config.json", "r") as f:
            try:
                config = json.load(f)
                template = config.get("template", "jakes_classic")
            except Exception:
                pass
                
    return jsonify({
        "base_resume": base_resume,
        "job_description": job_description,
        "template": template
    })

@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.json
    base_resume = data.get("base_resume", "")
    job_description = data.get("job_description", "")
    template = data.get("template", "jakes_classic")
    
    with open("base_resume.md", "w", encoding="utf-8") as f:
        f.write(base_resume)
        
    with open("job_description.txt", "w", encoding="utf-8") as f:
        f.write(job_description)
        
    with open("config.json", "w") as f:
        json.dump({"template": template}, f)
        
    try:
        dry_run = bool(data.get("dry_run", False))
        result = generate_resume_from_files(dry_run=dry_run)
        filename = os.path.basename(result["pdf_path"])
        resp = {
            "success": True,
            "pdf_url": f"/api/pdf/{filename}",
            "ats_score": result["ats_score"],
            "matched_keywords": result["matched_keywords"],
            "missing_keywords": result["missing_keywords"],
            "ai_status": result["ai_status"],
            "page_count": result.get("page_count", 0),
        }
        warnings = []
        if result["ai_status"] == "fallback":
            warnings.append("AI generation failed (likely quota). Used fallback fixture.")
        elif result["ai_status"] == "dry_run":
            warnings.append("Dry run: skipped Gemini, used fixture data.")
        if resp["page_count"] > 1:
            warnings.append(
                f"Resume is {resp['page_count']} pages. Trim bullets / shorten summary to fit one page."
            )
        if warnings:
            resp["warning"] = " ".join(warnings)
        return jsonify(resp)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/pdf/<filename>")
def get_pdf(filename):
    pdf_path = os.path.join("Generated_Resume", filename)
    if not os.path.exists(pdf_path) or not filename.endswith(".pdf"):
        return "PDF not found", 404
    return send_file(pdf_path, mimetype='application/pdf')

if __name__ == "__main__":
    app.run(debug=True, port=5000)
