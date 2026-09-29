# Low-Latency Architecture and Future-Proofing Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Give the grammar checker a measured latency budget, fix what the measurements show is actually
slow on the live-typing path, and pin the architectural rules that keep it fast as features land.

**Architecture:** The system is already layered correctly and should stay that way: `internal/engine`
(talks LSP to a persistent `harper-ls`) ← `internal/api` (HTTP, LanguageTool-compatible) ← clients (Go
binary, static JS UI, Python desktop clients). The only door between layers is the HTTP API, so clients can
never reach into the engine. The work is therefore: instrument the layers, measure, then fix the top cost —
and enforce the direction with a test so it cannot rot.

**Tech Stack:** Go 1.26 (stdlib + `gopkg.in/yaml.v3`, nothing else), Python 3 stdlib for probes/clients,
`hyperfine`-free timing with `time`, `go test -bench`, GTK/AT-SPI for the desktop client.

---

## Current context / assumptions

**Repo:** `/home/chethan/Documents/Default Project/grammar-server` (engine + API), with the desktop/UI
clients in `/home/chethan/Documents/Default Project/grammar-ui`.

**What the code actually does today (read from the source, with line references):**
- `internal/engine/harper.go:35` — `Harper` wraps **one persistent `harper-ls --stdio` process**, used as a
  language server through `internal/lsp/client.go`. No process spawn per request. Good.
- `internal/engine/harper.go:42` — `mu sync.Mutex` serializes **whole check cycles** (`didOpen` →
  `publishDiagnostics` → `codeAction`). The comment states why: concurrent checks race on the shared
  `Notifications` channel — a waiter accepts diagnostics only for its own URI and drops every other publish,
  so the owner of a dropped publish **blocks until timeout and returns zero matches**. This is a correctness
  constraint. Do not delete the mutex; it is load-bearing.
- `internal/engine/harper.go:~58` — `diagnosticsTimeout = 10 * time.Second`, with the comment that harper
  answers in milliseconds. A dropped publish therefore costs up to 10 s.
- `internal/api/handler.go:588` — per-request logging with `time.Since`, but **no `Server-Timing` header**
  and no per-phase timing in production. `internal/engine/phase_probe_test.go` already measures the
  lint/enrich split **in a test**, so the shapes of those phases are known.
- `internal/api/limits_test.go` — a length cap exists (before it, a 200 KB document went straight to
  harper-ls), and `internal/api/chunk.go` chunks long texts. Long inputs are therefore *chunked*, and each
  chunk still queues behind the single harper.
- There are **no benchmarks** anywhere (`find . -name '*_bench_test.go'` returns nothing).
- `grammar-ui/desktop/grammar-lookup.py:66` — `urllib.request.urlopen(req)`, i.e. **a fresh connection per
  check**. The watcher imports this module, so it inherits that.
- `grammar-ui/desktop/grammar-watch.py` — `DEBOUNCE_MS = 1200` after the last text change, plus a
  `POLL_MS = 1500` safety poll. The pop-up is a **separate process per suggestion** (GTK init cost).
- `go.mod` — `module grammar-server`, `go 1.26.5`, requiring only `gopkg.in/yaml.v3`. Keep it that way.

**The latency budget this plan targets** (to be confirmed by Task 3's baseline, then written into
`docs/architecture.md`):

| stage | today (unmeasured) | target |
|---|---|---|
| a11y read of the caret window (AT-SPI, DBus) | unknown | < 20 ms p95 |
| `POST /v2/check`, 40–200 char window | unmeasured | < 30 ms p95, < 60 ms p99 |
| `POST /v2/check`, full paragraph (~1 KB) | unmeasured | < 60 ms p95 |
| `POST /v2/check`, 200 KB document (chunked) | unmeasured | < 2 s, and must not delay a small check |
| HTTP round trip, localhost | unmeasured | < 3 ms p95 with a reused connection |
| our debounce before asking | 1200 ms fixed | 300–600 ms adaptive, engine-aware |
| pop-up process start → visible | unknown | < 200 ms, or say it is slower |
| **perceived: typing pause → suggestion visible** | ~1.3 s+ | **< 600 ms p95** |

**Measurement hygiene (non-negotiable on this machine):** the CPU is power-capped when hot — the same
benchmark reads up to ~2.3× slower at ~94 °C than at ~55 °C. Every timing task must print package
temperature **and** load average next to the numbers, and re-run if the box is above ~80 °C. A timing
without its temperature is noise.

---

## Proposed approach

Measure the three layers separately before changing any of them: a Python end-to-end probe
(`examples/latency-probe.py`) for what a user feels, and Go benchmarks for what the server does without the
network. Then fix in measured-cost order — which the source already suggests will be (1) our own fixed
1200 ms debounce, (2) connection-per-check on the client, (3) head-of-line blocking behind the single
serialized harper for concurrent clients, and (4) turning the 10 s timeout into an *error* rather than a
silent "no suggestions". Two architectural rules get tests, not good intentions: the engine never imports
the API layer, and no LLM call ever runs on the check path.

---

