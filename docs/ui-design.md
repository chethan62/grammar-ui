# The interface, as built

What the grammar-ui desktop program actually is, in enough detail to change it without guessing. Every
number here was measured on the machine it was built for; every claim is something the code or a gate
does, not something it was meant to do.

The client is **`grammar-popup.py`** plus **`grammar-card.qml`**, installed to `~/.local/bin`. It is a
Qt/QML program talking to the accessibility bus (`grammar-watch`) and to the engine over HTTP.

## 1. One program, one window, two views

There is one window class and one QML file. The same program is started in one of two ways:

| | the card | the settings window |
|---|---|---|
| started by | the watcher, one process per finding | `--settings`, or the card's own **Settings** link |
| view | `card.view === "finding"` | `card.view === "settings"` |
| lifetime | `--timeout` (12 s from the watcher) | until closed |
| position | at the caret, from `--x/--y/--caret-h` | centred on the primary screen, then clamped |
| flags | `CARD_FLAGS` | `PANEL_FLAGS` |

The flags are the design decision the whole shape follows from:

```
CARD_FLAGS  = FramelessWindowHint | X11BypassWindowManagerHint | WindowStaysOnTopHint
              | WindowDoesNotAcceptFocus | Tool
PANEL_FLAGS = the same, *without* WindowDoesNotAcceptFocus
```

A card must never take the keyboard — you are typing into your document, not into a card. A settings
window must have it, because typing an address, a model name or an API key is most of what it is for.
That is why there are two views rather than one, and why the window is called Settings rather than AI
runner: the AI runner is one group inside it, not its subject.

**Switching in place.** The card's **Settings** link calls `bridge.openSettings()`, which flips this
window to the settings view and back — one window, one process. `apply_view()` is the single place the
view and its flags are decided, and it carries a measured workaround: changing window flags on X
recreates the native window, and a recreated *visible* window paints an empty client area (measured
twice — right size, right pid, blank). So the window is hidden, its flags are changed, and it is shown
again on the other side. ✕ or Escape returns to the finding.

## 2. Geometry

- **Width** 436 px: a 408 px column with 14 px margins, both views.
- **The card** is placed at the caret's screen rectangle (`--x/--y`, plus `--caret-h` so `clamp()` can
  hang it above the line when the screen's bottom is in the way). `clamp()` keeps it on a monitor and
  flips it above the caret rather than covering the line being typed.
- **The card waits 40 ms** (`QTimer.singleShot(40, place)`) before positioning and reporting, because
  width and height are still zero before the layout settles; the settings view waits 300 ms, because
  its height arrives with its state a round trip later.
- **The panel** is centred on the primary screen and then run through the same `clamp()`: centring a
  window taller than the screen would put its footer — with Save on it — off the bottom.
- **The two scrolling lists** are capped at `list_max_for(screen_height)` px — `(height - 560) / 2`,
  floored at 60, capped at 132. The 560 is measured: the panel's fixed content is ~520 px with the
  lists at full height. On a 1080-tall screen that is 132; on a 768-tall screen it is 104, so the panel
  gives way in the only part that can.
- Sizes are reported by the card itself, on stderr, at the moment it places itself:
  `PLACED <x> <y> (asked <x> <y>) size <w>x<h>`. The gate compares that with what it asked for; a card
  that maps at 1×1 and "positions fine" is the failure that line exists to make visible.

## 3. The card, element by element

| element | content | behaviour |
|---|---|---|
| the word | `payload.old` | highlighted; the finding's own text |
| badge | `payload.badge` | e.g. `Rules engine · 1 ms` — the engine's own measurement |
| **Settings** | — | `bridge.openSettings()`: this window switches to the settings view |
| reason | `payload.reason` | the engine's sentence |
| `payload.more` | secondary line, when present | e.g. `harper-ls · MORFOLOGIK_RULE_EN_US` |
| `payload.others` | "N more" line, **only when > 0** | the card says nothing rather than "0 more" |
| FIXES chips | `payload.alts` | one button per alternative → `choose("replace", <alt>)` |
| REPHRASE row | `tone`, `intent` | needs `payload.sentence`; **Rephrase** becomes **Cancel** while streaming |
| footer | — | see the buttons below |

The card's buttons, their conditions and the action each one reports — the *whole* output contract is
one JSON line on stdout (`action_json`), and a marker file with the same shape:

