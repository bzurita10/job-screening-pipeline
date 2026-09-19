"""server.py — a thin local HTTP wrapper around agent_api.

Runs the pipeline as a small web service on your own machine so ANY front end
(a browser extension, a bookmarklet, an agentic browser, a shortcut, or plain
curl) can call it. It adds no cloud cost: it's a local Flask app you run
yourself. The only usage-based cost is the Claude API calls that analyze/
generate make — the same calls the CLI already makes; scanning, filtering,
keyword-gap and ATS-lint are all free (no API).

Run it:
    pip install -r requirements.txt
    python server.py            # serves on http://127.0.0.1:8765

Every endpoint returns the same JSON-safe dict the agent_api function returns,
always with an "ok" boolean. Errors come back as JSON, never as a crash.

SECURITY: binds to 127.0.0.1 (localhost) only, so nothing outside your machine
can reach it. Do not change host to 0.0.0.0 unless you know what that exposes.
"""

from flask import Flask, request, jsonify
from werkzeug.exceptions import HTTPException

import agent_api

app = Flask(__name__)


@app.errorhandler(HTTPException)
def _http_error_as_json(e):
    # Every error is JSON, so a browser extension never receives an HTML page.
    return jsonify({"ok": False, "error": e.description, "status": e.code}), e.code


@app.errorhandler(Exception)
def _uncaught_as_json(e):
    if isinstance(e, HTTPException):
        return _http_error_as_json(e)
    return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

# Permissive CORS so a future browser extension / bookmarklet can call this.
# (Localhost-only server, single user — this is safe here.)
@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


def _body() -> dict:
    return request.get_json(silent=True) or {}


@app.get("/")
def index():
    return jsonify({
        "service": "job-screening-pipeline",
        "ok": True,
        "endpoints": {
            "GET  /health": "liveness check",
            "POST /scan": "poll watchlist -> new ranked matches {analyze?, top?, include_seen?}",
            "POST /analyze": "score one posting {job_text}",
            "POST /generate": "build resume + cover letter for one posting {job_text}",
            "POST /ingest": "split & score a pasted blob {text, generate?}",
            "GET  /watchlist": "list companies + filters",
            "POST /watchlist": "add a company {name, ats, slug}",
            "GET  /watchlist/check": "verify every slug resolves",
            "POST /linkedin": "read LinkedIn alert emails {days?}",
            "GET  /applications": "list tracked applications (?status=)",
        },
    })


@app.get("/health")
def health():
    return jsonify({"ok": True})


@app.post("/scan")
def scan():
    b = _body()
    return jsonify(agent_api.scan_jobs(
        analyze=bool(b.get("analyze", False)),
        top=int(b.get("top", 8)),
        include_seen=bool(b.get("include_seen", False))))


@app.post("/analyze")
def analyze():
    return jsonify(agent_api.analyze_job(_body().get("job_text", "")))


@app.post("/generate")
def generate():
    return jsonify(agent_api.generate_documents(_body().get("job_text", "")))


@app.post("/ingest")
def ingest():
    b = _body()
    return jsonify(agent_api.ingest_batch(b.get("text", ""),
                                          generate=bool(b.get("generate", False))))


@app.get("/watchlist")
def watchlist():
    return jsonify(agent_api.list_watchlist())


@app.post("/watchlist")
def watchlist_add():
    b = _body()
    return jsonify(agent_api.add_watchlist_company(
        b.get("name", ""), b.get("ats", ""), b.get("slug", "")))


@app.get("/watchlist/check")
def watchlist_check():
    return jsonify(agent_api.check_watchlist())


@app.post("/linkedin")
def linkedin():
    return jsonify(agent_api.read_linkedin_alerts(days=int(_body().get("days", 7))))


@app.get("/applications")
def applications():
    return jsonify(agent_api.list_applications(status=request.args.get("status")))


if __name__ == "__main__":
    print("Pipeline server on http://127.0.0.1:8765  (Ctrl-C to stop)")
    app.run(host="127.0.0.1", port=8765, debug=False)