## Phase 0 — instrument and measure (nothing gets optimized before this)

### Task 1: Record the machine's state, so later numbers can be trusted

**Objective:** Establish the thermal/load context that every subsequent timing is only valid within.

**Files:**
- Create: `/home/chethan/Documents/Default Project/grammar-server/docs/perf/machine-state.md`

**Step 1: Capture state**

```bash
cd "/home/chethan/Documents/Default Project/grammar-server"
mkdir -p docs/perf
{
  echo "# Machine state"
  echo
  echo "Date: $(date -Iseconds)"
  echo "Kernel: $(uname -r)"
  echo "CPU: $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ //')"
  echo "Cores: $(nproc)"
  echo "Load: $(cat /proc/loadavg)"
  echo "Temps: $(for z in /sys/class/thermal/thermal_zone*/temp; do printf '%s ' "$(cat "$z" 2>/dev/null)"; done)"
  echo "Gov: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null)"
  echo "dGPU: $(nvidia-smi --query-gpu=clocks.sm,power.draw --format=csv,noheader 2>/dev/null || echo 'n/a')"
} | tee docs/perf/machine-state.md
```
Expected: a file with every field filled. Temps are millidegrees (e.g. `55000` = 55 °C).

**Step 2: Commit**

```bash
git add docs/perf/machine-state.md
git commit -m "docs(perf): record the machine state all timings are relative to"
```

---

### Task 2: Write the end-to-end latency probe (TDD-ish: the probe must fail loudly when the engine is down)

**Objective:** One command that reports percentiles per input size, so no claim about speed is a guess.

**Files:**
- Create: `/home/chethan/Documents/Default Project/grammar-server/examples/latency-probe.py`

**Step 1: Write the probe**

```python
#!/usr/bin/env python3
"""Latency probe for the local engine: percentiles per input size, plus machine state.

    python3 examples/latency-probe.py                 # default sizes, 20 runs each
    python3 examples/latency-probe.py --runs 50 --sizes 40,200,1000,10000,200000
    python3 examples/latency-probe.py --concurrent 4  # head-of-line-blocking check

Prints a table and writes JSON for the record. Exits 1 if the engine is unreachable, because a probe
that silently measures nothing is worse than no probe.
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SENTENCE = "She go to the office and their going to fix it tomorrow, in order to be sure."


def text_of(size):
    """Repeat the sentence (which contains real errors) until it is about `size` characters."""
    body = (SENTENCE + " ")
    reps = max(1, size // len(body))
    return (body * reps)[:size]


def one_check(api, size, language="en-US"):
    payload = json.dumps({"text": text_of(size), "language": language}).encode()
    req = urllib.request.Request(api + "/v2/check", data=payload,
                                 headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    return (time.perf_counter() - start) * 1000.0, len(data.get("matches", []))


def percentile(values, p):
    values = sorted(values)
    idx = min(len(values) - 1, int(round((p / 100.0) * (len(values) - 1))))
    return values[idx]


def machine_state():
    temps = []
    for zone in sorted(__import__("glob").glob("/sys/class/thermal/thermal_zone*/temp")):
        try:
            temps.append(int(open(zone).read().strip()) // 1000)
        except Exception:
            pass
    return {"load": open("/proc/loadavg").read().split()[:3], "temps_c": temps}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("GRAMMAR_API", "http://127.0.0.1:8875"))
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--sizes", default="40,200,1000,10000,200000")
    ap.add_argument("--concurrent", type=int, default=1)
    ap.add_argument("--out", default="docs/perf/latest.json")
    args = ap.parse_args()

    try:
        with urllib.request.urlopen(args.api + "/status", timeout=5) as resp:
            json.load(resp)
    except (urllib.error.URLError, OSError) as exc:
        print("engine unreachable at %s: %s" % (args.api, exc), file=sys.stderr)
        return 1

    state = machine_state()
    print("machine: load=%s temps=%s C" % (state["load"], state["temps_c"]))
    print("%-10s %-6s %8s %8s %8s %9s" % ("size", "runs", "p50 ms", "p95 ms", "max ms", "matches"))
    results = {}
    for size in [int(s) for s in args.sizes.split(",")]:
        if args.concurrent > 1:
            with ThreadPoolExecutor(max_workers=args.concurrent) as pool:
                rows = list(pool.map(lambda _: one_check(args.api, size), range(args.runs)))
        else:
            rows = [one_check(args.api, size) for _ in range(args.runs)]
        times = [r[0] for r in rows]
        results[str(size)] = {"p50": statistics.median(times), "p95": percentile(times, 95),
                              "max": max(times), "matches": rows[0][1]}
        print("%-10d %-6d %8.1f %8.1f %8.1f %9d"
              % (size, args.runs, results[str(size)]["p50"], results[str(size)]["p95"],
                 results[str(size)]["max"], rows[0][1]))

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump({"api": args.api, "runs": args.runs, "concurrent": args.concurrent,
                   "machine": state, "results": results}, fh, indent=2)
    print("wrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Step 2: Verify it fails when it should**

```bash
cd "/home/chethan/Documents/Default Project/grammar-server"
python3 examples/latency-probe.py --api http://127.0.0.1:1 ; echo "exit=$?"
```
Expected: `engine unreachable at http://127.0.0.1:1: ...` and `exit=1`.

