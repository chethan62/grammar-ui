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
    // Two window classes, and the host owns them: `apply_window_class` in grammar-popup.py decides
    // between them, once per process. Deliberately *not* a `flags:` binding — the host sets the
    // class as a call, and a binding would be overwritten by it.
    //
    // The *card* is a popup: BypassWindowManagerHint is what makes the position ours (the
    // compositor neither moves nor decorates it — the same thing the GTK card asked for with a
    // POPUP_MENU hint), and WindowDoesNotAcceptFocus keeps the caret in the app while you type.
    //
    // The *settings panel* is a window you work in: it takes focus (typing an address, a model name
    // or an API key is most of what it is for) and the compositor decorates it, so dragging,
    // minimising and maximising come for free. It carried the card's flags until someone tried to
    // move it (reported: "i cannot drag window or minimise or maximise").
    title: card.asWindow ? "AI runner — grammar" : ""
    color: card.asWindow ? card.c("surface", "#ffffff") : "transparent"
    visible: false
    width: surface.width
    height: surface.height

    // Set by the host: true only for --settings, i.e. the panel above rather than the card.
    property bool asWindow: false

    property var payload: ({})       // what the watcher sent
    property var colors: ({})        // from the desktop's palette, via the host
    property var candidates: []      // the model's alternatives, filled by the host
    property var settings: ({})      // GET /v1/ai, through the host
    property string view: "finding"  // "finding" | "settings"
    property string status: ""
    property bool busy: false

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
        enabled: card.asWindow
        // Spelled out rather than StandardKey.Cancel, which maps several bindings (Qt warns that
        // only one of them is used): Escape is the one a panel is expected to answer.
        sequence: "Escape"
        onActivated: bridge.choose("", "")
    }

    function c(name, fallback) { return colors && colors[name] ? colors[name] : fallback }
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

    // The whole card's entrance: short enough that it feels like it was already there.
    OpacityAnimator on opacity { from: 0; to: 1; duration: 120 }
    Component.onCompleted: visible = true

    Rectangle {
        id: surface
        x: 0
        y: 0
        radius: card.asWindow ? 0 : 12
        color: card.c("surface", "#ffffff")
        border.width: 1
        border.color: card.c("border", "#e2e5ea")
        // One decided width for both views, and a height taken from whichever is showing. Decided
        // rather than derived: sizing a column from children that wrap to the parent's width is a
        // binding cycle, and it clipped the rephrase row off the right edge (measured).
        implicitWidth: 436
        implicitHeight: (card.view === "settings" ? settingsPanel.implicitHeight
                                                  : findingPanel.implicitHeight) + 28
        // The shadow is the card's, not the window's: a decorated window already has a frame, and
        // an inner drop shadow under a titlebar looks like a mistake. The layer itself stays on —
        // disabling it (tried first) left the client area painting nothing at all, which read as a
        // transparent window with a titlebar on it.
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: !card.asWindow
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
                // surface at all. It opens a *separate process* as a real window — see
                // openSettings — because the panel is worked in, not glanced at.
                Act {
                    flat: true
                    text: "AI runner"
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

            // ---- the model's own answers --------------------------------------------------
            Rule { visible: card.canRephrase }
            Caption { text: "REPHRASE"; visible: card.canRephrase }
            Column {
                width: parent.width
                spacing: 2
                visible: card.canRephrase
                Repeater {
                    model: card.candidates
                    delegate: Act {
                        required property string modelData
                        flat: true
                        text: modelData
                        // A rephrase replaces the whole sentence, so this answer carries its text.
                        onClicked: bridge.choose("sentence", modelData)
                    }
                }
            }
            RowLayout {
                width: parent.width
                spacing: 6
                visible: card.canRephrase
                ComboBox {
                    id: toneBox
                    Layout.preferredWidth: 124
                    model: ["tone: as-is", "tone: professional", "tone: casual", "tone: formal"]
                }
                ComboBox {
                    id: intentBox
                    Layout.preferredWidth: 158
                    model: ["rephrase as-is", "concise", "clear", "simple"]
                }
                Item { Layout.fillWidth: true }
                Act {
                    text: "Rephrase"
                    enabled: !card.busy
                    onClicked: {
                        card.busy = true
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

        // ==================== the AI runner ====================
        Column {
            id: settingsPanel
            x: 14
            y: 14
            spacing: 10
            width: 408
            visible: card.view === "settings"

            // ---- header: what this is, and how it is doing ----------------------------------
            RowLayout {
                width: parent.width
                spacing: 8
                Text {
                    Layout.fillWidth: true
                    text: "AI runner"
                    font.pixelSize: 17
                    font.weight: Font.DemiBold
                    color: card.c("text", "#14181d")
                }
                // A dot and one word, so the state reads at a glance instead of being buried in a
                // sentence. The sentence is still there, under the buttons.
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
            }
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
                note: card.s("keyNote", "") + " — stored 0600 on the machine the engine runs on, "
                      + "never sent back, and only settable from that machine."
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

            Rule {}

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
            Behavior on color { ColorAnimation { duration: 90 } }
        }
    }
}
