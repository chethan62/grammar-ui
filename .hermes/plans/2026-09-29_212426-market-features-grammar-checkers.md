# Market Feature Map → Prioritised Build List: Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Produce a cited, two-source-verified feature map of the top grammar-checking apps (Grammarly,
LanguageTool, ProWritingAid, Wordtune, QuillBot, Hemingway, Sapling, Paperpal, Ginger) and turn it into a
prioritised, locally-verifiable build list for `grammar-server` + `grammar-ui`.

**Architecture:** The research output is one CSV (`feature-matrix.csv`) which is the *only* source of
truth — one row per (app, capability) pair, each row carrying two independent evidence URLs plus how the
claim was verified — and the prose deliverable is a `.odt` rendered from it. Every claim that touches
this project's roadmap (LibreOffice integration, LT-client compatibility, text-length limits, rephrase
latency) is verified **by running something locally**, not by citing a vendor page. The plan ends with a
decision column per gap and exactly one fully-specified implementation slice (the top-ranked item).

**Tech Stack:** Go engine (`grammar-server`, LT-compatible API on `:8875`, harper 2.11 backend), static UI
(`grammar-ui`, `:8899`), Python desktop clients (`grammar-lookup.py`, `grammar-watch.py`,
`grammar-popup.py`), LibreOffice 26.8.0.3, `curl`, `tesseract`, `spectacle`, `magick`.

---

## Current context / assumptions

**Repos (exact paths):**
- Core: `/home/chethan/Documents/Default Project/grammar-server` — has `cmd/`, `internal/`, `examples/`,
  `deployments/`, `Makefile` (targets: `all bundle-local install uninstall package appimage clean`).
  **There is no `docs/` directory yet.** Go tests live in `internal/**/*_test.go`; run with `go test ./...`.
- UI: `/home/chethan/Documents/Default Project/grammar-ui` — `desktop/{grammar-lookup,grammar-watch,grammar-popup}.py`,
  `desktop/{test-lookup,test-watch}.py`, `test/esc.test.js`, `Makefile` (`make test` runs node + both python tests).

**Engine (verified live, `curl -s http://127.0.0.1:8875/status`):**
```json
{"dialect":"American","endpoints":["POST /v2/check","POST /v2/fix-sentence","POST /v2/rewrite","POST /v2/stats","GET /v2/languages","GET /status"],"service":"grammar-server","status":"OK"}
```
`/v2/stats` **already exists** — so the "readability report" gap is very likely an *exposure* problem, not
a build problem. Find out what it returns before planning any new statistics code.

**Constraints that decide everything:**
- CPU-only. The dGPU is hard-capped at 300 MHz / ~15.5 W, and the standing policy is **no GPU code path
  anywhere**. Latency measurements must be taken with the machine cool (see Risks).
- Offline-first: no cloud calls, and plagiarism checking was explicitly refused.
- No Docker; install = binary + systemd user unit.
- Unlimited text: no character caps (a genuine differentiator — see Task 7).

**Already gathered this session (do not redo; re-verify only):**
- 95 unique URLs from 8 queries; primary vendor pages: `grammarly.com/features`,
  `grammarly.com/where-grammarly-works`, `grammarly.com/desktop`, `languagetool.org/premium`,
  `prowritingaid.com/features`; independent: Zapier's 6-best round-up, Sapling's LT-vs-PWA comparison.
- Market shape: Grammarly = all-round (correctness, clarity rephrase, **full-paragraph rewrite**, tone,
  generative drafts, "1M+ apps" desktop overlay); LanguageTool = multilingual + self-hostable, 20k+ extra
  checks on Premium, 150k chars/field, unlimited paraphrasing, Style Guide + Personal Dictionary;
  ProWritingAid = **25+ Writing Reports** (readability, sticky sentences, passive voice), consistency,
  plagiarism in Premium Plus (60/yr), story/manuscript tools; Wordtune = rewrite/shorten/expand;
  QuillBot = paraphrase modes; Hemingway = readability stats; Paperpal = academic; Sapling = grammar +
  autocomplete.