**Step 3: Verify it works**

```bash
python3 examples/latency-probe.py --runs 10 --sizes 40,200,1000
```
Expected: three table rows with p50/p95/max in milliseconds, `wrote docs/perf/latest.json`, and the machine
line printed first.

**Step 4: Commit**

```bash
git add examples/latency-probe.py
git commit -m "feat(perf): latency probe with percentiles, machine state and a loud failure mode"
```

---

### Task 3: Take the baseline, cool, with the temperature written down

**Objective:** The number every later claim is measured against.

**Files:**
- Create: `/home/chethan/Documents/Default Project/grammar-server/docs/perf/baseline-2026-09-29.json`

**Step 1: Check the box is cool, then measure**

```bash
cd "/home/chethan/Documents/Default Project/grammar-server"
uptime; for z in /sys/class/thermal/thermal_zone*/temp; do printf '%sC ' $(( $(cat $z) / 1000 )); done; echo
```
If any package temperature is above ~80 °C, wait (idle) and re-check. Do not record timings above 80 °C.

**Step 2: Run the baseline and keep it**

```bash
python3 examples/latency-probe.py --runs 20 --sizes 40,200,1000,10000,200000 --out docs/perf/baseline-2026-09-29.json
```
Expected: five rows; the 200000 row is the chunked path and is expected to be the slowest. Copy the printed
machine line into the JSON commit message.

**Step 3: Commit**

```bash
git add docs/perf/baseline-2026-09-29.json
git commit -m "docs(perf): baseline latency at <TEMP>C, <LOAD> load"
```

---

### Task 4: Instrument the server so the client can see where the time goes (TDD)

**Objective:** A machine-readable split between the API's own overhead and the harper cycle, forever.

**Files:**
- Modify: `/home/chethan/Documents/Default Project/grammar-server/internal/api/handler.go`
- Test: `/home/chethan/Documents/Default Project/grammar-server/internal/api/handler_test.go`

**Step 1: Write the failing test**

```go
func TestCheckReportsServerTiming(t *testing.T) {
	// The client and the probe need to know how much of a request was harper and how much was us.
	// Without this header every latency number is a single opaque total.
	rec := doCheck(t, `{"text":"She go to the office.","language":"en-US"}`)
	if rec.Code != 200 {
		t.Fatalf("status = %d", rec.Code)
	}
	timing := rec.Header().Get("Server-Timing")
	if !strings.Contains(timing, "harper;dur=") || !strings.Contains(timing, "total;dur=") {
		t.Fatalf("Server-Timing = %q, want harper;dur=... and total;dur=...", timing)
	}
}
```
Adapt `doCheck` / the recorder helper to whatever `handler_test.go` already uses — do not invent a second
harness (DRY). If there is no such helper, build the request with `httptest.NewRecorder()` inline.

**Step 2: Run it to verify it fails**

```bash
cd "/home/chethan/Documents/Default Project/grammar-server"
go test ./internal/api/ -run TestCheckReportsServerTiming -v 2>&1 | tail -5
```
Expected: FAIL — `Server-Timing = ""`.

**Step 3: Implement minimally**

In the check handler, time the engine call and set the header before writing the body:

```go
	engineStart := time.Now()
	matches, err := h.engine.Check(text, opts)
	engineMs := float64(time.Since(engineStart).Microseconds()) / 1000.0
	// Server-Timing is the standard place for this and costs one header; it is what lets the
	// latency probe separate harper from our own overhead without a profiler.
	w.Header().Set("Server-Timing", fmt.Sprintf("harper;dur=%.1f, total;dur=%.1f", engineMs, totalMs))
```
`totalMs` is `time.Since(requestStart)` where `requestStart` is captured at the top of the handler. Imports
are `fmt` and `time`; both are almost certainly already present.

**Step 4: Run to verify pass**

```bash
go test ./internal/api/ -run TestCheckReportsServerTiming -v 2>&1 | tail -3
curl -s -D- -o /dev/null -XPOST localhost:8875/v2/check -H 'Content-Type: application/json' \
  -d '{"text":"She go to the office.","language":"en-US"}' | grep -i server-timing
```
Expected: `--- PASS`, then a header line like `server-timing: harper;dur=12.3, total;dur=12.9`.

**Step 5: Commit**

```bash
git add internal/api/handler.go internal/api/handler_test.go
git commit -m "feat(api): Server-Timing on /v2/check so latency is attributable"
```

---

## Phase 1 — server-side measurements and the concurrency decision

### Task 5: Add Go benchmarks, including a parallel one

**Objective:** Measure the server without the network, and quantify the single-harper serialization.

**Files:**
- Create: `/home/chethan/Documents/Default Project/grammar-server/internal/api/bench_test.go`

**Step 1: Write the benchmarks**

