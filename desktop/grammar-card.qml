// The suggestion card, and the AI-runner settings panel it can turn into.
//
// QML rather than GTK3: the GTK card was ~90% Breeze's defaults, so every attempt to design it
// came back looking like a Breeze dialog. Here the surface, the chips, the spacing and the
// entrance are all ours, while the colours still come from the desktop's own palette (the host
// passes them as `colors`), so it belongs on this desktop rather than merely appearing on it.
//
// Text is plain, and that is left alone on purpose. The strings arrive from the user's own writing
// via the engine, and the GTK card had to escape them by hand for Pango. Here the injection
// cannot happen at all.
//
// Two views, one surface. "finding" is the card at the caret; "settings" is the same card as the
// AI-runner panel (`grammar-popup.py --settings`), which is where the browser UI's settings went
// when the browser UI was removed. The panel decides nothing: it draws what GET /v1/ai returns and
// calls POST /v1/ai, which validates, applies and saves, so the choice outlives a restart. Which
// preset is right for a box, and whether a key is present, are the server's findings, not ours.
//
// The process contract is unchanged: the host reads a JSON payload on stdin, prints one JSON line
// on stdout, and exits 2 when no card is possible. Nothing here touches the network except through
// the host's slots.

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts

Window {
    id: card
    // One window, two views: the finding at the caret turns into the AI-runner panel in place
    // (`view`), which is what "one UI" means here — no second window, no second process. The class
    // is therefore the card's, always: override-redirect, frameless, on top. That is also the only
    // class that renders on this stack — a compositor-managed window paints its background and none
    // of its content under XWayland (measured with decorations and without, layer on and off, flags
    // from QML and from the host). The panel's own titlebar below is the compensation: a compositor
    // will not move a window it does not manage, so the drag is ours.
    color: "transparent"
    visible: false
    width: surface.width
    height: surface.height

    property var payload: ({})       // what the watcher sent
    property var colors: ({})        // from the desktop's palette, via the host
    property var candidates: []      // the model's alternatives, filled by the host
    property var changes: []         // what each alternative changed: [[removed, added], ...]
    property string streaming: ""    // the model's words, arriving, before the answers do
    property var settings: ({})      // GET /v1/ai, through the host
    property string view: "finding"  // "finding" | "settings"
    property string status: ""
    property bool busy: false
    // The desktop's animation-duration factor, from the host: 1.0 normal, 0.25 a quarter, 0 none.
    property real motion: 1.0

    readonly property bool hasAlts: payload.alts !== undefined && payload.alts.length > 1
    readonly property bool canRephrase: payload.api !== undefined && payload.api.length > 0
                                        && payload.sentence !== undefined && payload.sentence.length > 0
    // A request from another machine may not change the backend: the server refuses it, so the
    // panel is read-only rather than offer a button that fails.
    readonly property bool editable: settings.writable !== false

    // Escape closes the panel — the standard gesture for a window you opened to change something,
    // and the only one it has now that it is a real window. Disabled for the card, which never
    // takes focus and therefore has no keys to miss: Enter and Escape do nothing there by design.
    Shortcut {
        enabled: card.view === "settings"
        // Spelled out rather than StandardKey.Cancel, which maps several bindings (Qt warns that
        // only one of them is used): Escape is the one a panel is expected to answer.
        sequence: "Escape"
        onActivated: bridge.choose("", "")
    }

    function c(name, fallback) { return colors && colors[name] ? colors[name] : fallback }

    // Move the window by a delta. The claim on the position is the same one that puts the card at
    // the caret, and it is why the panel can be dragged at all: an override-redirect window is the
    // host's to place, so the titlebar MouseArea asks for this instead of the compositor.
    function dragBy(dx, dy) { bridge.dragWindow(dx, dy) }
    // The settings state is another process's JSON: every read needs a fallback.
    function s(name, fallback) {
        return settings && settings[name] !== undefined ? settings[name] : fallback
    }

    // Save, and the same thing Enter does in any of the fields: one place, so the four controls
    // cannot drift apart, and the key is handed over and then cleared out of the widget.
    function saveRunner() {
        card.status = ""
        bridge.saveSettings(runnerBox.currentValue, urlField.text, modelBox.editText, keyField.text)
        keyField.text = ""
    }

    // The whole card's entrance: short enough that it feels like it was already there, and scaled
    // by the desktop's animation factor — at 0 it is not an animation at all, which is the point.
    OpacityAnimator on opacity { from: 0; to: 1; duration: Math.round(120 * card.motion) }
    Component.onCompleted: visible = true

    Rectangle {
        id: surface
        x: 0
        y: 0
        radius: 12
        color: card.c("surface", "#ffffff")
        border.width: 1
        border.color: card.c("border", "#e2e5ea")
        // One decided width for both views, and a height taken from whichever is showing. Decided
        // rather than derived: sizing a column from children that wrap to the parent's width is a
        // binding cycle, and it clipped the rephrase row off the right edge (measured).
        implicitWidth: 436
        implicitHeight: (card.view === "settings" ? panelScroll.implicitHeight
                                                  : findingPanel.implicitHeight) + 28
        // The layer stays on for both views: it is what the card's shadow and rounded corners are
        // drawn with, and the card is the class both views use.
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowBlur: 0.7
            shadowVerticalOffset: 4
            shadowColor: "#40000000"
        }

        // ==================== the finding ====================
        Column {
            id: findingPanel
            x: 14
            y: 14
            spacing: 10
            width: 408
            visible: card.view !== "settings"

            // ---- what is wrong ------------------------------------------------------------
            RowLayout {
                width: parent.width
                spacing: 8
                Text {
                    Layout.fillWidth: true
                    text: card.payload.old || ""
                    font.pixelSize: 19
                    font.weight: Font.DemiBold
                    font.strikeout: true
                    color: card.c("text", "#14181d")
                    wrapMode: Text.WordWrap
                }
                Text {
                    text: card.payload.badge || ""
                    font.pixelSize: 11
                    color: card.c("faint", "#8a93a0")
                }
                // The way in to the settings. The card is where the checking happens, so it is
                // where the backend is chosen; after the browser UI was removed there is no other
                // surface at all. It switches *this* window to the panel in place — one UI, one
                // window, one process — and the ✕ in the panel's titlebar or Escape comes back.
                Act {
                    flat: true
                    text: "Settings"
                    onClicked: bridge.openSettings()
                }
            }
            Text {
                width: parent.width
                visible: text.length > 0
                text: card.payload.reason || ""
                font.pixelSize: 13
                color: card.c("muted", "#5a6472")
                wrapMode: Text.WordWrap
            }
            Text {
                width: parent.width
                visible: text.length > 0
                text: card.payload.more || ""
                font.pixelSize: 12
                color: card.c("faint", "#8a93a0")
                wrapMode: Text.WordWrap
            }
            // The sentence often carries more than one issue, and the engine's own sample does:
            // naming one error while "Fix sentence" silently corrected both read as a mystery.
            Text {
                width: parent.width
                visible: (card.payload.others || 0) > 0
                text: card.payload.others === 1
                      ? "1 more issue in this sentence — Fix sentence corrects both."
                      : (card.payload.others + " more issues in this sentence — Fix sentence "
                         + "corrects them all.")
                font.pixelSize: 12
                color: card.c("muted", "#5a6472")
                wrapMode: Text.WordWrap
            }

            // ---- the engine's answers -----------------------------------------------------
            Rule { visible: card.hasAlts }
            Caption { text: "FIXES"; visible: card.hasAlts }
            Flow {
                width: parent.width
                spacing: 6
                visible: card.hasAlts
                Repeater {
                    model: card.payload.alts || []
                    delegate: Act {
                        required property int index
                        required property string modelData
                        // Best-first from the engine, and the first wears the accent: which fix
                        // is on offer should be visible, not inferred.
                        primary: index === 0
                        text: modelData
                        onClicked: bridge.choose("replace", modelData)
                    }
                }
            }
            Row {
                anchors.right: parent.right
                spacing: 6
                Act { text: "Ignore"; onClicked: bridge.choose("", "") }
                Act { text: "Copy"; onClicked: bridge.choose("copy", "") }
                Act { text: "Fix sentence"; primary: true; onClicked: bridge.choose("sentence", "") }
            }
            // Pausing in this application, offered from the surface that is already open: the
            // alternative is a settings list nobody opens to discover a problem. Its own line and
            // low emphasis, because it is a standing choice about the application, not an action on
            // this finding — and hidden when the host does not know the application's name, since a
            // button that cannot keep its promise is worse than no button.
            Act {
                flat: true
                anchors.right: parent.right
                visible: card.payload.app !== undefined && card.payload.app.length > 0
                text: "Ignore in " + card.payload.app
                onClicked: bridge.choose("ignore-app", "")
            }
            // "Not now" rather than "not this application": the same one-click idea, for a meeting
            // or a deadline. It ends by itself, so the card does not have to offer a way back.
            Act {
                flat: true
                anchors.right: parent.right
                text: "Pause for an hour"
                onClicked: bridge.choose("pause-hour", "")
            }
            // Only for a single misspelled word: the engine's list is a *word* list, so hiding a
            // phrase or a sentence with it would be a promise the feature cannot keep. The host sends
            // an empty word for anything else, which hides this rather than offering it wrongly.
            Act {
                flat: true
                anchors.right: parent.right
                visible: card.payload.word !== undefined && card.payload.word.length > 0
                text: "Ignore this word"
                onClicked: bridge.choose("ignore-word", "")
            }

            // ---- the model's own answers --------------------------------------------------
            Rule { visible: card.canRephrase }
            Caption { text: "REPHRASE"; visible: card.canRephrase }
            Column {
                width: parent.width
                spacing: 2
                visible: card.canRephrase
                Repeater {
                    model: card.candidates
                    delegate: Column {
                        required property string modelData
                        required property int index
                        width: parent.width
                        spacing: 1
                        Act {
                            flat: true
                            text: modelData
                            // A rephrase replaces the whole sentence, so this answer carries its text.
                            onClicked: bridge.choose("sentence", modelData)
                        }
                        // What this answer changed, against the sentence you are replacing — the same
                        // two colours the finding view already uses for the original and the fix:
                        // struck-through and muted for what went, full contrast for what arrived.
                        Row {
                            spacing: 10
                            leftPadding: 2
                            visible: card.changes.length > index && card.changes[index].length == 2
                                     && (card.changes[index][0].length > 0 || card.changes[index][1].length > 0)
                            Text {
                                visible: text.length > 0
                                text: card.changes.length > index ? card.changes[index][0] : ""
                                font.pixelSize: 11
                                font.strikeout: true
                                color: card.c("faint", "#8a93a0")
                            }
                            Text {
                                visible: text.length > 0
                                text: card.changes.length > index ? card.changes[index][1] : ""
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                                color: card.c("text", "#e7eaee")
                            }
                        }
                    }
                }
            }
            // The model's words as it writes them. Above the answers, because it is replaced by them
            // when they land: with a local model the finished sentence can be two seconds away, and a
            // card that says "Rephrasing…" for two seconds looks stuck.
            Text {
                width: parent.width
                visible: card.streaming.length > 0
                text: card.streaming
                font.pixelSize: 12
                color: card.c("muted", "#5a6472")
                wrapMode: Text.WordWrap
            }
            RowLayout {
                width: parent.width
                spacing: 6
                visible: card.canRephrase
                ComboBox {
                    id: toneBox
                    Layout.preferredWidth: 124
                    enabled: !card.busy
                    model: ["tone: as-is", "tone: professional", "tone: casual", "tone: formal"]
                }
                ComboBox {
                    id: intentBox
                    Layout.preferredWidth: 158
                    enabled: !card.busy
                    model: ["rephrase as-is", "concise", "clear", "simple"]
                }
                Item { Layout.fillWidth: true }
                // One button, two jobs: while a rephrase is running the useful thing to offer is
                // stopping it, not starting another one.
                Act {
                    text: card.busy ? "Cancel" : "Rephrase"
                    onClicked: {
                        if (card.busy) {
                            bridge.cancel()
                            card.status = "Stopping…"
                            return
                        }
                        card.busy = true
                        card.streaming = ""
                        card.status = "Rephrasing… a local model takes a few seconds"
                        bridge.rephrase(toneBox.currentIndex, intentBox.currentIndex)
                    }
                }
            }
            Text {
                width: parent.width
                visible: text.length > 0
                text: card.status
                font.pixelSize: 12
                color: card.c("muted", "#5a6472")
                wrapMode: Text.WordWrap
            }
        }

        // ==================== the settings window ====================
        // One window for everything this product can be told: what it skips (the ignore list, the
        // per-application pauses, the pause) first, because that is the checking half and the half
        // most settings belong to — and the AI runner below in its own labelled group, because it is
        // one choice, not the subject of the window.
        // The whole panel scrolls as one region. Its fixed content alone measures ~709 px, so on a
        // screen shorter than that the two lists have nothing left to give up, and a window taller
        // than the display leaves clamp() able to push the footer — with Save on it — off one edge.
        // One scrollbar for the panel also removes the two nested lists, where the wheel went to
        // whichever list happened to be under the cursor.
        ScrollView {
            id: panelScroll
            x: 14
            y: 14
            width: 408
            contentWidth: availableWidth
            visible: card.view === "settings"
            implicitHeight: Math.min(settingsPanel.implicitHeight, card.s("panelMax", 100000))
            clip: true
            ScrollBar.vertical.policy: ScrollBar.AsNeeded

            Column {
                id: settingsPanel
                spacing: 10
                width: 408

                // ---- header: what this is, and how it is doing ----------------------------------
                // This row is the panel's titlebar, because the window is override-redirect: a
                // compositor will not move a window it does not manage, so dragging is ours and the
                // close is ours. A glyph would be one font away from tofu, so the ✕ is drawn.
                Item {
                    width: parent.width
                    implicitHeight: headerRow.implicitHeight
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.SizeAllCursor
            // The panel is dragged by this row and the window is override-redirect, so the
            // drag must not be stolen by the scroll view that now wraps it.
            preventStealing: true
                        // Incremental, not from the press point: passing the total delta to a
                        // position-setting move would apply it again on every event and run away.
                        property point from: Qt.point(0, 0)
                        onPressed: from = Qt.point(mouse.x, mouse.y)
                        onPositionChanged: {
                            card.dragBy(mouse.x - from.x, mouse.y - from.y)
                            from = Qt.point(mouse.x, mouse.y)
                        }
                    }
                    RowLayout {
                        id: headerRow
                        anchors.fill: parent
                        spacing: 8
                        Text {
                            Layout.fillWidth: true
                            text: "Settings"
                            font.pixelSize: 17
                            font.weight: Font.DemiBold
                            color: card.c("text", "#14181d")
                        }
                        // A dot and one word, so the state reads at a glance instead of being buried in
                        // a sentence. The sentence is still there, under the buttons.
                        Row {
                            spacing: 6
                            Rectangle {
                                width: 7
                                height: 7
                                radius: 3.5
                                anchors.verticalCenter: parent.verticalCenter
                                color: {
                                    var t = card.s("tone", "idle")
                                    if (t === "good") return "#2f9e57"
                                    if (t === "warn") return "#d9822b"
                                    if (t === "bad") return "#cf4b3f"
                                    return card.c("faint", "#8a93a0")
                                }
                            }
                            Text {
                                text: {
                                    var t = card.s("tone", "idle")
                                    if (t === "good") return "working"
                                    if (t === "warn") return "unverified"
                                    if (t === "bad") return "not answering"
                                    return "off"
                                }
                                font.pixelSize: 12
                                color: card.c("muted", "#5a6472")
                            }
                        }
                        Act {
                            flat: true
                            implicitWidth: 26
                            // "Close panel" is the accessible name, not something drawn: the contentItem
                            // below is the vector ✕. Distinct from the footer's "Close" so both are
                            // findable — a button with no name is a button no test can press.
                            text: "Close panel"
                            Accessible.description: "Close the settings panel"
                            onClicked: bridge.choose("", "")
                            contentItem: Item {
                                Rectangle {
                                    width: 11
                                    height: 1.5
                                    rotation: 45
                                    anchors.centerIn: parent
                                    color: card.c("muted", "#5a6472")
                                }
                                Rectangle {
                                    width: 11
                                    height: 1.5
                                    rotation: -45
                                    anchors.centerIn: parent
                                    color: card.c("muted", "#5a6472")
                                }
                            }
                        }
                    }
                }
                // ---- the settings that used to have no window at all -----------------------------
                // A word could be added from a card's "Ignore this word" and only a text editor could take
                // it back; "Ignore in <application>" had exactly the same problem. They live here now, so
                // one window holds every setting this product has — which is why the panel exists and a
                // menu does not.
                Rule {}

                Field {
                    label: "IGNORED WORDS"
                    note: card.s("words", []).length
                          ? "The engine stops reporting these; removing one brings the findings back."
                          : "Nothing ignored yet. \"Ignore this word\" on a card adds one."
                    // Bounded, so the window stays a window: the list scrolls inside it instead of
                    // growing the panel. Eight ignored words used to make it 785 px tall, and the cap
                    // that hid the rest was a worse answer than a scrollbar — this panel is where the
                    // user is told to look for them.
        Column {
                        width: parent.width
                        spacing: 4
                        Repeater {
                            model: card.s("words", [])
                        delegate: RowLayout {
                            width: parent.width
                            spacing: 6
                            Text {
                                // Layout.fillWidth, not an arithmetic width: a long ignored word has to
                                // shorten itself rather than push its own button off the panel. Measured
                                // on the first attempt — "Allow flibbertigibbet" came out clipped.
                                Layout.fillWidth: true
                                text: modelData
                                font.pixelSize: 12
                                color: card.c("muted", "#5a6472")
                                elide: Text.ElideRight
                            }
                            Act {
                                flat: true
                                // The name is the action and the word. A column of buttons all called
                                // "Remove" is a list no one — screen reader or gate — can tell apart.
                                text: "Allow " + modelData
                                Accessible.description: "Stop ignoring " + modelData
                                onClicked: bridge.dropWord(modelData)
                            }
                        }
                    }
                    }
        }

                Rule {}

                Field {
                    label: "PAUSED APPLICATIONS"
                    note: card.s("pausedApps", []).length
                          ? "Checked nowhere until you resume them. Password managers and terminals are "
                            + "always left alone and are not listed."
                          : "Nothing paused. \"Ignore in <application>\" on a card pauses one."
                    // Bounded, so the window stays a window: the list scrolls inside it instead of
                    // growing the panel. Eight ignored words used to make it 785 px tall, and the cap
                    // that hid the rest was a worse answer than a scrollbar — this panel is where the
                    // user is told to look for them.
        Column {
                        width: parent.width
                        spacing: 4
                        Repeater {
                            model: card.s("pausedApps", [])
                        delegate: RowLayout {
                            width: parent.width
                            spacing: 6
                            Text {
                                Layout.fillWidth: true
                                text: modelData
                                font.pixelSize: 12
                                color: card.c("muted", "#5a6472")
                                elide: Text.ElideRight
                            }
                            Act {
                                flat: true
                                text: "Resume " + modelData
                                Accessible.description: "Check in " + modelData + " again"
                                onClicked: bridge.resumeApp(modelData)
                            }
                        }
                    }
                    }
        }

                Rule {}

                Field {
                    label: "PAUSE"
                    note: card.s("pauseNote", "not paused")
                    Row {
                        width: parent.width
                        spacing: 6
                        Act { text: "Pause for an hour"; onClicked: bridge.pauseHour() }
                        Act { text: "Check again now"; onClicked: bridge.resumeNow() }
                    }
                }

                Rule {}

                Caption { text: "AI SETTINGS" }

                Text {
                    width: parent.width
                    text: "Which model rephrases your sentences. Checking your writing works either "
                          + "way — this affects Rephrase only, and the choice survives a restart."
                    font.pixelSize: 12
                    color: card.c("muted", "#5a6472")
                    wrapMode: Text.WordWrap
                }

                Rule {}

                Field {
                    label: "RUNNER"
                    note: {
                        var p = card.s("presets", [])[runnerBox.currentIndex] || ({})
                        return p.local === false ? "Cloud: what you rephrase leaves this machine."
                                                 : "Local: nothing leaves this machine."
                    }
                    ComboBox {
                        id: runnerBox
                        width: parent.width
                        model: card.s("presets", [])
                        textRole: "label"
                        valueRole: "id"
                        // Follows what is configured, and re-follows it when a save returns a new state.
                        currentIndex: {
                            var ps = card.s("presets", [])
                            var want = card.s("provider", "")
                            for (var i = 0; i < ps.length; i++) {
                                if (ps[i].id === want) return i
                            }
                            return -1
                        }
                        onActivated: {
                            // Picking a product fills in its defaults, because nobody remembers that
                            // LM Studio listens on 1234. Both fields stay editable for anything
                            // unlisted, and typing an address does not wipe a model already chosen.
                            var p = card.s("presets", [])[currentIndex] || ({})
                            urlField.text = p.url || ""
                            if (p.model) modelBox.editText = p.model
                        }
                    }
                }

                Field {
                    label: "ADDRESS"
                    note: "The base URL of the server, no /v1 needed at the end."
                    TextField {
                        id: urlField
                        width: parent.width
                        text: card.s("url", "")
                        placeholderText: "http://127.0.0.1:11434"
                        enabled: card.editable
                        onAccepted: card.saveRunner()
                    }
                }

                Field {
                    label: "MODEL"
                    // Editable *and* listed: a local server reports what it has loaded, so the list is
                    // the real choice, and a name it does not know can still be typed.
                    note: "What this server reports, or type any model name."
                    ComboBox {
                        id: modelBox
                        width: parent.width
                        editable: true
                        enabled: card.editable
                        model: card.s("models", [])
                        editText: card.s("model", "")
                    }
                }

                Field {
                    // Only for runners that need one: showing an empty password box under a local
                    // server would suggest something is missing when nothing is.
                    visible: card.s("needsKey", false)
                    label: "API KEY"
                    note: card.s("keyNote", "") + " — never sent back, and only settable from that machine."
                    TextField {
                        id: keyField
                        width: parent.width
                        enabled: card.editable
                        echoMode: TextInput.Password
                        // Never the stored value: the server reports whether it has one and nothing
                        // more, so an empty box means "leave it alone" and the note above says which.
                        placeholderText: card.s("keySet", false)
                                         ? "a key is saved — type here to replace it"
                                         : "paste the key"
                        onAccepted: card.saveRunner()
                    }
                }

                Text {
                    width: parent.width
                    visible: text.length > 0
                    text: card.s("hint", "")
                    font.pixelSize: 11
                    color: card.c("faint", "#8a93a0")
                    wrapMode: Text.WordWrap
                }

                // The honest bits: a cloud backend, a key not set yet, or a request from another
                // machine that may not change anything. Each one is the server's own finding, passed
                // through rather than invented here.
                Repeater {
                    model: card.s("warnings", [])
                    delegate: Row {
                        required property string modelData
                        width: settingsPanel.width
                        spacing: 7
                        Rectangle {
                            width: 6
                            height: 6
                            radius: 3
                            color: "#d9822b"
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            width: settingsPanel.width - 13
                            text: modelData
                            font.pixelSize: 12
                            color: card.c("muted", "#5a6472")
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: 6
                    Text {
                        Layout.fillWidth: true
                        text: card.status.length > 0 ? card.status : card.s("status", "")
                        font.pixelSize: 12
                        color: card.c("muted", "#5a6472")
                        wrapMode: Text.WordWrap
                    }
                    Act { text: "Close"; onClicked: bridge.choose("", "") }
                    Act {
                        text: "Test"
                        // The same round trip as Save: /v1/ai asks the backend for its model list, so
                        // one call answers both "is it up" and "what may I pick".
                        onClicked: {
                            card.status = ""
                            bridge.loadSettings()
                        }
                    }
                    Act {
                        text: "Save"
                        primary: true
                        enabled: card.editable
                        onClicked: card.saveRunner()
                    }
                }
            }
}
    }

    // A hairline and a small heading: the first version was one flat column, so nothing said where
    // the fixes ended and the actions began.
    component Rule: Rectangle {
        width: parent.width
        height: 1
        color: card.c("border", "#e2e5ea")
    }
    component Caption: Text {
        font.pixelSize: 11
        font.weight: Font.DemiBold
        font.letterSpacing: 0.8
        color: card.c("faint", "#8a93a0")
    }

    // A labelled row: caption, the control, and a note that says what it wants or what the state
    // is. One definition, so four fields cannot end up aligned differently — which is most of what
    // "looks professional" means in a settings panel.
    component Field: Column {
        id: field
        property string label: ""
        property string note: ""
        default property alias content: slot.data
        width: settingsPanel.width
        spacing: 4
        Caption { text: field.label }
        Column { id: slot; width: field.width; spacing: 4 }
        Text {
            width: field.width
            visible: field.note.length > 0
            text: field.note
            font.pixelSize: 11
            color: card.c("faint", "#8a93a0")
            wrapMode: Text.WordWrap
        }
    }

    // Controls.Basic's Button styled from scratch — this is the part GTK would not give up.
    component Act: AbstractButton {
        id: act
        property bool primary: false
        property bool flat: false
        implicitWidth: Math.max(64, label.implicitWidth + 22)
        implicitHeight: flat ? 26 : 30
        hoverEnabled: true
        contentItem: Text {
            id: label
            text: act.text
            anchors.fill: parent
            verticalAlignment: Text.AlignVCenter
            horizontalAlignment: flat ? Text.AlignLeft : Text.AlignHCenter
            font.pixelSize: act.flat ? 13 : 12.5
            font.weight: act.primary ? Font.DemiBold : Font.Normal
            color: act.enabled
                   ? (act.primary ? card.c("accentInk", "#ffffff") : card.c("text", "#14181d"))
                   : card.c("faint", "#8a93a0")
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: 7
            color: {
                if (act.flat) return act.hovered ? card.c("hover", "#f0f2f5") : "transparent"
                if (act.primary) return act.hovered ? card.c("accentHover", "#2c333b") : card.c("accent", "#1c2127")
                if (act.hovered) return card.c("hover", "#f0f2f5")
                return card.c("chip", "#f4f5f7")
            }
            border.width: act.flat || act.primary ? 0 : 1
            border.color: card.c("border", "#e2e5ea")
            Behavior on color { ColorAnimation { duration: Math.round(90 * card.motion) } }
        }
    }
}