- **Measured badly, and the measurement must not be trusted yet — read exit codes before believing a
  negative.** Only the X11 variants actually ran: `xdotool search --pid` confirmed a real window
  ("Position: 900,300 Geometry: 102x87") for override-redirect and managed X11, and full-screen `tesseract`
  found none of its text in a 3280x1080 screenshot. But **every launch of the real popup script failed
  before starting**: `/usr/bin/python3: can't open file '/home/chethan/Documents/Default': [Errno 2] No such
  file or directory` (exit 2) — the path was passed unquoted and split on the space in "Default Project".
  So the Wayland `POPUP_MENU` variant was never tested, and "the popup is invisible on Wayland" is an
  unsupported claim that this plan previously repeated. Kate (managed Wayland, spawned the same way) *is*
  visible, so windows from these processes do reach the screen. Task 8 redoes this properly.

**Assumption to state out loud:** vendor feature pages are marketing. A capability enters the CSV only
with two independent sources, or with a local command's output pasted into `verified_how`.

---

## Proposed approach

Work in three passes. **Pass A (Tasks 1–6):** build the matrix as a file, fill it per app tier, and
two-source every cell. **Pass B (Tasks 7–9):** verify the four claims that actually change this project's
roadmap by running local commands, and settle the popup question with the evidence that was missing.
**Pass C (Tasks 10–12):** rank the gaps into a decision column, render the `.odt`, commit, and record the
outcome in the skill. One implementation slice (Task 11) is specified end-to-end because it is
configuration-only; every other build item gets its own plan once it wins a rank.

---

## Pass A — the matrix

### Task 1: Create the matrix file and its header

**Objective:** A CSV that later tasks only append to, so every claim has one home.

**Files:**
- Create: `/home/chethan/Documents/Default Project/grammar-server/docs/market/feature-matrix.csv`

**Step 1: Write the header row and nothing else**

```csv
app,capability,level,evidence1,evidence2,verified_how,local_status,local_check,decision,effort
```

**Step 2: Verify it is a valid single-row CSV**

Run:
```bash
cd "/home/chethan/Documents/Default Project/grammar-server"
python3 -c "import csv;rows=list(csv.reader(open('docs/market/feature-matrix.csv')));print(len(rows), len(rows[0]))"
```
Expected: `1 10`

**Step 3: Commit**

```bash
git add docs/market/feature-matrix.csv
git commit -m "docs(market): empty feature matrix, one row per app+capability"
```

---

### Task 2: Pin the capability vocabulary

**Objective:** Fix the 12 capability keys so per-app tasks cannot invent synonyms (DRY).

**Files:**
- Create: `/home/chethan/Documents/Default Project/grammar-server/docs/market/README.md`

**Step 1: Write the vocabulary file**

```markdown
# Market feature matrix

Source of truth is `feature-matrix.csv`. One row per (app, capability).
`level` is free | paid. `local_status` is have | partial | gap | out-of-scope.
`decision` is build | extend | skip, with the reason in `verified_how`.

Capability keys (use these exact strings):
1. spelling-typos
2. grammar-agreement
3. punctuation
4. commonly-confused-words
5. clarity-rephrase
6. full-paragraph-rewrite
7. tone
8. style-readability-reports
9. consistency-variant-spellings
10. personal-dictionary-style-guide
11. generative-drafting
12. plagiarism

Rules:
- A capability enters the CSV only with two independent sources, or one local command's output.
- A vendor's own page counts as one source. Two vendor pages from the same vendor count as one.
```

**Step 2: Verify the keys are readable back**

Run:
```bash
grep -c '^[0-9]\+\. ' "/home/chethan/Documents/Default Project/grammar-server/docs/market/README.md"
```
Expected: `12`

**Step 3: Commit**

```bash
git add docs/market/README.md
git commit -m "docs(market): fix the capability vocabulary and the evidence rule"
```

---

### Task 3: Fill tier 1 — Grammarly, LanguageTool, ProWritingAid

**Objective:** Every capability row for the three market leaders, each with one vendor and one
independent source.

**Files:**
- Modify: `/home/chethan/Documents/Default Project/grammar-server/docs/market/feature-matrix.csv`

**Step 1: Re-fetch the primary pages (they may have changed since this session)**