```go
package api

import "testing"

func benchText(n int) string {
	sentence := "She go to the office and their going to fix it tomorrow. "
	out := ""
	for len(out) < n {
		out += sentence
	}
	return out[:n]
}

func BenchmarkCheck40(b *testing.B) {
	h := liveTestHandler(b) // reuse the existing test constructor; skip when harper is absent
	body := checkBody(benchText(40))
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		h.Check(body)
	}
}

func BenchmarkCheckParagraph(b *testing.B) { /* same shape, benchText(1000) */ }

func BenchmarkCheck200k(b *testing.B) { /* same shape, benchText(200000) */ }

func BenchmarkCheckParallel(b *testing.B) {
	// This is the one that matters: one serialized harper means a big check blocks small ones.
	h := liveTestHandler(b)
	small, big := checkBody(benchText(40)), checkBody(benchText(200000))
	b.ResetTimer()
	b.RunParallel(func(pb *testing.PB) {
		i := 0
		for pb.Next() {
			if i%10 == 0 {
				h.Check(big)
			} else {
				h.Check(small)
			}
			i++
		}
	})
}
```
`liveTestHandler` and `checkBody` stand for whatever the existing tests use (see
`internal/api/rewrite_test.go:21` for the `engine.NewHarper("harper-ls", ...)` + `t.Skipf` pattern) — reuse
it, do not add a parallel harness.

**Step 2: Run them**

```bash
cd "/home/chethan/Documents/Default Project/grammar-server"
go test ./internal/api/ -run '^$' -bench . -benchtime 2s -benchmem 2>&1 | tee /tmp/bench.txt | tail -12
```
Expected: four `Benchmark…` lines with `ns/op`. Record them in `docs/perf/baseline-2026-09-29.json` under a
`"go_bench"` key.

**Step 3: Commit**

```bash
git add internal/api/bench_test.go docs/perf/baseline-2026-09-29.json
git commit -m "test(perf): benchmarks including a parallel mix of small and 200k checks"
```

---

### Task 6: Decide the harper pool by measurement, and write the decision down

**Objective:** If a 200 KB check blocks a 40-char one, that is the single biggest latency risk for live
typing — and the only correct fix is more harper processes, not a removed mutex.

**Files:**
- Modify: `/home/chethan/Documents/Default Project/grammar-server/internal/engine/harper.go` (only if needed)
- Create: `/home/chethan/Documents/Default Project/grammar-server/docs/perf/pool-decision.md`

**Step 1: Measure head-of-line blocking end to end**

```bash
cd "/home/chethan/Documents/Default Project/grammar-server"
systemctl --user restart grammar-server 2>/dev/null || true; sleep 2
python3 examples/latency-probe.py --runs 20 --sizes 40 --concurrent 1 --out /tmp/hol-1.json
python3 examples/latency-probe.py --runs 20 --sizes 40,200000 --concurrent 2 --out /tmp/hol-2.json
python3 - <<'PY'
import json
a = json.load(open("/tmp/hol-1.json"))["results"]["40"]
b = json.load(open("/tmp/hol-2.json"))["results"]["40"]
print("small check alone:      p50 %.0f ms  p95 %.0f ms" % (a["p50"], a["p95"]))
print("small check beside 200k: p50 %.0f ms  p95 %.0f ms" % (b["p50"], b["p95"]))
PY
```
Expected: two lines of numbers. **Decision rule:** if the small check's p95 rises by more than ~3× (or above
100 ms) when a 200 KB check is in flight, implement the pool.

**Step 2: If the rule fires, implement a small pool**

In `internal/engine/harper.go`, keep `Harper` as-is (one process, one mutex) and add a pool that picks the
least-busy member; each member keeps its own LSP client and therefore its own `Notifications` channel —
which is exactly what the existing mutex comment says is required for correctness:

```go
// Pool is N independent harper processes. Concurrency needs N processes, not a removed mutex: the
// mutex exists because each waiter only accepts diagnostics for its own URI, so two checks sharing
// one LSP channel drop each other's publishes and one of them returns zero matches.
//
// ponytail: N is fixed at construction; make it dynamic only if per-request queueing shows up in
// measurements again.
type Pool struct {
	members []*Harper
	next    uint64
}

func NewPool(size int, bin, dialect string, cfg json.RawMessage) (*Pool, error) {
	if size < 1 {
		size = 1
	}
	p := &Pool{}
	for i := 0; i < size; i++ {
		h, err := NewHarper(bin, dialect, cfg)
		if err != nil {
			return nil, err
		}
		p.members = append(p.members, h)
	}
	return p, nil
}

// Check round-robins. ponytail: round-robin, not least-loaded — it is one line and the pool is small;
// switch to least-loaded if the mix is ever wildly uneven.
func (p *Pool) Check(text string, opts Options) ([]Match, error) {
	i := atomic.AddUint64(&p.next, 1)
	return p.members[i%uint64(len(p.members))].Check(text, opts)
}
```
Size it from `runtime.NumCPU()/2` clamped to 2..4 at the call site, and log the chosen size at startup.

**Step 3: Re-run Step 1's measurement and compare**

