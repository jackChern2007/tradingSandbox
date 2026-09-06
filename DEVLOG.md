# Devlog contract

The supervisor writes the devlog. Entries may become public posts, so this file
is a constraint document before it is a style guide.

---

## The hazard, first

**A devlog is the most likely way this study gets ruined.**

"How's the model doing?" is the natural thing to write about and the one thing
that cannot be written. Reporting accuracy mid-run means reading outcomes, which
breaks the blinding in §3.6 of the pre-registration. It also creates a second,
subtler problem: once you have publicly said the model looks good at week three,
abandoning it at week ten costs something. Pre-registration exists to make that
decision free. A leaky devlog puts the price back.

So the devlog is **operational, not evaluative**, until N is reached.

The supervisor cannot violate this by accident: it reads `health/` only, and
`devlog.py` refuses to emit anything outcome-shaped. The rule is written here so
the constraint is understood rather than merely enforced.

One number about resolutions *is* allowed: how many forecasts have resolved.
Blinding covers outcome **values**, not sample **size** — a count says how far
along the study is and nothing about which way it is going. Without it nobody
blind could tell when N=300 had been reached.

---

## What can and cannot appear before N

| Fair game | Off limits until N |
|---|---|
| throughput, cycles, records | accuracy, Brier, skill score |
| parse-failure rate and reasons | any resolution or outcome |
| retrieval health, context length | "it called this one right" |
| verbalized vs logprob divergence | any running tally of wins |
| injection flags raised (counts) | whether it is "beating the market" |
| resolved count (progress toward N; carries no direction) | anything implying a live score |
| alarms fired and what broke | predictions about the final result |
| infrastructure and its failures | |

---

## The good news: two of the four questions need no outcomes

This is what makes an honest devlog worth reading rather than a compliance
exercise.

**Q3 — verbalized vs logprob.** Whether the model's stated number matches its
internal one is measurable on day one and needs no resolutions ever. Round-number
clustering, systematic gaps, where the two diverge most — all publishable while
the study is still blind.

**Q4 — injection.** Every instruction-shaped string the model finds in retrieved
text is a finding the moment it is flagged. Counts come from `health/`; the
**text** lives in `forecasts/`, which the supervisor cannot read, so Jack supplies
the quotes if an entry needs them. That boundary is not negotiable for
convenience.

Plus the thing nobody writes about honestly: what actually broke. Silent
retrieval collapse, tokenization surprises, a prompt the model quietly ignores.
That is the most useful and least published part of running one of these.

---

## Boring entries are valid entries

Some weeks the honest entry is:

> **Week 4.** +180 forecasts. Parse rate steady at 0.91. No alarms. Nothing
> broke. Nothing to report.

Publish it. The pressure to find a story every week is exactly the pressure that
turns a study into content, and a run of unremarkable entries is stronger
evidence of a real experiment than a run of exciting ones. **The supervisor must
never manufacture significance from a quiet window** — if the digest is flat, the
entry is short.

---

## Two registers, kept separate

**`devlog/entries/NNN.md`** — the internal record. Append-only, timestamped,
factual, generated from a `devlog.py` digest. Never edited after writing. This is
part of the study's audit trail.

**`devlog/posts/`** — drafts for publication. Derived from entries, freely
editable, voice and framing as you like.

Keeping them apart means a post can be rewritten ten times without touching the
record of what was true when.

---

## Procedure

```bash
python3 devlog.py --root study --since 7d > /tmp/digest.md
```

1. Supervisor reads the digest. **Every number in the entry comes from it.** No
   number is estimated, remembered, or inferred.
2. Supervisor writes `devlog/entries/NNN.md` using the template below.
3. If anything in the digest is unexplained, the entry says so plainly. "The
   thin-evidence share tripled and I do not know why" is a good line.
4. Jack decides separately whether an entry becomes a post.

### Entry template

```markdown
# Entry NNN — <date range>

**Status:** collecting | halted | complete
**Forecasts logged:** N · **resolved:** R of 300

## What ran
<throughput, uptime, cycles>

## What broke
<parse failures, alarms, infrastructure — say "nothing" if nothing>

## Signals
<retrieval, sharpness, verbalized-vs-logprob, injection counts>

## Open questions
<anything unexplained. This section is allowed to be the longest one.>

## Not in this entry
Outcome data is withheld by design until N=300 is reached. No accuracy figure
appears in any entry before then.
```

That last section stays in **every** entry. It tells a reader why the obvious
number is missing, and it keeps the writer honest.

---

## Cadence

Weekly, or on any halt. Do not post per-cycle — a devlog fine-grained enough to
show day-to-day movement invites reading trends into noise, which is the same
error the leaderboards in the teardowns make.

---

## Why this makes the study stronger, not just safer

A run of timestamped public entries that never once mention accuracy is
**evidence the protocol was followed.** It is a contemporaneous record that the
method did not change once results started arriving — which is the exact claim
every writeup in this genre makes and none can support.

The devlog stops being marketing attached to a study and becomes part of the
method.

---

## After N is reached

The boundary lifts entirely. The sample is closed and can no longer be biased.
Scoring, the full result, the calibration curves, the writeup — all unrestricted.

The final entry should link the raw logs and state the result whichever way it
landed, including "no detectable edge, and at this sample size I could not have
detected one anyway." Per §8 of the pre-registration, that gets published too.