| button | shown when | reports |
|---|---|---|
| **Copy** | always | `{"action": "copy"}` |
| **Fix sentence** | always (primary) | `{"action": "sentence"}` |
| **Ignore** | always | `{"action": ""}` — a dismissal, not an edit |
| **Ignore this word** | `payload.word` is non-empty | `{"action": "ignore-word"}` |
| **Ignore in `<app>`** | `payload.app` is non-empty | `{"action": "ignore-app"}` |
| **Pause for an hour** | `payload.app` is non-empty | `{"action": "pause-hour"}` |
| an alternative chip | one per `payload.alts` | `{"action": "replace", "text": <alt>}` |
| **Rephrase** / **Cancel** | `payload.sentence` is non-empty | streams `/v2/rewrite`; not an action |

The keyboard route reaches the same answers without the card ever taking focus: `grammar-action accept`
sends `choose("sentence", "")`, `grammar-action dismiss` sends `choose("", "")` — the same two calls the
buttons make, which is the point of asserting both routes against one expected line.

## 4. The settings window, section by section

| section | content | what a control calls | who owns the state |
|---|---|---|---|
| header | title, a dot + one word for the runner's state (`working`/`unverified`/`not answering`/`off`), ✕ | ✕ closes | — |
| **IGNORED WORDS** | one row per word, each with **Allow `<word>`** | `bridge.dropWord(word)` → `POST /v2/ignore {word, forget:true}` | the engine |
| **PAUSED APPLICATIONS** | one row per paused app, each with **Resume `<app>`** | `bridge.resumeApp(app)` → `grammar-pause --unblock <app>` | this client's blocklist file |
| **PAUSE** | `pauseNote` — "not paused", or "paused for another N minutes" | **Pause for an hour** → `grammar-pause 1h`; **Check again now** → `grammar-pause off` | the pause file |
| **AI SETTINGS** | the description, then RUNNER / ADDRESS / MODEL, the key note, and warnings | **Save** → `POST /v1/ai`; **Test** → `GET /v1/ai` again | the server (`~/.config/grammar-server/ai.json`) |
| footer | the status line, then Close / Test / Save | — | — |

Two rules hold across it:

- **Reads come from the owner, and nothing is cached.** The words come from the engine over
  `GET /v2/ignore`; the blocklist and the pause come from this client's files. All of it is read on
  load, because a card's "Ignore this word" or a `grammar-pause` run from a shortcut can change it
  while the window is open.
- **Writes go through the owner, then the state is asked for again** — never assumed. The panel does
  not write the blocklist or the pause file itself: `grammar-pause` owns those two and their own words
  for a refusal (exit 1 when there was nothing of yours to undo), and those words appear on the status
  line. A panel that trusted its own click would show a word it had failed to remove.

Rows are named for what they do *to what* — "Allow flibbertigibbet", "Resume firefox" — because a
column of buttons all called "Remove" is a list no screen reader, and no test, can tell apart.

**The two lists scroll.** Each lives in a `ScrollView` whose height is `min(<its Column>.implicitHeight,
listMax)`, measured against the Column and *not* against the ScrollView's `contentItem`: that is the
Flickable's own container, whose implicit height is 0 for a Column child, which rendered two empty
lists that the accessibility tree still reported in full.

## 5. The state contract

The QML reads host state through `card.s(key, default)` (the settings view) and `card.payload.<key>`
(the finding). Nothing in the QML computes a setting; the host hands over a view.

**The payload the card is started with** (stdin JSON, merged with argv flags by `parse_payload`):
`old`, `reason`, `badge`, `alts`, `api`, `sentence`, plus `more`, `others`, and the `word` / `app` the
two conditional buttons read. The watcher's finding supplies all of them.

**The settings view** (`settings_view(state)`, pure, in `grammar_core`): `provider`, `url`, `model`,
`presets`, `models`, `hint`, `warnings`, `reachable`, `writable`, `status`, `tone`, `keyEnv`, `keySet`,
`needsKey`, `keyNote`, `words`, `pausedApps`, `listMax`, `pauseNote`.

The seam between the two files is asserted in both directions: every key the view emits must appear in
the QML (a rename on one side is a blank row), and no key the QML still reads may have stopped being
sent (that renders as the word `undefined` — quieter than a rename and just as wrong).

## 6. Colour and type