Expected: the small check's p95 beside a 200 KB check returns to within ~2× of its solo number. Write both
numbers into `docs/perf/pool-decision.md`, or — if the rule did *not* fire in Step 1 — write that down
instead, with the numbers, and make no code change.

**Step 4: Commit**

```bash
git add internal/engine/harper.go internal/engine/harper_test.go docs/perf/pool-decision.md
git commit -m "perf(engine): N harper processes for concurrency, sized and decided from measurement"
```

---

### Task 7: Stop reporting a timeout as "no suggestions" (TDD)

**Objective:** A dropped publish currently costs 10 s and then returns an empty match list, which every
client reads as "your text is clean". That is a wrong answer, not a slow answer.

**Files:**
- Modify: `/home/chethan/Documents/Default Project/grammar-server/internal/engine/harper.go`
- Test: `/home/chethan/Documents/Default Project/grammar-server/internal/engine/harper_test.go`

**Step 1: Write the failing test** — make the timeout injectable so the test does not need to wait 10 s:

```go
func TestCheckTimeoutIsAnErrorNotAnEmptyResult(t *testing.T) {
	// A missing diagnostics publish used to come back as zero matches, which reads as "text is
	// clean". Silence and success must not be the same answer.
	h := newTestHarper(t, "harper-ls")
	h.diagnosticsTimeout = time.Nanosecond // force the timeout path
	matches, err := h.Check("She go to the office, and their going to fix it.", Options{})
	if err == nil {
		t.Fatalf("got %d matches and no error; a timeout must be reported", len(matches))
	}
	if !strings.Contains(err.Error(), "diagnostic") {
		t.Fatalf("error = %v, want it to name the missing diagnostics publish", err)
	}
}
```
If `diagnosticsTimeout` is currently a `const`, change it to a struct field **first** (a const cannot be
injected) — that is part of this task.

**Step 2: Run it to verify it fails**

```bash
go test ./internal/engine/ -run TestCheckTimeoutIsAnErrorNotAnEmptyResult -v 2>&1 | tail -5
```
Expected: FAIL — `got 0 matches and no error; a timeout must be reported`.

**Step 3: Implement minimally** — on the wait-for-diagnostics timeout, return a wrapped error instead of the
empty slice, naming the URI and the timeout in the message.

**Step 4: Run to verify pass, and that nothing else broke**

```bash
go test ./... 2>&1 | tail -6
```
Expected: the new test PASSes and every existing package still reports `ok`. If an existing test asserted
"empty on timeout", it was encoding the bug — fix the test and say so in the commit message.

**Step 5: Commit**

```bash
git add internal/engine/harper.go internal/engine/harper_test.go
git commit -m "fix(engine): a diagnostics timeout is an error, not an empty match list

Zero matches means 'clean' to every client, so a dropped publish was reported as a clean
document after a 10 s wait. The timeout is now injectable and the failure is reported."
```

---

## Phase 2 — client-side latency, where the user actually waits

### Task 8: Make the desktop client reuse one connection (TDD)

**Objective:** `urlopen` per check means a fresh TCP connection per suggestion. Locally that is small but
free to remove — and it becomes real over the LAN, which is a supported deployment.

**Files:**
- Modify: `/home/chethan/Documents/Default Project/grammar-ui/desktop/grammar-lookup.py`
- Test: `/home/chethan/Documents/Default Project/grammar-ui/desktop/test-lookup.py`

**Step 1: Write the failing test** — count connections at a stub server:

```python
def test_check_reuses_one_connection():
    """A fresh connection per check is wasted work on every keystroke-pause; count them."""
    import http.server, threading, socketserver
    seen = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(self.client_address[1])          # a new source port means a new connection
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = b'{"matches":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    api, saved = "http://127.0.0.1:%d" % srv.server_address[1], lookup.API
    lookup.API = api
    try:
        lookup.check("one")
        lookup.check("two")
    finally:
        lookup.API, _ = saved, srv.shutdown()
    assert len(set(seen)) == 1, "two checks used %d connections (%s)" % (len(set(seen)), seen)
```

**Step 2: Run it to verify it fails**

```bash
cd "/home/chethan/Documents/Default Project/grammar-ui"
/usr/bin/python3 desktop/test-lookup.py 2>&1 | grep -A2 AssertionError | head -5
```
Expected: FAIL — `two checks used 2 connections ([…])`.

**Step 3: Implement minimally** — keep one `http.client.HTTPConnection` per host, recreate it on error:

```python
_CONNECTIONS = {}


def _post(path, body):
    """One keep-alive connection per host (stdlib http.client, no new dependency). urlopen opened a
    fresh TCP connection for every check, which costs real milliseconds over the LAN."""
    from urllib.parse import urlparse
    url = urlparse(API)
    key = (url.hostname, url.port or 80)
    conn = _CONNECTIONS.get(key)
    for attempt in (0, 1):          # second attempt after a stale keep-alive socket
        try:
            if conn is None:
                conn = http.client.HTTPConnection(*key, timeout=30)
                _CONNECTIONS[key] = conn
            conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
            return json.load(conn.getresponse())
        except (http.client.HTTPException, OSError):
            try:
                conn.close()
            except Exception:
                pass
            _CONNECTIONS.pop(key, None)
            conn = None
            if attempt:
                raise
    raise RuntimeError("unreachable")
```
Point `check()` at `_post(...)`, keep `urllib.error.HTTPError` handling equivalent to today's
(`urllib.error.URLError` is caught by callers — return the same exception types so callers keep working),
and add `import http.client`.

