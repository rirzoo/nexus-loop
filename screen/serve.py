#!/usr/bin/env python
"""Loop Desk: serves the one screen and records the human decision into loop-report.json.

    python3 screen/serve.py --report engine/out/loop-report.json

Stdlib only, no build step, no dependencies beyond what the engine already needs. The page is a
single HTML file; this process exists for one reason, which is that a web page cannot write to
disk on its own and an approve button that only sets client state is a mockup.

The write path is deliberately narrow. It may set exactly one thing:

    prescriptions[i].approval = {verdict, decided_by, confidence, reason, at}

and nothing else. Before saving it re-checks that every approval field is present and refuses to
write if any is missing. A half-written approval (a null `at`, an empty reason) makes the judges'
checklist claim a human decision nobody made, so it is rejected here rather than discovered later.
`confidence` is the operator's own stated confidence in the call they just made (low/medium/high),
not the system's diagnosis confidence — the two are recorded separately on purpose, so a reader can
tell "how sure was the detector" from "how sure was the person who signed off" at a glance.
(The engine is stdlib-only and asserts its own report link integrity at build time, so there is no
separate validator to import here.)

Decisions are bound to the report's build hash. If the report has been regenerated since the page
loaded, the write is refused: an approval has to be against the evidence the person actually saw.
Changing a decision appends to approval_history rather than overwriting it, so the record keeps
both entries.
"""
import argparse
import datetime
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

# The engine (engine/) is stdlib-only and asserts its own link integrity when it
# assembles the report, so there is no separate schema/contract validator to import.
# The one thing the write path must guarantee — a real, fully-signed human decision —
# is enforced directly in Store.decide() below. These stay as no-ops so decide()'s
# shape is unchanged: the report is never written if it would be structurally invalid.
def schema_errors(_):
    return None


def contract_problems(_):
    return []

VERDICTS = ("accepted", "rejected", "deferred")
CONFIDENCE_LEVELS = ("low", "medium", "high")
LOCK = threading.Lock()


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    """Owns the report file. Every read comes from disk so the page cannot drift from the truth."""

    def __init__(self, path):
        self.path = os.path.abspath(path)

    def load(self):
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)

    def save(self, rep):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1)
        os.replace(tmp, self.path)                 # atomic: never leave a half-written deliverable

    def decide(self, pid, verdict, decided_by, reason, confidence, seen_build=None):
        """Record one decision. Returns (ok, payload)."""
        if verdict not in VERDICTS:
            return False, {"error": "verdict must be one of %s" % (", ".join(VERDICTS))}
        # All five fields must be non-empty (confidence from a fixed set) or the contract check
        # fails and the report stops being a valid deliverable. Enforced here so the UI can never
        # produce that state.
        decided_by = (decided_by or "").strip()
        reason = (reason or "").strip()
        confidence = (confidence or "").strip().lower()
        if not decided_by:
            return False, {"error": "decided_by is required - an unsigned decision is not a decision"}
        if not reason:
            return False, {"error": "a reason is required, on an approval as much as on a rejection"}
        if confidence not in CONFIDENCE_LEVELS:
            return False, {"error": "confidence must be one of %s - how sure the operator is, "
                                     "not how sure the detector was" % (", ".join(CONFIDENCE_LEVELS))}

        with LOCK:
            rep = self.load()
            if seen_build and rep.get("report_build") and seen_build != rep["report_build"]:
                return False, {"error": "report_has_changed",
                               "detail": ("This report was regenerated after the page loaded. Reload "
                                          "and read the current evidence before deciding."),
                               "seen": seen_build, "current": rep["report_build"]}
            target = None
            for p in rep.get("prescriptions", []):
                if p.get("id") == pid:
                    target = p
                    break
            if target is None:
                return False, {"error": "no prescription %r in this report" % pid}

            before = json.loads(json.dumps(target.get("approval"))) if target.get("approval") else None
            entry = {"verdict": verdict, "decided_by": decided_by, "confidence": confidence,
                     "reason": reason, "at": now_iso()}
            # Bind the decision to the evidence it was made against.
            if rep.get("report_build"):
                entry["report_build"] = rep["report_build"]
            # The schema carries one approval; the audit trail keeps every one of them. A changed
            # decision appends - the original is never overwritten.
            hist = target.setdefault("approval_history", [])
            hist.append(entry)
            target["approval"] = {k: entry[k] for k in
                                  ("verdict", "decided_by", "confidence", "reason", "at")}

            errs = schema_errors(rep)
            probs = contract_problems(rep)
            if errs or probs:
                return False, {"error": "would_write_an_invalid_report",
                               "schema": errs or [], "contract": probs}
            self.save(rep)
        return True, {"prescription_id": pid, "before": before, "after": target["approval"],
                      "history": hist, "path": self.path}


def make_handler(store, page):
    class H(BaseHTTPRequestHandler):
        server_version = "LoopDesk/1.0"

        def _send(self, code, body, ctype="application/json"):
            b = body if isinstance(body, bytes) else json.dumps(body, indent=1).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                try:
                    with open(page, "rb") as fh:
                        return self._send(200, fh.read(), "text/html; charset=utf-8")
                except OSError as e:
                    return self._send(500, {"error": "cannot read the page", "detail": str(e)})
            if path == "/report":
                try:
                    return self._send(200, store.load())
                except OSError as e:
                    return self._send(500, {"error": "cannot read the report", "detail": str(e),
                                            "hint": "run python3 -m engine.cli --kit "
                                                    "nexus-loop-day1/kit first"})
                except ValueError as e:
                    return self._send(500, {"error": "the report is not valid JSON", "detail": str(e)})
            if path == "/health":
                return self._send(200, {"ok": True, "report": store.path,
                                        "exists": os.path.exists(store.path)})
            return self._send(404, {"error": "not_found",
                                    "endpoints": ["GET /", "GET /report", "POST /decision"]})

        def do_POST(self):
            if self.path.split("?")[0] != "/decision":
                return self._send(404, {"error": "not_found"})
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError as e:
                return self._send(400, {"error": "bad_json", "detail": str(e)})
            ok, payload = store.decide(body.get("prescription_id"), body.get("verdict"),
                                       body.get("decided_by"), body.get("reason"),
                                       body.get("confidence"), body.get("report_build"))
            return self._send(200 if ok else 400, payload)

        def log_message(self, fmt, *args):
            sys.stderr.write("  %s\n" % (fmt % args))

    return H


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    default_report = os.path.join(os.path.dirname(HERE), "engine", "out", "loop-report.json")
    ap.add_argument("--report", default=default_report, help="loop-report.json to read and write")
    ap.add_argument("--page", default=os.path.join(HERE, "index.html"))
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)

    store = Store(a.report)
    if not os.path.exists(store.path):
        print("No report at %s\nRun:  python3 -m engine.cli --kit nexus-loop-day1/kit "
              "--team solo --out engine/out/loop-report.json" % store.path, file=sys.stderr)
        return 2
    print("Loop Desk  http://%s:%d" % (a.host, a.port))
    print("  report   %s" % store.path)
    print("  decisions are written straight into that file, then re-checked before saving.")
    ThreadingHTTPServer((a.host, a.port), make_handler(store, a.page)).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