Run:
```bash
python3 - <<'PY'
from hermes_tools import web_extract
for u in ["https://www.grammarly.com/features","https://languagetool.org/premium",
          "https://prowritingaid.com/features"]:
    r = web_extract([u], char_limit=6000)["results"][0]
    print("##", u, "\n", (r.get("content") or "")[:400], "\n")
PY
```
Expected: each page prints a feature list; if a page 404s or moved, record the new URL and continue.

**Step 2: Add one row per (app, capability) that the sources actually support**

Copy-pasteable pattern (repeat per row, changing the app and capability):

```bash
cd "/home/chethan/Documents/Default Project/grammar-server"
python3 - <<'PY'
import csv
row = ["Grammarly","full-paragraph-rewrite","paid",
       "https://www.grammarly.com/features","https://zapier.com/blog/best-ai-grammar-checker-rewording-tool/",
       "vendor page states 'Instantly accept full-paragraph rewrites'; Zapier lists Grammarly as the all-round editor", "", "", "", ""]
with open("docs/market/feature-matrix.csv","a",newline="") as fh:
    csv.writer(fh).writerow(row)
print("rows now:", sum(1 for _ in open("docs/market/feature-matrix.csv")) - 1)
PY
```
Expected: `rows now: 1` then increasing.

**Step 3: Verify no tier-1 row is single-sourced**

Run:
```bash
awk -F, 'NR>1 && ($4=="" || $5=="") && ($1=="Grammarly"||$1=="LanguageTool"||$1=="ProWritingAid") {print "MISSING:", $0}' \
  "/home/chethan/Documents/Default Project/grammar-server/docs/market/feature-matrix.csv"
```
Expected: no output.

**Step 4: Commit**

```bash
git add docs/market/feature-matrix.csv
git commit -m "docs(market): tier 1 rows with two sources each"
```

---

### Task 4: Fill tier 2 — Wordtune, QuillBot, Hemingway, Sapling

**Objective:** Same as Task 3, for the specialist tools.

**Files:**
- Modify: `/home/chethan/Documents/Default Project/grammar-server/docs/market/feature-matrix.csv`

**Step 1: Gather, one query per app (keeps results cheap and deduped)**

Run:
```bash
python3 - <<'PY'
from hermes_tools import web_search
for q in ["Wordtune features rewrite shorten expand official",
          "QuillBot paraphrase modes features official",
          "Hemingway editor readability features official",
          "Sapling grammar checker autocomplete features official"]:
    res = web_search(q, limit=10).get("data", {}).get("web", [])
    print("##", q)
    for r in res[:6]:
        print("   ", r.get("url"), "|", (r.get("title") or "")[:60])
PY
```
Expected: 6 URLs per query; pick the official domain as source 1 and any independent review as source 2.

**Step 2: Append the rows** — same pattern as Task 3, with the tier-2 app names.

**Step 3: Verify counts**

Run:
```bash
python3 -c "
import csv,collections
rows=list(csv.DictReader(open('/home/chethan/Documents/Default Project/grammar-server/docs/market/feature-matrix.csv')))
print(collections.Counter(r['app'] for r in rows))"
```
Expected: a per-app count; each of the seven apps so far has at least 5 rows.

**Step 4: Commit**

```bash
git add docs/market/feature-matrix.csv
git commit -m "docs(market): tier 2 rows"
```

---

### Task 5: Fill tier 3 — Paperpal, Ginger, self-hosted LanguageTool, and the local baseline

**Objective:** Include the academic tool, one legacy tool, and — importantly — the engine we actually
run, because the baseline's own capabilities belong in the matrix as `have`.

**Files:**
- Modify: `/home/chethan/Documents/Default Project/grammar-server/docs/market/feature-matrix.csv`

**Step 1: Verify harper's real capability list locally instead of trusting a README**

Run:
```bash
ls ~/.cache/grammar-server/ ; ~/.cache/grammar-server/harper-cli --version 2>&1 | head -2
printf 'She go to the office. Their going their own way.\n' > /tmp/harper-probe.txt
~/.cache/grammar-server/harper-cli --help 2>&1 | head -20
```
Expected: the version banner (2.11.0 as last seen) and the subcommand list. Paste whatever it actually
prints into `verified_how` for the baseline rows.