**Step 4: Run to verify pass, plus the whole gate**

```bash
/usr/bin/python3 desktop/test-lookup.py 2>&1 | tail -3
make test 2>&1 | tail -4
```
Expected: the new test passes and `grammar-watch: … assertions - passed` unchanged.

**Step 5: Commit**

```bash
git add desktop/grammar-lookup.py desktop/test-lookup.py
git commit -m "perf(client): keep one HTTP connection instead of dialling per check"
```

---

### Task 9: Make the debounce adaptive so perceived latency drops (TDD)

**Objective:** Our own 1200 ms timer dominates what a user feels whenever the engine is fast. Make the wait
a function of the last measured engine time, not a constant.

**Files:**
- Modify: `/home/chethan/Documents/Default Project/grammar-ui/desktop/grammar-watch.py`
- Test: `/home/chethan/Documents/Default Project/grammar-ui/desktop/test-watch.py`

**Step 1: Write the failing test** (pure function, no a11y needed):

```python
def test_debounce_adapts_to_the_engine():
    """Fixed 1.2s is most of the perceived latency when the engine answers in 15ms."""
    ok(watch.debounce_ms(None) == 600, "no measurement yet: a middle setting: %r" % watch.debounce_ms(None))
    ok(watch.debounce_ms(15) == 300, "fast engine: 300ms: %r" % watch.debounce_ms(15))
    ok(watch.debounce_ms(120) == 900, "slow engine: 900ms: %r" % watch.debounce_ms(120))
    ok(watch.debounce_ms(9000) == 1500, "very slow engine: the ceiling, 1500ms: %r" % watch.debounce_ms(9000))
```

**Step 2: Run it to verify it fails**

```bash
cd "/home/chethan/Documents/Default Project/grammar-ui"
/usr/bin/python3 desktop/test-watch.py 2>&1 | grep -A2 AssertionError | head -4
```
Expected: FAIL — `AttributeError: module 'grammar_watch' has no attribute 'debounce_ms'`.

**Step 3: Implement minimally**

```python
def debounce_ms(last_engine_ms):
    """How long to wait after the last keystroke before asking, given how fast the engine was.

    A fixed 1200ms was most of what the user felt while the engine answered in milliseconds. Bands
    rather than a formula so the number stays explainable in a journal line.
    """
    if last_engine_ms is None:
        return 600
    if last_engine_ms < 40:
        return 300
    if last_engine_ms < 250:
        return 900
    return 1500
```
Record the measured engine time in `check()` from the request (wall-clock around `self.client.check`) into
`self.last_engine_ms`, and use `debounce_ms(self.last_engine_ms)` in place of `DEBOUNCE_MS` where the timeout
is scheduled. Leave `POLL_MS` alone — it is a safety net, not the primary path, and shrinking it costs CPU
on every app.

**Step 4: Run to verify pass**

```bash
/usr/bin/python3 desktop/test-watch.py 2>&1 | tail -2
```
Expected: `grammar-watch: <N+4> assertions - passed`.

**Step 5: Commit**

```bash
git add desktop/grammar-watch.py desktop/test-watch.py
git commit -m "perf(watch): debounce adapts to the measured engine time (1.2s fixed was the bottleneck)"
```

---

### Task 10: Measure the pop-up's own start-up cost, and say so if it is slow

**Objective:** The pop-up is a new process per suggestion (GTK init). If that is over ~200 ms it belongs in
the budget, not in a surprise.

**Files:**
- Modify: `/home/chethan/Documents/Default Project/grammar-server/docs/perf/baseline-2026-09-29.json`
  (add a `"popup_start_ms"` key)

**Step 1: Time it, five times**

```bash
for i in 1 2 3 4 5; do
  /usr/bin/env python3 - <<'PY'
import subprocess, time, os
ui = "/home/chethan/Documents/Default Project/grammar-ui/desktop/grammar-popup.py"
start = time.perf_counter()
subprocess.run(["/usr/bin/python3", ui, "--x", "900", "--y", "300", "--label", "t", "--timeout", "1"],
               capture_output=True, timeout=30)
print("%.0f ms" % ((time.perf_counter() - start) * 1000))
PY
done
```
Expected: five millisecond values. Record the median.

**Step 2: Decide, in writing**

If the median is above 200 ms, note the upgrade path in `docs/architecture.md`: a single long-lived pop-up
process driven over a FIFO, marked `ponytail: one process per suggestion until start-up shows up in the
budget`. Do **not** build it yet — YAGNI until the number says otherwise.

**Step 3: Commit**

