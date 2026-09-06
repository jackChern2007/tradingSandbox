#!/usr/bin/env python3
"""
tokencheck.py - the one-time tokenizer / decoder check from elicitation.md.

Run ONCE before collection against the real inference server. It answers, with
evidence, every row of the record table at the bottom of elicitation.md:

  * is thinking actually off (no think tag in the first-token distribution)?
  * which Yes/No variants exist as single tokens, and with what ids?
  * are the server's reported probabilities pre- or post-constraint?
  * what logprob_mass does Call B get on ten trial questions?

    python3 tokencheck.py --stack llama --url http://127.0.0.1:8080 [--weights model.gguf]
    python3 tokencheck.py --stack vllm  --url http://127.0.0.1:8000 --model <served name>

Stdlib only. One HTTP attempt per request, no retries. Nothing here touches
study/ and no market price is involved; the trial questions are generic.
"""

import argparse, hashlib, json, math, sys, urllib.error, urllib.request

YES = ("Yes", "yes", "YES")
NO = ("No", "no", "NO")
GRAMMAR = 'root ::= "Yes" | "No"'
TOPK = 20
MASS_FLOOR = 0.50

TRIALS = [
    ("Will it rain in London on at least one day next week?",
     "Resolves YES if the Met Office records measurable rain at Heathrow on any day."),
    ("Will the S&P 500 close higher next Friday than it did last Friday?",
     "Resolves YES if the official close is strictly higher."),
    ("Will a magnitude 7.0 or greater earthquake occur anywhere on Earth next month?",
     "Resolves YES per USGS."),
    ("Will the next Nobel Prize in Physics be shared by three laureates?",
     "Resolves YES if exactly three people are named."),
    ("Will a human land on Mars before 2030?",
     "Resolves YES on a crewed landing confirmed by the operating agency."),
    ("Will the home team win the next El Clasico?",
     "Resolves YES on a home win in regulation."),
    ("Will Bitcoin trade above $100,000 at any point next week?",
     "Resolves YES per a major exchange's trade print."),
    ("Will the US federal funds target range be cut at the next FOMC meeting?",
     "Resolves YES on any reduction of the target range."),
    ("Will a new species of mammal be formally described next year?",
     "Resolves YES on a peer-reviewed description."),
    ("Will the incumbent win the next mayoral election in Chicago?",
     "Resolves YES if the incumbent is certified the winner."),
]


def call_b_prompt(question, criteria, close="2026-12-31", today="2026-09-06"):
    return (f"QUESTION\n{question}\n\nRESOLVES\n{criteria}\n\n"
            f"RESOLUTION DATE\n{close}          TODAY: {today}\n\n"
            "EVIDENCE\n(no evidence retrieved)\n\n"
            "Will this resolve YES? Answer with exactly one word, Yes or No.\n"
            "Answer:")


def http(url, body=None, key=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.read().decode()[:400]}"}
    except Exception as e:  # one attempt; report, never retry
        return {"_error": repr(e)}