**Step 2: Add the baseline rows with `local_status` = have | partial, and a `local_check` command**

Example row (adjust to what Step 1 actually showed):
```
grammar-server(harpy/harper 2.11),spelling-typos,free,(local),(local),"harper-cli --version prints 2.11.0; /v2/check reports TYPOS category",have,"curl -s -XPOST localhost:8875/v2/check -d '{...}'",skip,
```

**Step 3: Verify every row has a non-empty `local_status`**

Run:
```bash
python3 -c "
import csv
bad=[r for r in csv.DictReader(open('/home/chethan/Documents/Default Project/grammar-server/docs/market/feature-matrix.csv')) if not r['local_status']]
print('unclassified rows:', len(bad)); [print(' ', r['app'], r['capability']) for r in bad[:10]]"
```
Expected: `unclassified rows: 0`

**Step 4: Commit**

```bash
git add docs/market/feature-matrix.csv
git commit -m "docs(market): tier 3 plus the local baseline, verified against harper"
```

---

### Task 6: Cross-check the market leaders' *limits* (the numbers people pay for)

**Objective:** Pin the character/word limits and tier boundaries, because "unlimited" is this project's
cheapest differentiator and must be sourced, not assumed.

**Files:**
- Modify: `/home/chethan/Documents/Default Project/grammar-server/docs/market/feature-matrix.csv`

**Step 1: Extract two independent statements about LanguageTool's limits**

Run:
```bash
python3 - <<'PY'
from hermes_tools import web_search, web_extract
res = web_search("LanguageTool premium 150000 characters per text field free 10000 limit", limit=10)
urls = [r["url"] for r in res.get("data", {}).get("web", []) if "languagetool.org" in r["url"] or "comparison" in r["url"]][:2]
print(urls)
for r in web_extract(urls, char_limit=4000)["results"]:
    txt = r.get("content") or ""
    for line in txt.splitlines():
        if "characters" in line.lower() and any(c.isdigit() for c in line):
            print(r["url"], "->", line.strip()[:160])
PY
```
Expected: at least two lines naming a numeric limit (10,000 free / 150,000 premium as last seen).

**Step 2: Add a `limits` row per app** using the same CSV pattern, `capability` = `text-length-limits`.

**Step 3: Commit**

```bash
git add docs/market/feature-matrix.csv
git commit -m "docs(market): sourced character limits per app"
```

---

## Pass B — verify what actually changes our roadmap

### Task 7: Verify the four claims locally, with a command each

**Objective:** Replace four "probably"s with four command outputs. This is the task that makes the whole
document worth trusting.

**Files:**
- Create: `/home/chethan/Documents/Default Project/grammar-server/examples/market-claims-check.sh`

**Step 1: Write the script with all four checks**

```bash
#!/usr/bin/env bash
# Verifies the four market claims that touch this project's roadmap.
# Exits non-zero if any check changes, so it can gate CI.
set -uo pipefail
FAIL=0
api=${GRAMMAR_API:-http://127.0.0.1:8875}

echo "1) the engine answers the LanguageTool client contract"
python3 examples/lt-client-smoke.py >/tmp/lt-smoke.out 2>&1 && echo "   PASS" || { echo "   FAIL"; tail -5 /tmp/lt-smoke.out; FAIL=1; }

echo "2) no character cap: 200k characters are accepted"
python3 - "$api" <<'PY'
import json, sys, urllib.request
text = ("She go to the office. " * 10000)   # ~210k characters
body = json.dumps({"text": text, "language": "en-US"}).encode()
req = urllib.request.Request(sys.argv[1] + "/v2/check", data=body, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=120) as resp:
    data = json.load(resp)
print("   %d chars accepted, %d matches" % (len(text), len(data.get("matches", []))))
PY
[ $? -eq 0 ] || FAIL=1

echo "3) /v2/stats exists and says what it computes"
curl -s -m 30 -XPOST "$api/v2/stats" -H 'Content-Type: application/json' \
  -d '{"text":"The quick brown fox jumps over the lazy dog. It was a very nice day."}' | head -c 400; echo

echo "4) rephrase latency on this CPU (one sentence, cold-ish)"
time curl -s -m 120 -XPOST "$api/v2/rewrite" -H 'Content-Type: application/json' \
  -d '{"text":"In order to make the report better, we should consider the possibility of revising it."}' | head -c 300; echo

exit $FAIL
```