```bash
git add docs/perf/baseline-2026-09-29.json docs/architecture.md
git commit -m "docs(perf): pop-up start-up cost, and the upgrade path if it matters"
```

---

## Phase 3 — pin the architecture so it cannot rot

### Task 11: A test that the engine never depends on the API layer (TDD, with a proven failure)

**Objective:** The layering is the thing that keeps this future-proof; enforce it mechanically.

**Files:**
- Create: `/home/chethan/Documents/Default Project/grammar-server/internal/engine/layering_test.go`

**Step 1: Write the test**

```go
package engine

import (
	"os/exec"
	"strings"
	"testing"
)

func TestEngineDoesNotDependOnAPI(t *testing.T) {
	// Dependency direction: engine <- api <- clients. The engine is the reusable core; if it ever
	// imports the API layer the layering is gone and the engine can no longer be embedded elsewhere.
	// This catches test files too, which is how the poisoned run below proves it bites.
	out, err := exec.Command("go", "list", "-f", "{{join .Imports \"\\n\"}}", "./...").Output()
	if err != nil {
		t.Skipf("go list unavailable: %v", err)
	}
	for _, imp := range strings.Split(string(out), "\n") {
		imp = strings.TrimSpace(imp)
		if strings.HasSuffix(imp, "internal/api") {
			t.Fatalf("internal/engine imports %s — dependency direction is engine <- api", imp)
		}
	}
}
```

**Step 2: Prove it can fail** (this is the point of the task — an always-green guard is decoration)

```bash
cd "/home/chethan/Documents/Default Project/grammar-server"
cat > /tmp/poison.go <<'EOF'
package engine

import _ "grammar-server/internal/api"
EOF
cp /tmp/poison.go internal/engine/zz_poison_test.go
go test ./internal/engine/ -run TestEngineDoesNotDependOnAPI 2>&1 | tail -3
rm internal/engine/zz_poison_test.go
```
Expected: FAIL while the poison file exists, then the file is deleted and the next run PASSes. Record both
outputs in the commit message.

**Step 3: Verify clean**

```bash
go test ./internal/engine/ -run TestEngineDoesNotDependOnAPI -v 2>&1 | tail -3
```
Expected: `--- PASS`.

**Step 4: Commit**

```bash
git add internal/engine/layering_test.go
git commit -m "test(engine): enforce the dependency direction, with the poisoned run that proves it bites"
```

---

### Task 12: Write the architecture decisions down where the next person will look

**Objective:** The rules, the numbers, and the reasons — including what is deliberately not built.

**Files:**
- Create: `/home/chethan/Documents/Default Project/grammar-server/docs/architecture.md`

**Step 1: Write it with these sections, each with its evidence**

```markdown
# Architecture

## Layers
engine (LSP → one harper-ls process, or a pool) <- api (HTTP, LanguageTool-compatible) <- clients
(binary, static UI, python desktop clients). Enforced by internal/engine/layering_test.go.

## Why the engine's mutex is not a bug
One harper process, one LSP channel: each waiter accepts diagnostics only for its own URI, so two
concurrent checks on one process drop each other's publishes and one returns zero matches. Concurrency
needs N processes (internal/engine/harper.go Pool), not a removed mutex.

## Latency budget
<table filled from docs/perf/baseline-2026-09-29.json, with the machine temperature recorded beside it>

## Rules
1. stdlib + gopkg.in/yaml.v3 only. A new dependency needs a measured reason.
2. No LLM call on the check path. /v2/rewrite is on-demand and separately bounded; live tone/rewrite
   sliders would be worse than useless on a CPU-only box.
3. The HTTP API is the only door between layers, and it is LanguageTool-compatible (/v2/check), verified
   against a real third-party client (examples/lt-client-smoke.py).
4. Endpoints are versioned (/v2); a breaking change gets a new path, never a silent shape change.
5. No GPU code path anywhere: the dGPU is capped at 300 MHz and the policy is CPU-only.
6. A timeout is an error, never empty results. Zero matches means clean.
7. Measure cool: timings above ~80 °C are invalid on this machine.

## Deliberately not built
Plagiarism (needs the network), generative drafting, cloud sync, a per-keystroke tone pass.
```

**Step 2: Fill the budget table from the actual baseline JSON**

```bash
python3 -c "
import json
d=json.load(open('/home/chethan/Documents/Default Project/grammar-server/docs/perf/baseline-2026-09-29.json'))
print(d['machine']); [print(k, v) for k,v in d['results'].items()]"
```
Expected: the machine line and five rows; the numbers in your table must match them exactly.

**Step 3: Commit**

```bash
git add docs/architecture.md
git commit -m "docs: architecture, latency budget and the rules, each with its measurement"
```

---

### Task 13: Record the outcome in the skill and ship

**Objective:** The durable lessons land where the next session reads them.

**Files:**
- Modify: `~/.hermes/skills/grammar-server/SKILL.md`

**Step 1: Add the lessons**