The palette is **pure data** in `grammar_core.card_colors(dark)` — ten keys, chosen here rather than in
the QML so they can be tested: `surface`, `chip`, `hover`, `border`, `text`, `muted`, `faint`,
`accent`, `accentInk`, `accentHover`. Light or dark is decided by the host from the platform palette's
window lightness, not from a hardcoded preference.

Contrast is asserted, not assumed: WCAG ratio ≥ 4.5 for `text` on `surface` and for `accentInk` on
`accent`. Type is small by design — 17 px for the window title, 12 px for body and rows, 11 px for the
explanatory notes, which are `faint` so they recede.

## 7. Keyboard and accessibility

The card cannot be focused *on purpose*: on Wayland nothing else can place a window at a caret without
the compositor's cooperation, and a card that steals focus interrupts the sentence being typed. The cost
is real and accepted: **its buttons cannot be reached by Tab**, so the keyboard route is the shortcuts
(`grammar-action accept` / `dismiss`, bound in the desktop) and Escape/✕ for dismissal.

Everything is still reachable through AT-SPI, and that is how the gates drive it: a live card reports
`['Settings', 'tolerating', 'Ignore', 'Copy', 'Fix sentence', 'Ignore in Firefox', 'Pause for an hour',
'Ignore this word', 'Rephrase', ...]`, and buttons are activated by *name* — no clicking at a guessed
position. `Accessible.description` is set on the vector ✕ (a name is what a test can press) and on every
row button. Two measured limits: terminals publish no text interface for the input line, and Electron
apps publish nothing without `--force-renderer-accessibility`, so "anywhere" is never promised —
"anything that publishes accessible text" is.

## 8. What pins this

| gate | pins |
|---|---|
| `desktop/test-popup-place.py` | `clamp()`, the payload merge, the palette and its contrast, the view↔QML seam both ways, `list_max_for` arithmetic — plus live legs: placement from the X server, the keyboard route, a pressed card button, and the panel's own list |
| `desktop/test-watch.py` | the watcher: the read window, the debounce bands, the ignore route, the blocklist, the pause verbs, the notification contract |
| `desktop/test-doctor.py` | every silent failure the doctor names |
| `desktop/test-lookup.py` | the selection checker |

Live legs are opt-in (`GRAMMAR_LIVE=1`) and every one of them prints why it skipped. The panel leg
asserts that the panel's **height follows its rows** — 845 px with eight words, 739 px with one. That
is not a taste: two lists once rendered empty behind every passing assertion, and neither the rows'
extents nor their position detects it. Measured on the broken build, a row inside a collapsed list
still reported `103x26` at a plausible spot *inside* its window, so an extents check added for that
purpose was passing on the bug it was written for. The window's own size was the only property that
differed, so it is the one asserted — and the assertion was proved by putting the bug back and
watching it fail before it was trusted.

```bash
make test                                        # the gates that run anywhere
GRAMMAR_LIVE=1 /usr/bin/python3 desktop/test-popup-place.py    # + the ones that need a screen
```

## 9. Deliberately not built

- **A tray icon.** Impossible on this desktop and it fails silently; measured, documented, not rebuilt.
- **A whole-panel scroll.** Only the lists grow, so only they scroll. Measured, though, the panel is
  **709 px tall with nothing in the lists at all** — so on a screen shorter than that the lists have
  nothing left to give, and `clamp()` can only push the footer, with Save on it, off one edge. The fix
  is one scroll area around the whole panel (or smaller fixed content), not more tuning of the list cap.
  Not built: the display this runs on has the room. The assertion that would drive it is already
  available — `PLACED … size WxH` read against a scaled screen (`QT_SCALE_FACTOR`), which is how the
  709 was measured. A live leg doing exactly that was written and removed again: Qt's `offscreen`
  platform is the only way to fake a small screen here (no Xvfb, no sudo), and its software renderer
  segfaults the panel the moment a list has a scrollbar to draw.
- **A resident card process.** One process per suggestion costs ~586 ms before the card is visible
  (measured: 10 ms interpreter, 120 ms PySide6 import, ~420 ms Qt start-up and window map; QML
  compilation is ~0, which was checked and is a dead end). It is the largest user-felt cost left. The
  trigger to build the long-lived card is recorded in `grammar-server/docs/architecture.md`.
- **An engine-address field.** The engine is on loopback; the address is a constant in the payload.
- **Adding an ignored word or a paused app from the panel.** Both can only be added from a card's own
  buttons, so adding requires a finding to appear first; the panel can only take them back.