**Step 2: Run it and record the real numbers**

Run:
```bash
cd "/home/chethan/Documents/Default Project/grammar-server"
chmod +x examples/market-claims-check.sh && ./examples/market-claims-check.sh
```
Expected: four sections, each printing a real value. Then check the machine is not hot before trusting
check 4's timing:
```bash
uptime; sensors 2>/dev/null | grep -m2 -i 'package\|tctl' || cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | head -3
```
If package temp is above ~80 °C, re-run check 4 after it cools and record both numbers.

**Step 3: Paste each result into `verified_how` for the matching rows**

**Step 4: Commit**

```bash
git add examples/market-claims-check.sh docs/market/feature-matrix.csv
git commit -m "docs(market): verify the four roadmap claims with a runnable script"
```

---

### Task 8: Settle the popup question with the evidence that was missing

**Objective:** Either prove a popup can be visible on this session, or record the finding that it cannot
— with the four earlier variants re-run **in the foreground so stderr is captured**.

**Files:**
- Create: `/tmp/popup-matrix.log` (scratch, not committed)
- Modify: `/home/chethan/Documents/Default Project/grammar-server/docs/market/ambient-ui-notes.md`

**Step 1: Run each variant in the foreground, capturing exit code and stderr — with every path quoted**

The path to the real script contains a space. An unquoted launch fails with
`can't open file '/home/chethan/Documents/Default'` and exit 2, which looks exactly like "the window was
created but never shown" if you only go looking for the window afterwards. That is the mistake this task
exists to undo: quote every path, and read the exit code before drawing any conclusion.

```bash
cd /tmp
UI="/home/chethan/Documents/Default Project/grammar-ui/desktop/grammar-popup.py"
run() {  # $1 = label, rest = the command
  local label="$1"; shift
  echo "=== $label"
  timeout 8 "$@" >/tmp/one.log 2>&1; echo "exit=$?"
  sed -n '1,15p' /tmp/one.log
}
run "override-redirect X11" env GDK_BACKEND=x11     /usr/bin/python3 /tmp/opencode/gtkpop3.py 900 300
run "managed X11"           env GDK_BACKEND=x11     /usr/bin/python3 /tmp/opencode/gtkpop4.py 900 300
run "managed Wayland"       env GDK_BACKEND=wayland /usr/bin/python3 /tmp/opencode/gtkpop5.py 900 300
run "real script X11"       env GDK_BACKEND=x11     /usr/bin/python3 "$UI" --x 900 --y 300 --label "go to goes" --timeout 5
run "real script Wayland"   env GDK_BACKEND=wayland /usr/bin/python3 "$UI" --x 900 --y 300 --label "go to goes" --timeout 5
```
Expected: `exit=124` for each (killed by `timeout` while still up) and **empty** stderr. `exit=2` with
`can't open file '/home/chethan/Documents/Default'` means the path was split again — fix the quoting before
concluding anything. Any other non-zero exit printing a GTK/Wayland error is the finding you were after.

**Step 2: For whichever variant exits cleanly, screenshot while it runs and OCR the whole screen**

```bash
( sleep 2; spectacle -b -n -f -o /tmp/popup-check.png ) &
timeout 8 env GDK_BACKEND=wayland python3 /tmp/opencode/gtkpop5.py 900 300 >/dev/null 2>&1
sleep 2
tesseract /tmp/popup-check.png stdout 2>/dev/null | grep -iE 'goes|fix it' || echo "NOT VISIBLE"
```
Expected: either `goes`/`Fix it` with coordinates (popup works — then wire it), or `NOT VISIBLE`
(the finding to record).

**Step 3: Write the finding**