```markdown
- **The engine's mutex is load-bearing.** One harper process = one LSP Notifications channel; two
  concurrent checks drop each other's diagnostics publishes and one returns zero matches. Concurrency
  means more processes, never a removed lock.
- **A timeout must never look like "clean".** Zero matches is the success shape, so every timeout must
  return an error; otherwise a dropped publish is reported as a clean document.
- **Fixed debounce was the dominant user-visible latency**, not the engine: 1200 ms of our own timer
  against an engine answering in tens of milliseconds. Measure the layer you control first.
- **Time cool.** The same benchmark reads up to ~2.3x slower above ~90 °C on this box; a timing without
  its temperature is not evidence.
```

**Step 2: Run every gate and push**

```bash
cd "/home/chethan/Documents/Default Project/grammar-server"
go test ./... 2>&1 | tail -4
TOKEN=$(grep -m1 '^GITHUB_TOKEN=' ~/.hermes/.env | cut -d= -f2-)
git push -q "https://x-access-token:$TOKEN@github.com/chethan62/grammar-server.git" main && echo pushed
cd "/home/chethan/Documents/Default Project/grammar-ui" && make test 2>&1 | tail -3
git push -q "https://x-access-token:$TOKEN@github.com/chethan62/grammar-ui.git" main && echo pushed-ui
```
Expected: `go test` all `ok`, `make test` green, two `pushed` lines.

**Step 3: Watch CI**

```bash
export GH_TOKEN=$TOKEN
for R in grammar-server grammar-ui; do
  gh run watch --repo chethan62/$R "$(gh run list --repo chethan62/$R --limit 1 --json databaseId --jq '.[0].databaseId')" --exit-status && echo "$R green"
done
```
Expected: `grammar-server green` and `grammar-ui green`.

---

## Files likely to change

| Path | Repo | Change |
|---|---|---|
| `examples/latency-probe.py` | core | new: percentiles per size, machine state, loud failure |
| `docs/perf/machine-state.md`, `baseline-2026-09-29.json`, `pool-decision.md` | core | new: the measurements of record |
| `docs/architecture.md` | core | new: layers, budget, rules, non-goals |
| `internal/api/handler.go` + `handler_test.go` | core | `Server-Timing` header |
| `internal/api/bench_test.go` | core | new: benchmarks incl. parallel mix |
| `internal/engine/harper.go` + `harper_test.go` | core | injectable timeout, timeout-is-an-error, optional pool |
| `internal/engine/layering_test.go` | core | new: dependency-direction guard |
| `desktop/grammar-lookup.py` + `test-lookup.py` | ui | keep-alive connection |
| `desktop/grammar-watch.py` + `test-watch.py` | ui | adaptive `debounce_ms()` |
| `~/.hermes/skills/grammar-server/SKILL.md` | — | the four durable lessons |

## Tests / validation

- Every code task is TDD: failing test written and run with its exact expected failure text, then the
  minimal implementation, then the run that passes. Commits carry the reason.
- Two tasks must **prove their assertion can fail**: the layering guard (poison import, run, delete) and the
  timeout test (nanosecond timeout injected). An always-green guard is decoration.
- `go test ./...` and `make test` are the gates; both must be green before the final push.
- Numeric claims live in `docs/perf/*.json` with the machine state beside them; the architecture doc's table
  must match that JSON exactly — a budget table with hand-typed numbers is how latency work starts lying.
- `examples/latency-probe.py` exits 1 when the engine is unreachable, so it can gate CI without silently
  measuring nothing.

## Risks, tradeoffs, and open questions

1. **The pool may not be worth it.** Two harper processes double the memory and the rule-list read for a
   benefit that only exists under concurrent load (the phone over the LAN, or the UI open while typing).
   The decision rule in Task 6 is explicit: p95 of a small check rising >3× or above 100 ms beside a 200 KB
   check. If it does not fire, write that down and change no code.
2. **Adaptive debounce trades perceived latency against engine load.** 300 ms means up to 4× more checks
   while typing. That is cheap for a 15 ms check, but if Task 5's benchmarks are much slower than the
   baseline suggests, keep the 600 ms band. The bands are deliberately coarse and explainable.
3. **`Server-Timing` exposes internal timing.** Harmless here (no auth, LAN-bound) but it is a public
   surface: keep it to durations, never rule names or text.
4. **Changing the timeout semantics can surface latent bugs.** Any caller that relied on "empty on timeout"
   will now see an error — that is the intent, and `go test ./...` is where it shows. Fix the callers,
   never the assertion.
5. **Keep-alive can hold a stale socket.** The implementation retries once and then raises; over a flaky LAN
   that is one extra failed request, not a hang. Watch for it if the phone reports intermittent failures.
6. **Open question:** should the pop-up be a long-lived process? Task 10 measures start-up; if it is over
   ~200 ms the FIFO design is documented but not built. Decide with the number, not with the elegance.
7. **Open question:** is 600 ms p95 perceived latency actually the right target? Nobody has measured a user
   perceiving it yet. Task 3's baseline plus Task 9's change give the before/after; if 600 ms still feels
   laggy in use, the next lever is the pop-up's start-up cost (Task 10), not the engine.
