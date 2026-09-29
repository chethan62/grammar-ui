// The suggestion card.
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

    property var payload: ({})      // what the watcher sent
    property var colors: ({})       // from the desktop's palette, via the host
    property var candidates: []     // the model's alternatives, filled by the host
    property string status: ""
    property bool busy: false
    readonly property bool hasAlts: payload.alts !== undefined && payload.alts.length > 1
    readonly property bool canRephrase: payload.api !== undefined && payload.api.length > 0
                                        && payload.sentence !== undefined && payload.sentence.length > 0

    function c(name, fallback) { return colors && colors[name] ? colors[name] : fallback }

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
        implicitWidth: content.implicitWidth + 28
        implicitHeight: content.implicitHeight + 28
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowBlur: 0.7
            shadowVerticalOffset: 4
            shadowColor: "#40000000"
        }

        Column {
            id: content
            x: 14
            y: 14
            spacing: 10
            // A decided width, not a width derived from children that wrap to the parent's width:
            // that is a binding cycle, and it clipped the right edge of every wide row (measured —
            // the rephrase combos were cut off). Text wraps inside this; the card no longer has to
            // shrink to its longest line.
            width: 408

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