```markdown
# Ambient UI notes

## Popup at the caret on KWin Wayland — measured 2026-09-29

Variants tried: override-redirect X11, managed X11, Wayland POPUP_MENU, managed Wayland.
All were created and mapped (xdotool search --pid: "Position: 900,300 Geometry: 102x87");
none appeared in a 3280x1080 screenshot (full-image tesseract). Kate — a managed Wayland
client started the same way — is visible, so the fault is in the popup, not in spawning windows.
Foreground runs with stderr captured: <paste exit codes and any errors here>.

Consequence: notifications (org.freedesktop.Notifications, already verified on the session bus)
remain the shipping delivery. Re-open only with a new mechanism, not a new hint.
```

**Step 4: Commit**

```bash
git add docs/market/ambient-ui-notes.md
git commit -m "docs(market): record what the popup experiments actually showed"
```

---

### Task 9: Read what `/v2/stats` already returns, before planning any readability work

**Objective:** The readability-report gap may be an exposure problem, not a build problem (DRY).

**Files:**
- Modify: `/home/chethan/Documents/Default Project/grammar-server/docs/market/feature-matrix.csv`

**Step 1: Inspect the endpoint's real output**

```bash
curl -s -XPOST http://127.0.0.1:8875/v2/stats -H 'Content-Type: application/json' -d '{
 "text":"The quick brown fox jumps over the lazy dog. However, it was a very nice day, and the fox was very happy indeed. In order to improve the report, we should consider revising it."}' \
 | python3 -m json.tool | head -40
```
Expected: real JSON. Note every metric it already returns (word counts, readability, passive voice,
repeated words, sentence lengths — whatever is actually there).

**Step 2: Map ProWritingAid's report list onto what already exists**

```bash
grep -rn "passive\|readab\|flesch\|adverb\|sticky" "/home/chethan/Documents/Default Project/grammar-server/internal" | head -20
```
Expected: at least one hit per metric already implemented. Anything ProWritingAid offers that this returns
becomes `extend` (UI work) instead of `build` (engine work) — that difference is the whole value of this task.

**Step 3: Update the `style-readability-reports` rows for the baseline with the real command and output**

**Step 4: Commit**

```bash
git add docs/market/feature-matrix.csv
git commit -m "docs(market): what /v2/stats already covers, so reports become an exposure task"
```

---

## Pass C — decisions, deliverable, handoff

### Task 10: Fill the decision column

**Objective:** Every gap gets `build | extend | skip` plus a one-line reason. No blank cells.

**Files:**
- Modify: `/home/chethan/Documents/Default Project/grammar-server/docs/market/feature-matrix.csv`

**Step 1: Apply the ranking rule**

```markdown
Rank by (value ÷ effort), where:
- value: 3 = every competitor charges for it and a writer notices the absence; 1 = specialist niche.
- effort: 1 = configuration/no new code (e.g. pointing LibreOffice at the local engine);
          2 = pure local computation (statistics, consistency);
          3 = needs an LLM pass on CPU (tone, paragraph rewrite) → on-demand only, never live.
Anything requiring the network, or the dGPU: skip, with the reason written down.
```

**Step 2: Verify no blank decisions remain**

```bash
python3 -c "
import csv
rows=list(csv.DictReader(open('/home/chethan/Documents/Default Project/grammar-server/docs/market/feature-matrix.csv')))
blank=[r for r in rows if r['local_status']!='have' and not r['decision']]
print('gaps without a decision:', len(blank))
print('counts:', {d: sum(1 for r in rows if r['decision']==d) for d in ('build','extend','skip')})"
```
Expected: `gaps without a decision: 0` and a non-zero count for at least `extend`.

**Step 3: Commit**

```bash
git add docs/market/feature-matrix.csv
git commit -m "docs(market): decision per gap with the value/effort rule"
```

---

### Task 11: Ship the top-ranked item end-to-end (LibreOffice's own grammar checker → local engine)

**Objective:** The single highest value ÷ effort item: LibreOffice 26.8 has a built-in LanguageTool server
field, and our engine already speaks the `/v2/check` contract, so this should be configuration plus proof.
Doing it here establishes the template for every later build item: change, then prove, then record.