def chat(a, prompt, constrain=False):
    body = {
        "model": a.model, "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1, "temperature": 0.0, "logprobs": True,
        "top_logprobs": TOPK,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    if constrain:
        if a.stack == "llama":
            body["grammar"] = GRAMMAR
        else:
            body["guided_choice"] = ["Yes", "No"]
    return http(a.url.rstrip("/") + "/v1/chat/completions", body, a.api_key)


def top_logprobs(resp):
    try:
        return [(t["token"], t["logprob"], t.get("bytes"))
                for t in resp["choices"][0]["logprobs"]["content"][0]["top_logprobs"]]
    except (KeyError, IndexError, TypeError):
        return None


def norm(tok):
    """Token string with byte-level space markers removed, for matching."""
    for m in ("Ġ", "▁"):
        if tok.startswith(m):
            tok = tok[1:]
    return tok.strip()


def classify(top):
    p_yes = p_no = other = 0.0
    yes_hits, no_hits, think = [], [], []
    for tok, lp, _ in top:
        p = math.exp(lp)
        n = norm(tok)
        if n in YES:
            p_yes += p; yes_hits.append((tok, round(p, 4)))
        elif n in NO:
            p_no += p; no_hits.append((tok, round(p, 4)))
        else:
            other += p
        if "think" in tok.lower():
            think.append(tok)
    return p_yes, p_no, other, yes_hits, no_hits, think


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stack", choices=("llama", "vllm"), required=True)
    ap.add_argument("--url", required=True, help="server base url")
    ap.add_argument("--model", default="local", help="served model name (vLLM needs the real one)")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--weights", default=None, help="weights file to sha256")
    ap.add_argument("--out", default="tokencheck_result.json")
    a = ap.parse_args()

    rec = {"stack": a.stack, "url": a.url}

    # -- server identity --
    models = http(a.url.rstrip("/") + "/v1/models", key=a.api_key)
    rec["server_models"] = models.get("data", models)
    if a.stack == "llama":
        props = http(a.url.rstrip("/") + "/props", key=a.api_key)
        rec["server_props"] = {k: props.get(k) for k in
                               ("build_info", "model_path", "chat_template") if k in props}
        if isinstance(rec["server_props"].get("chat_template"), str):
            rec["server_props"]["chat_template"] = rec["server_props"]["chat_template"][-300:]
    if a.weights:
        print(f"hashing {a.weights} ...", file=sys.stderr)
        rec["weights_sha256"] = sha256(a.weights)

    # -- rendered prompt (llama.cpp can show it) --
    q, c = TRIALS[0]
    if a.stack == "llama":
        r = http(a.url.rstrip("/") + "/apply-template",
                 {"messages": [{"role": "user", "content": call_b_prompt(q, c)}],
                  "chat_template_kwargs": {"enable_thinking": False}}, a.api_key)
        rendered = r.get("prompt") if isinstance(r, dict) else None
        rec["rendered_tail"] = rendered[-200:] if rendered else r.get("_error")
    else:
        rec["rendered_tail"] = ("vLLM does not expose the rendered prompt; confirm from "
                                "the chat template that the assistant turn begins right "
                                "after 'Answer:' with enable_thinking=false")

    # -- raw vs constrained distribution on trial 0 --
    raw = chat(a, call_b_prompt(q, c))
    con = chat(a, call_b_prompt(q, c), constrain=True)
    t_raw, t_con = top_logprobs(raw), top_logprobs(con)
    if t_raw is None:
        print("FATAL: no top_logprobs in the unconstrained response:",
              json.dumps(raw)[:600], file=sys.stderr)
        return 2
    py, pn, po, yh, nh, think = classify(t_raw)
    rec["raw_top"] = [(t, round(math.exp(lp), 5)) for t, lp, _ in t_raw[:10]]
    rec["yes_variants_present"] = yh
    rec["no_variants_present"] = nh
    rec["think_tokens_in_top"] = think
    rec["thinking_off"] = not think and (py + pn) > 0.05
    if t_con is None:
        rec["constraint"] = {"applied": False,
                             "error": con.get("_error") or json.dumps(con)[:300]}
    else:
        cy, cn, co, _, _, _ = classify(t_con)
        rec["constraint"] = {
            "applied": True,
            "top": [(t, round(math.exp(lp), 5)) for t, lp, _ in t_con[:10]],
            "non_yesno_mass_reported": round(co, 5),
            "probabilities_are_pre_constraint": co > 1e-6,
        }

    # -- mass on ten trials --
    masses, rows = [], []
    for q, c in TRIALS:
        r = chat(a, call_b_prompt(q, c))
        t = top_logprobs(r)
        if t is None:
            rows.append({"q": q, "error": r.get("_error") or "no logprobs"}); continue
        py, pn, po, _, _, _ = classify(t)
        mass = py + pn
        masses.append(mass)
        rows.append({"q": q[:60], "p_yes_raw": round(py, 4), "p_no_raw": round(pn, 4),
                     "mass": round(mass, 4),
                     "p_logprob": round(py / (py + pn), 4) if mass else None,
                     "top1": t[0][0]})
    rec["trials"] = rows
    rec["mass_min"] = round(min(masses), 4) if masses else None
    rec["mass_median"] = round(sorted(masses)[len(masses) // 2], 4) if masses else None
    rec["trials_below_floor"] = sum(1 for m in masses if m < MASS_FLOOR)

    with open(a.out, "w") as fh:
        json.dump(rec, fh, indent=2)

    # -- the table for elicitation.md --
    ok = lambda b: "yes" if b else "NO"
    print("\n=== record for elicitation.md ===")
    print(f"| stack and version | {a.stack}; {json.dumps(rec.get('server_props', {}).get('build_info') or rec['server_models'])[:80]} |")
    print(f"| weights sha256 | {rec.get('weights_sha256', '(pass --weights)')} |")
    print(f"| thinking disabled via | chat_template_kwargs.enable_thinking=false; think tokens in top-{TOPK}: {think or 'none'} |")
    tail = rec["rendered_tail"] or ""
    print(f"| rendered Call B prefix ends at `Answer:` | {ok(tail.rstrip().endswith('Answer:') or 'Answer:' in tail[-120:])} — tail: {json.dumps(tail[-80:])} |")
    print(f"| YES variants present as single tokens | {yh or 'NONE'} |")
    print(f"| NO variants present as single tokens | {nh or 'NONE'} |")
    cst = rec["constraint"]
    if cst.get("applied"):
        print(f"| reported probabilities are pre-constraint | {ok(cst['probabilities_are_pre_constraint'])} — "
              f"non-Yes/No mass under constraint {cst['non_yesno_mass_reported']} |")
    else:
        print(f"| reported probabilities are pre-constraint | constraint NOT applied: {cst['error']} |")
    print(f"| logprob_mass on 10 trials (min / median) | {rec['mass_min']} / {rec['mass_median']}; "
          f"{rec['trials_below_floor']} below the {MASS_FLOOR} floor |")
    print(f"\nfull result written to {a.out}")

    problems = []
    if think:
        problems.append("thinking is NOT off - a think token appears in the first-token distribution")
    if not yh or not nh:
        problems.append("a Yes or No variant is missing as a single token")
    if cst.get("applied") and not cst["probabilities_are_pre_constraint"]:
        problems.append("server reports POST-constraint probabilities; use the unconstrained read for mass")
    if rec["trials_below_floor"] and rec["trials_below_floor"] >= 3:
        problems.append("mass below floor on 3+ trials - the prompt is not eliciting Yes/No")
    for p in problems:
        print("  !! " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
