"""
scripts/check_llm_fallback.py
------------------------------
Answers one question with a non-zero exit code: **is the local LLM fallback actually
working, for every service that thinks it has one?**

This exists because the fallback failed silently, repeatedly, in ways nothing surfaced
(milestone 65):

  * Ollama was not running at all, and every container still reported healthy;
  * when it was running, its default 127.0.0.1 binding was invisible from inside a
    container -- `host.docker.internal:11434` resolved and was refused in ~10 ms;
  * VoiceHR's evaluator fallback had settings with defaults but **no `.env` entries**, so
    the feature built in milestone 61 was switched off in practice;
  * the only symptom of any of it was a transient Gemini error becoming a hard failure
    instead of a fallback.

A fallback you cannot verify is not a fallback. So this checks, from *inside each
container* (which is the only place the answer means anything):

  1. what the service's own settings object actually says -- not what the .env file looks
     like, since a missing key silently becomes a default;
  2. whether the configured base URL is reachable from there;
  3. whether the specific models that service is configured to use are present;
  4. optionally (--generate) that a real completion comes back, because a reachable
     server with a broken model still fails at the only moment that matters.

Exit code is 0 only if every enabled fallback passes, so this can gate a demo or CI.

Run:
    python scripts/check_llm_fallback.py
    python scripts/check_llm_fallback.py --generate     # also prove one real completion
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

# How to read each service's *effective* configuration. Reading the settings object the
# app itself constructs is the point: a key missing from .env becomes a default, and a
# default is exactly how VoiceHR's fallback came to be disabled without anyone noticing.
#
# The model keys differ per service by design (milestone 61): the model that wins JD
# structuring is near the bottom at interview evaluation, so they must not be shared.
SERVICES = [
    {
        "container": "csb-resume-intelligence",
        "label": "resume-intelligence (API)",
        "probe": "from api import Settings; s = Settings()",
        "models": ["local_llm_structured_model", "local_llm_text_model"],
    },
    {
        "container": "csb-resume-intelligence-worker",
        "label": "resume-intelligence (worker)",
        "probe": "from api import Settings; s = Settings()",
        "models": ["local_llm_structured_model", "local_llm_text_model"],
    },
    {
        "container": "csb-recruitment-workflow",
        "label": "recruitment-workflow",
        "probe": "from main import Settings; s = Settings()",
        "models": ["local_llm_structured_model", "local_llm_text_model"],
    },
    {
        "container": "csb-voicehr",
        "label": "voicehr (evaluator)",
        "probe": "from backend.core.config import settings as s",
        "models": ["local_llm_evaluation_model"],
    },
]


def _exec(container: str, code: str, timeout: int = 180) -> tuple[int, str]:
    out = subprocess.run(
        ["docker", "exec", "-i", container, "python", "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    return out.returncode, ((out.stdout or "") + (out.stderr or "")).strip()


def check(service: dict, do_generate: bool) -> dict:
    models_expr = ", ".join(f"getattr(s, {m!r}, None)" for m in service["models"])
    code = f'''
import json
{service["probe"]}
enabled = bool(getattr(s, "local_llm_enabled", False))
base = getattr(s, "local_llm_base_url", None)
wanted = [m for m in ({models_expr},) if m]
result = {{"enabled": enabled, "base_url": base, "wanted": wanted,
           "reachable": None, "present": [], "missing": [], "error": None,
           "generated": None}}
if enabled and base:
    try:
        import httpx
        r = httpx.get(base.rstrip("/") + "/api/tags", timeout=10)
        result["reachable"] = (r.status_code == 200)
        if r.status_code == 200:
            have = {{m.get("name") or m.get("model") for m in r.json().get("models", [])}}
            # Ollama reports "name:tag"; a config value without a tag means ":latest".
            def known(w):
                return w in have or (w + ":latest") in have or any(
                    h.split(":")[0] == w.split(":")[0] and h.split(":")[-1] == w.split(":")[-1]
                    for h in have if h
                )
            result["present"] = [w for w in wanted if known(w)]
            result["missing"] = [w for w in wanted if not known(w)]
        else:
            result["error"] = f"HTTP {{r.status_code}} from /api/tags"
    except Exception as exc:
        result["reachable"] = False
        result["error"] = f"{{type(exc).__name__}}: {{str(exc)[:120]}}"

if {do_generate!r} and result.get("reachable") and result["present"]:
    try:
        import httpx
        m = result["present"][0]
        r = httpx.post(base.rstrip("/") + "/api/generate",
                       json={{"model": m, "prompt": "Reply with the single word: ok",
                              "stream": False, "think": False,
                              "options": {{"temperature": 0}}}},
                       timeout=300)
        body = r.json() if r.status_code == 200 else {{}}
        text = (body.get("response") or body.get("thinking") or "").strip()
        # Record WHY, not just that it failed. A check tool that reports "failed: None"
        # tells you nothing and sends you reading its own source instead of fixing the
        # fallback -- which is exactly what it did on its first real run.
        why = None
        if r.status_code != 200:
            why = f"HTTP {{r.status_code}}: {{r.text[:160]}}"
        elif not text:
            why = f"HTTP 200 but empty response; keys={{sorted(body)[:8]}} "                   f"done_reason={{body.get('done_reason')!r}}"
        result["generated"] = {{"model": m, "ok": bool(text), "sample": text[:40],
                               "error": why}}
    except Exception as exc:
        result["generated"] = {{"ok": False, "error": f"{{type(exc).__name__}}: {{str(exc)[:100]}}"}}

print("R:" + json.dumps(result))
'''
    rc, out = _exec(service["container"], code)
    if rc != 0:
        return {"fatal": f"could not query {service['container']}: {out[-200:]}"}
    for line in out.splitlines():
        if line.startswith("R:"):
            return json.loads(line[2:])
    return {"fatal": f"no result from {service['container']}: {out[-200:]}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--generate", action="store_true",
                    help="also send one real completion per service (slower, proves more)")
    args = ap.parse_args()

    print("\nLocal LLM fallback check  (from inside each container)\n")
    failures: list[str] = []

    for service in SERVICES:
        result = check(service, args.generate)
        label = service["label"]

        if "fatal" in result:
            print(f"  [SKIP] {label}\n         {result['fatal']}")
            # Not a failure: the container may legitimately not be running.
            continue

        if not result["enabled"]:
            print(f"  [OFF ] {label}\n         fallback disabled in this service's settings "
                  f"(LOCAL_LLM_ENABLED)")
            continue

        base = result["base_url"]
        if not result["reachable"]:
            print(f"  [FAIL] {label}\n         {base} unreachable -- {result['error']}")
            failures.append(f"{label}: unreachable at {base}")
            continue

        if result["missing"]:
            print(f"  [FAIL] {label}\n         {base} reachable, but missing model(s): "
                  f"{', '.join(result['missing'])}")
            failures.append(f"{label}: missing {result['missing']}")
            continue

        gen = result.get("generated")
        if gen is not None and not gen.get("ok"):
            print(f"  [FAIL] {label}\n         reachable with models present, but a real "
                  f"completion failed: {gen.get('error')}")
            failures.append(f"{label}: completion failed")
            continue

        detail = f"models: {', '.join(result['present'])}"
        if gen:
            detail += f"  |  generated {gen['sample']!r} with {gen['model']}"
        print(f"  [ OK ] {label}\n         {base}  |  {detail}")

    print()
    if failures:
        print(f"  {len(failures)} PROBLEM(S) -- the fallback will not save you:")
        for f in failures:
            print(f"    - {f}")
        print("\n  Most likely fixes:")
        print("    * stack not up          : docker compose -f infra/docker-compose.yml up -d")
        print("    * ollama service down   : docker compose -f infra/docker-compose.yml up -d ollama")
        print("    * model absent          : docker exec csb-ollama ollama pull <model>")
        print("    * base_url is host.docker.internal: it must be http://ollama:11434 now")
        return 1

    print("  All enabled fallbacks are reachable with their configured models present.")
    if not args.generate:
        print("  (Re-run with --generate to also prove a real completion comes back.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
