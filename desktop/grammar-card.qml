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
    // BypassWindowManagerHint is what makes the position ours: with it the window is
    // override-redirect, so the compositor neither moves nor decorates it — the same thing the GTK
    // card asked for with a POPUP_MENU hint. WindowDoesNotAcceptFocus keeps the caret in the app.
    flags: Qt.FramelessWindowHint | Qt.X11BypassWindowManagerHint
           | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus | Qt.Tool
    color: "transparent"
    visible: false
    width: surface.width
    height: surface.height

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

    function c(name, fallback) { return colors && colors[name] ? colors[name] : fallback }
    // The settings state is another process's JSON: every read needs a fallback.
    function s(name, fallback) {
        return settings && settings[name] !== undefined ? settings[name] : fallback
    }

    // The whole card's entrance: short enough that it feels like it was already there.
    OpacityAnimator on opacity { from: 0; to: 1; duration: 120 }
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
        implicitHeight: (card.view === "settings" ? settingsPanel.implicitHeight
                                                  : findingPanel.implicitHeight) + 28
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
                // surface at all.
                Act {
                    flat: true
                    text: "AI runner"
                    onClicked: {
                        card.view = "settings"
                        card.status = ""
                        bridge.loadSettings()
                    }
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
            spacing: 9
            width: 408
            visible: card.view === "settings"

            Text {
                text: "AI runner"
                font.pixelSize: 17
                font.weight: Font.DemiBold
                color: card.c("text", "#14181d")
            }
            Text {
                width: parent.width
                text: "Which model rephrases your sentences. Checking your writing works either "
                      + "way — this affects Rephrase only, and the choice survives a restart."
                font.pixelSize: 12
                color: card.c("muted", "#5a6472")
                wrapMode: Text.WordWrap
            }

            Caption { text: "RUNNER" }
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
                    // Picking a product fills in its defaults, because nobody remembers that LM
                    // Studio listens on 1234. Both fields stay editable for anything unlisted.
                    var p = card.s("presets", [])[currentIndex] || ({})
                    urlField.text = p.url || ""
                    modelBox.editText = p.model || ""
                }
            }

            Caption { text: "WHERE IT ANSWERS" }
            TextField {
                id: urlField
                width: parent.width
                text: card.s("url", "")
                placeholderText: "http://127.0.0.1:11434"
                enabled: card.editable
            }

            Caption { text: "MODEL" }
            ComboBox {
                id: modelBox
                width: parent.width
                // Editable *and* listed: a local server reports what it has loaded, so the list is
                // the real choice, and a name it does not know can still be typed.
                editable: true
                enabled: card.editable
                model: card.s("models", [])
                editText: card.s("model", "")
            }
            Text {
                width: parent.width
                visible: text.length > 0
                text: card.s("hint", "")
                font.pixelSize: 11
                color: card.c("faint", "#8a93a0")
                wrapMode: Text.WordWrap
            }

            // The honest bits: a cloud backend, a key that is not in the server's environment, or a
            // request from another machine that may not change anything. Each one is the server's
            // own finding, passed through rather than invented here.
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
                    onClicked: {
                        card.status = ""
                        bridge.saveSettings(runnerBox.currentValue, urlField.text, modelBox.editText)
                    }
                }
                Act { text: "Close"; onClicked: bridge.choose("", "") }
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