**Files:**
- Create: `/home/chethan/Documents/Default Project/grammar-server/examples/lo-grammar-server-check.sh`
- Modify: `/home/chethan/Documents/Default Project/grammar-server/README.md` (a "Use it inside LibreOffice" section)

**Step 1: Find where LibreOffice stores the setting, by setting it once by hand**

In LibreOffice: **Tools ▸ Options ▸ Language Settings ▸ LanguageTool**, tick "Check text in the
LanguageTool Server" (wording may differ) and set the base URL to `http://127.0.0.1:8875`. Then:

```bash
grep -n -i -A3 -B3 'languagetool' ~/.config/libreoffice/4/user/registrymodifications.xcu | head -40
```
Expected: the exact property name and value (record it verbatim in the script's comments — that is the
whole point of this step; do not guess the key).

**Step 2: Write the check script around that key**

```bash
#!/usr/bin/env bash
# Proves LibreOffice is pointing its own grammar checker at the local engine.
set -uo pipefail
xcud=~/.config/libreoffice/4/user/registrymodifications.xcu
expected=http://127.0.0.1:8875
if grep -qi "$expected" "$xcud"; then
  echo "PASS: LibreOffice has the local engine configured"
else
  echo "FAIL: $expected not found in $xcud"
  echo "Set it in Tools > Options > Language Settings > LanguageTool, then re-run."
  exit 1
fi
curl -sf -m 5 http://127.0.0.1:8875/status >/dev/null && echo "PASS: engine reachable" || { echo "FAIL: engine down"; exit 1; }
```

**Step 3: Prove it visibly, not by configuration alone**

Type `She go to the office.` in LibreOffice Writer and screenshot:

```bash
sleep 3; spectacle -b -n -f -o /tmp/lo-check.png
magick /tmp/lo-check.png -crop 1400x400+300+300 +repage /tmp/lo-crop.png
tesseract /tmp/lo-crop.png stdout 2>/dev/null | head -5
```
Expected: the sentence appears; take a second screenshot 3 seconds later and confirm the underline/squiggle
is present (compare crops — a squiggle changes pixels under the word, the text does not move):

```bash
compare -metric AE /tmp/lo-crop.png /tmp/lo-crop2.png null: 2>&1; echo
```
Expected: a non-zero pixel difference count. Zero means nothing was marked — record that honestly as a
negative result rather than claiming success.

**Step 4: Document it in the README** with the exact UI path and the one-line revert (unset the URL).

**Step 5: Commit**

```bash
git add examples/lo-grammar-server-check.sh README.md
git commit -m "docs: point LibreOffice's own grammar checker at the local engine, with proof"
```

---

### Task 12: Render the `.odt` deliverable, commit, and record the outcome

**Objective:** Ship the document in the house format (`.odt` with a clickable outline), and leave the
findings where the next session will find them.

**Files:**
- Create: `/home/chethan/Documents/Default Project/grammar-server/docs/market/grammar-checkers-2026.odt`

**Step 1: Build the `.odt`** using the `libreoffice-doc-pipeline` skill: one-line TOC, bookmarks for every
section, collapsible detail not available in `.odt` so keep it terse — the CSV is the detail.

Content sections, in order: What the market does / Feature matrix (one table, fed from the CSV) /
What is verifiably local / Ranked build list / Deliberately skipped, with reasons.

**Step 2: Verify it is a real document with an outline, not a renamed markdown**

```bash
f=/home/chethan/Documents/Default Project/grammar-server/docs/market/grammar-checkers-2026.odt
unzip -l "$f" | grep -E 'content.xml|styles.xml' ; \
unzip -p "$f" content.xml | grep -o 'text:bookmark' | wc -l
```
Expected: both XML parts listed, and a bookmark count of at least 5.

**Step 3: Render the first page and look at it**

```bash
libreoffice --headless --convert-to pdf --outdir /tmp "$f" >/dev/null 2>&1
pdftoppm -png -r 80 -f 1 -l 1 /tmp/grammar-checkers-2026.pdf /tmp/doc-page
tesseract /tmp/doc-page-1.png stdout 2>/dev/null | head -12
```
Expected: the title and the first section heading are readable in the OCR — proof the document is not blank.

**Step 4: Commit and push both repos** (the CSV/doc/scripts in the core repo; nothing changes in the UI
repo unless Task 8 concluded "wire the popup")

```bash
cd "/home/chethan/Documents/Default Project/grammar-server"
go test ./... 2>&1 | tail -3
git add -A && git commit -m "docs(market): feature map and ranked build list" && git push
GH_TOKEN=$(grep -m1 '^GITHUB_TOKEN=' ~/.hermes/.env | cut -d= -f2-) gh run watch --repo chethan62/grammar-server \
  "$(gh run list --repo chethan62/grammar-server --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status
```
Expected: `go test` all packages ok, push succeeds, CI concludes success.

**Step 5: Update the `grammar-server` skill** with the durable lessons: the four measured claims, the
LibreOffice setting key, and the popup finding.

---

## Files likely to change

| Path | Repo | Why |
|---|---|---|
| `docs/market/feature-matrix.csv` | grammar-server | the source of truth (created) |
| `docs/market/README.md` | grammar-server | capability vocabulary + evidence rule (created) |
| `docs/market/ambient-ui-notes.md` | grammar-server | the popup finding (created) |
| `docs/market/grammar-checkers-2026.odt` | grammar-server | the deliverable (created) |
| `examples/market-claims-check.sh` | grammar-server | verifies the four roadmap claims (created) |
| `examples/lo-grammar-server-check.sh` | grammar-server | proves the LibreOffice wiring (created) |
| `README.md` | grammar-server | the LibreOffice section (modified) |
| `desktop/grammar-watch.py` | grammar-ui | only if Task 8 concludes "wire the popup" |

## Tests / validation

- Every research task ends with a command whose expected output is written above; a task is done when the
  command prints exactly that.
- `examples/market-claims-check.sh` is the regression gate for the four claims — it exits non-zero if the
  LT client contract breaks, if a character cap appears, or if `/v2/rewrite` stops answering.
- `go test ./...` must be green before the final commit (`internal/api/limits_test.go` already pins the
  LT `/v2/languages` shape, so a compatibility regression fails there first).
- Task 11's proof is a pixel diff, not a config read: a populated config that marks nothing is a failure.
- Task 12's proof is a bookmark count plus OCR of a rendered page, not the file's existence.

## Risks, tradeoffs, and open questions

1. **Vendor pages are marketing and they change.** Two sources reduce the error; neither eliminates it.
   Mitigation: the `verified_how` column must say *how* at least one claim in each row was checked.
2. **Timing measurements on this box are unreliable while it is hot.** The CPU drops to roughly 2.3×
   slower at ~94 °C. Task 7's rephrase latency is only meaningful below ~80 °C; record the temperature
   with the number or the number is noise.
3. **The popup question is still open — the earlier answer was not evidence.** Every X11 variant ran and
   was mapped but never appeared in a screenshot; the Wayland `POPUP_MENU` variant never ran at all (the
   unquoted path), so nothing is known about it yet. If Task 8 confirms a popup cannot be shown, the honest
   outcome is: notifications are the delivery mechanism, and "next to the caret" is a KWin-script or
   compositor problem, not an application problem. Do not spend more time on window hints without a fresh
   reason.
4. **Plagiarism and generative drafting stay out of scope** — the first needs the network and was already
   refused; the second is a different product (drafting, not checking).
5. **Tone is a latency decision, not a capability decision.** Offline LLM tone is possible; per-keystroke
   tone on this CPU is not. Live tone sliders would be worse than useless; an explicit "rewrite this
   sentence" action is fine.
6. **Open question:** does LibreOffice's LanguageTool integration require a specific LT API version or
   server behaviour beyond `/v2/check`? Precedent for optimism: an earlier missing `longCode` field in
   `/v2/languages` broke a real LT client with a 200 response, and it was fixed in `9e2a0f9` and pinned in
   `limits_test.go` — so plan for one more such field-level surprise, and treat Task 11 Step 3's pixel
   diff as the real answer.
7. **Open question:** should the `.odt` live in the core repo or a third docs repo? Plan assumes core,
   since that is where the CSV lives.
