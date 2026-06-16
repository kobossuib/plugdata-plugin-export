import json
import subprocess
from pathlib import Path
import platform
import os
import shutil
import argparse
import re
import sys

parser = argparse.ArgumentParser(description="Build plugins with CMake")
parser.add_argument(
    "--compiler-launcher",
    type=str,
    help="Optional compiler launcher (e.g., ccache, sccache)"
)
parser.add_argument(
    "--generator",
    choices=["ninja", "xcode", "visualstudio"],
    default="ninja",
    help="CMake generator to use: ninja (default), xcode, or visualstudio"
)
parser.add_argument(
    "--configure-only",
    action="store_true",
    help="Only run CMake configuration, skip the build step"
)

args = parser.parse_args()

# ── Sanity-check helpers ────────────────────────────────────────────────────

KNOWN_FORMATS = {"VST3", "AU", "LV2", "CLAP", "Standalone"}
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

errors = []   # fatal problems  – abort after collecting all of them
warnings = [] # non-fatal oddities

def error(msg: str):
    errors.append(f"  ERROR: {msg}")

def warn(msg: str):
    warnings.append(f"  WARNING: {msg}")

def validate_config(path: str) -> list:
    """Load and validate config.json. Returns the parsed list or exits."""
    if not os.path.isfile(path):
        print(f"FATAL: config.json not found at '{os.path.abspath(path)}'")
        sys.exit(1)

    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"FATAL: config.json is not valid JSON – {e}")
        sys.exit(1)

    if not isinstance(data, list):
        print("FATAL: config.json must contain a JSON array of plugin objects.")
        sys.exit(1)

    if len(data) == 0:
        warn("config.json contains no plugins – nothing to build.")

    return data

def validate_plugin(plugin: dict, index: int):
    prefix = f"Plugin[{index}]"

    # ── Required fields ──────────────────────────────────────────────────────
    name = plugin.get("name")
    if not name:
        error(f"{prefix}: missing required field 'name'.")
    elif not isinstance(name, str) or not name.strip():
        error(f"{prefix}: 'name' must be a non-empty string (got {name!r}).")

    path = plugin.get("path")
    if not path:
        error(f"{prefix} ({name!r}): missing required field 'path'.")
    else:
        resolved = Path(path).resolve()
        if not resolved.exists():
            error(f"{prefix} ({name!r}): plugin path does not exist: '{resolved}'")
        elif not resolved.is_file():
            error(f"{prefix} ({name!r}): plugin path exists but is not a file: '{resolved}'")

    # ── Optional but validated fields ────────────────────────────────────────
    formats = plugin.get("formats", [])
    if not isinstance(formats, list):
        error(f"{prefix} ({name!r}): 'formats' must be a list, got {type(formats).__name__}.")
    else:
        if len(formats) == 0:
            warn(f"{prefix} ({name!r}): 'formats' is empty – no build targets will be produced.")
        for fmt in formats:
            if fmt not in KNOWN_FORMATS:
                warn(f"{prefix} ({name!r}): unknown format '{fmt}'. "
                     f"Known formats are: {', '.join(sorted(KNOWN_FORMATS))}.")

    plugin_type = plugin.get("type", "")
    if plugin_type and plugin_type.lower() not in ("fx", "instrument", ""):
        warn(f"{prefix} ({name!r}): unexpected 'type' value '{plugin_type}'. "
             f"Expected 'fx' or 'instrument'.")

    version = plugin.get("version", "1.0.0")
    if not VERSION_RE.match(str(version)):
        warn(f"{prefix} ({name!r}): 'version' value '{version}' does not follow "
             f"MAJOR.MINOR.PATCH format.")

    for bool_field in ("enable_gem", "enable_sfizz", "enable_ffmpeg"):
        val = plugin.get(bool_field)
        if val is not None and not isinstance(val, bool):
            warn(f"{prefix} ({name!r}): '{bool_field}' should be a boolean, got {val!r}.")

# ── Run validation ───────────────────────────────────────────────────────────

plugins_config = validate_config("config.json")

for i, plugin in enumerate(plugins_config):
    if not isinstance(plugin, dict):
        error(f"Plugin[{i}]: expected an object, got {type(plugin).__name__}.")
        continue
    validate_plugin(plugin, i)

if warnings:
    print("Build warnings:")
    for w in warnings:
        print(w)
    print()

if errors:
    print("Build errors – cannot continue:")
    for e in errors:
        print(e)
    sys.exit(1)

# ── Continue with the rest of the build ─────────────────────────────────────

# Koboss patches to PluginMode.h — Plan B: NVG drawing (native to plugdata)
_plugin_mode_h = Path("plugdata/Source/PluginMode.h")
if _plugin_mode_h.exists():
    _src = _plugin_mode_h.read_text(encoding='utf-8')

    # 1. Hide titleBar and cnv when chorus (in resized's normal-mode branch).
    #    Keep nvgSurface VISIBLE because render() is what we use to paint.
    _resized_hide_needle = '        } else {\n            float scale = getWidth() / width;\n            pluginModeScale = scale;\n            \n            scaleComboBox.setVisible(true);\n            editorButton->setVisible(true);\n\n            titleBar.setBounds(0, 0, getWidth(), titlebarHeight);\n            scaleComboBox.setBounds(8, 8, 74, titlebarHeight - 16);\n            editorButton->setBounds(getWidth() - titlebarHeight, 0, titlebarHeight, titlebarHeight);'
    _resized_hide_new = '''        } else if (isKoboss()) {
            // Koboss: hide chrome; NVG render() will do all drawing
            pluginModeScale = 1.0f;
            titleBar.setVisible(false);
            scaleComboBox.setVisible(false);
            editorButton->setVisible(false);
            cnv->setVisible(false);
            // Let clicks fall through nvgSurface to reach PluginMode's mouseDown
            editor->nvgSurface.setInterceptsMouseClicks(false, false);
            setInterceptsMouseClicks(true, true);
            // NO robar el foco del teclado: así Ableton sigue recibiendo las notas
            // MIDI del teclado del ordenador mientras Toni toquetea el plugin.
            setWantsKeyboardFocus(false);
            setMouseClickGrabsKeyboardFocus(false);
            editor->nvgSurface.setWantsKeyboardFocus(false);
            editor->nvgSurface.setMouseClickGrabsKeyboardFocus(false);
            // Delay: arrancar repintado continuo para animar el iso
            if (isKobossDelay() && !kobossDelayTimer.isTimerRunning()) {
                kobossDelayTimer.owner = this;
                kobossDelayTimer.startTimerHz(60);
            }
        } else {
            float scale = getWidth() / width;
            pluginModeScale = scale;

            scaleComboBox.setVisible(true);
            editorButton->setVisible(true);

            titleBar.setBounds(0, 0, getWidth(), titlebarHeight);
            scaleComboBox.setBounds(8, 8, 74, titlebarHeight - 16);
            editorButton->setBounds(getWidth() - titlebarHeight, 0, titlebarHeight, titlebarHeight);'''
    if _resized_hide_needle in _src and "Koboss: hide chrome" not in _src:
        _src = _src.replace(_resized_hide_needle, _resized_hide_new, 1)
        print("Koboss patch: resized() hides chrome for chorus")

    # 2. Hide the "Plugin Info" (P) button
    _needle = 'editorButton->setBounds(getWidth() - titlebarHeight, 0, titlebarHeight, titlebarHeight);'
    _new = 'editorButton->setBounds(-9999, -9999, 1, 1); // Koboss: hide info button'
    if _needle in _src and _new not in _src:
        _src = _src.replace(_needle, _new)
        print("Koboss patch: hid info button")

    # 3. Hide centered patch title text (paint method draws nothing for title)
    _title_needle = 'g.drawText(cnv->patch.getTitle().upToLastOccurrenceOf(".pd", false, true), titleBar.getBounds(), Justification::centred);'
    _title_new = '// Koboss: title hidden'
    if _title_needle in _src and _title_new not in _src:
        _src = _src.replace(_title_needle, _title_new)
        print("Koboss patch: hid title text")

    # 3b. NO robar el foco del teclado en koboss: PluginMode::keyPressed hace
    #     grabKeyboardFocus() en CADA tecla -> el host (Ableton) nunca recibe las
    #     notas MIDI del teclado del ordenador. Para koboss, pasar la tecla y no robar.
    _kbfocus_needle = '''            setKioskMode(false);
            return true;
        }
        grabKeyboardFocus();

        return false;
    }'''
    _kbfocus_new = '''            setKioskMode(false);
            return true;
        }
        if (isKoboss()) {
            if (isKobossDelay() && handleKobossDelayKey(key)) return true;
            return false; // Koboss: no robar foco -> el host recibe teclas/MIDI
        }
        grabKeyboardFocus();

        return false;
    }'''
    if _kbfocus_needle in _src and "Koboss: no robar foco" not in _src:
        _src = _src.replace(_kbfocus_needle, _kbfocus_new, 1)
        print("Koboss patch: keyPressed no roba el foco del teclado")

    # 4. Replace render() body with our NVG custom drawing for chorus mode
    _render_needle = '    void render(NVGcontext* nvg, Rectangle<int> const area)\n    {\n        NVGScopedState scopedState(nvg);'
    _render_new = '    void render(NVGcontext* nvg, Rectangle<int> const area)\n    {\n        if (isKobossDelay()) { renderKobossDelay(nvg); return; }\n        if (isKobossChorus()) { renderKobossChorus(nvg); return; }\n        NVGScopedState scopedState(nvg);'
    if _render_needle in _src and "renderKobossChorus(nvg)" not in _src:
        _src = _src.replace(_render_needle, _render_new, 1)
        print("Koboss patch: render() delegates to renderKobossChorus")

    # 5. Hook handleKobossClick into existing mouseDown
    _mousedown_needle = 'void mouseDown(MouseEvent const& e) override\n    {\n\n        if (scaleComboBox.contains(e.getEventRelativeTo(&scaleComboBox).getPosition()) || !e.mods.isLeftButtonDown())'
    _mousedown_new = 'void mouseDown(MouseEvent const& e) override\n    {\n        if (handleKobossClick(e)) return;\n\n        if (scaleComboBox.contains(e.getEventRelativeTo(&scaleComboBox).getPosition()) || !e.mods.isLeftButtonDown())'
    if _mousedown_needle in _src and "handleKobossClick(e)" not in _src:
        _src = _src.replace(_mousedown_needle, _mousedown_new, 1)
        print("Koboss patch: hooked mouseDown")

    # 5b. Hook handleKobossDrag into existing mouseDrag
    _mousedrag_needle = 'void mouseDrag(MouseEvent const& e) override\n    {\n        if (!isDraggingWindow)\n            return;'
    _mousedrag_new = 'void mouseDrag(MouseEvent const& e) override\n    {\n        if (handleKobossDrag(e)) return;\n        if (!isDraggingWindow)\n            return;'
    if _mousedrag_needle in _src and "handleKobossDrag(e)" not in _src:
        _src = _src.replace(_mousedrag_needle, _mousedrag_new, 1)
        print("Koboss patch: hooked mouseDrag")

    # 5c. Hook handleKobossUp into existing mouseUp
    _mouseup_needle = 'void mouseUp(MouseEvent const& e) override\n    {\n        isDraggingWindow = false;\n    }'
    _mouseup_new = 'void mouseUp(MouseEvent const& e) override\n    {\n        if (isKobossDelay()) handleKobossDelayUp();\n        kobossKnobDragging = -1;\n        isDraggingWindow = false;\n    }\n\n    void mouseWheelMove(MouseEvent const& e, MouseWheelDetails const& wheel) override\n    {\n        if (isKobossDelay() && handleKobossDelayWheel(e, wheel)) return;\n    }'
    if _mouseup_needle in _src and "kobossKnobDragging = -1" not in _src:
        _src = _src.replace(_mouseup_needle, _mouseup_new, 1)
        print("Koboss patch: hooked mouseUp + mouseWheelMove")

    # 6. Inject custom UI methods before paint() definition
    _custom_ui_marker = "// Koboss Chorus custom UI"
    if _custom_ui_marker not in _src:
        _custom_ui_block = '''    // Koboss Chorus custom UI (NVG drawing)
    // NOTE: editor->pd->kobossActivePreset/OutWet/OutGain live on editor->pd (PluginProcessor)
    // so they persist when the editor is closed and reopened.
    bool kobossInSettings = false;
    int kobossKnobDragging = -1;
    int kobossDragStartY = 0;
    float kobossDragStartValue = 0.0f;

    bool isKobossDelay() const { return cnv && cnv->patch.getTitle().containsIgnoreCase("delay"); }
    bool isKobossChorus() const { return cnv && cnv->patch.getTitle().containsIgnoreCase("chorus"); }
    bool isKoboss() const { return isKobossDelay() || isKobossChorus(); }

    // Repaint continuo (~60fps) para animar el visualizador iso del delay
    struct KbDelayTimer : public juce::Timer {
        PluginMode* owner = nullptr;
        void timerCallback() override {
            if (owner != nullptr && owner->editor != nullptr)
                owner->editor->nvgSurface.invalidateAll();
        }
    };
    KbDelayTimer kobossDelayTimer;

    juce::Rectangle<float> kobossButton(int idx) const {
        constexpr float cellSize = 44.0f;
        constexpr float gap = 6.0f;
        constexpr float totalW = 3.0f * cellSize + 2.0f * gap;
        float const startX = ((float)getWidth() - totalW) * 0.5f;
        float const btnY = 50.0f;
        return juce::Rectangle<float>(startX + (float)idx * (cellSize + gap), btnY, cellSize, cellSize);
    }

    int kobossPresetAt(juce::Point<int> p) const {
        for (int i = 0; i < 3; ++i)
            if (kobossButton(i).contains(p.toFloat())) return i;
        return -1;
    }

    juce::Rectangle<int> kobossIconBounds() const {
        return juce::Rectangle<int>(getWidth() - 32, 8, 24, 24);
    }

    juce::Point<float> kobossKnobCenter(int idx) const {
        float const W = (float)getWidth();
        return juce::Point<float>(idx == 0 ? W * 0.30f : W * 0.70f, 95.0f);
    }
    static constexpr float kobossKnobRadius = 28.0f;

    int kobossKnobAt(juce::Point<int> p) const {
        for (int i = 0; i < 2; ++i) {
            auto c = kobossKnobCenter(i);
            float dx = (float)p.x - c.x;
            float dy = (float)p.y - c.y;
            if (dx*dx + dy*dy <= (kobossKnobRadius + 4) * (kobossKnobRadius + 4)) return i;
        }
        return -1;
    }

    static NVGcolor nvgHex(uint32_t rgb, float a = 1.0f) {
        return nvgRGBA((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF, (unsigned char)(a * 255.0f));
    }

    void renderKobossIcon(NVGcontext* nvg) {
        auto r = kobossIconBounds();
        float cx = (float)r.getCentreX();
        float cy = (float)r.getCentreY();
        nvgFillColor(nvg, nvgHex(0x8a8a8a));
        nvgStrokeColor(nvg, nvgHex(0x8a8a8a));
        nvgStrokeWidth(nvg, 1.4f);
        if (kobossInSettings) {
            // close X
            nvgBeginPath(nvg);
            nvgMoveTo(nvg, cx - 4.5f, cy - 4.5f);
            nvgLineTo(nvg, cx + 4.5f, cy + 4.5f);
            nvgMoveTo(nvg, cx + 4.5f, cy - 4.5f);
            nvgLineTo(nvg, cx - 4.5f, cy + 4.5f);
            nvgStroke(nvg);
        } else {
            // three dots
            for (int i = 0; i < 3; ++i) {
                nvgBeginPath(nvg);
                nvgCircle(nvg, cx - 6.0f + (float)i * 6.0f, cy, 1.5f);
                nvgFill(nvg);
            }
        }
    }

    void renderKobossHeader(NVGcontext* nvg) {
        nvgFontFace(nvg, "Inter-Bold");
        nvgFontSize(nvg, 14.0f);
        nvgTextAlign(nvg, NVG_ALIGN_LEFT | NVG_ALIGN_TOP);
        nvgFillColor(nvg, nvgHex(0x1a1a1a));
        nvgText(nvg, 22, 14, "KOBOSS", nullptr);

        float bounds[4];
        nvgTextBounds(nvg, 22, 14, "KOBOSS", nullptr, bounds);
        nvgFontFace(nvg, "Inter-Regular");
        nvgFontSize(nvg, 10.5f);
        nvgFillColor(nvg, nvgHex(0x8a8a8a));
        nvgText(nvg, bounds[2] + 8, 17, "CHORUS", nullptr);
    }

    void renderKobossFooter(NVGcontext* nvg) {
        float const W = (float)getWidth();
        float const H = (float)getHeight();
        nvgFontFace(nvg, "Inter-Regular");
        nvgFontSize(nvg, 9.0f);
        nvgFillColor(nvg, nvgHex(0x8a8a8a));
        nvgTextAlign(nvg, NVG_ALIGN_LEFT | NVG_ALIGN_BOTTOM);
        nvgText(nvg, 22, H - 12, "KOBOSSBEATS.COM", nullptr);
        nvgTextAlign(nvg, NVG_ALIGN_RIGHT | NVG_ALIGN_BOTTOM);
        nvgText(nvg, W - 22, H - 12, "v0.2.1", nullptr);
    }

    void renderKobossKnob(NVGcontext* nvg, float cx, float cy, float value,
                          const char* label, const char* valueStr) {
        // outer ring background
        nvgBeginPath(nvg);
        nvgCircle(nvg, cx, cy, kobossKnobRadius);
        nvgStrokeColor(nvg, nvgHex(0xe5e5e5));
        nvgStrokeWidth(nvg, 1.0f);
        nvgStroke(nvg);

        // value arc — 270° sweep from -135° (bottom-left) clockwise
        float const PI = 3.14159265f;
        float startAngle = PI * 0.75f;     // 135° (bottom-left in screen coords)
        float endAngle   = startAngle + value * (PI * 1.5f); // up to top-right
        nvgBeginPath(nvg);
        nvgArc(nvg, cx, cy, kobossKnobRadius - 2.0f, startAngle, endAngle, NVG_HOLE);
        nvgStrokeColor(nvg, nvgHex(0x1a1a1a));
        nvgStrokeWidth(nvg, 2.5f);
        nvgStroke(nvg);

        // indicator tick at end angle
        float ix = cx + std::cos(endAngle) * (kobossKnobRadius - 8.0f);
        float iy = cy + std::sin(endAngle) * (kobossKnobRadius - 8.0f);
        nvgBeginPath(nvg);
        nvgCircle(nvg, ix, iy, 2.5f);
        nvgFillColor(nvg, nvgHex(0xff6a3d));
        nvgFill(nvg);

        // value text inside
        nvgFontFace(nvg, "Inter-Bold");
        nvgFontSize(nvg, 11.0f);
        nvgFillColor(nvg, nvgHex(0x1a1a1a));
        nvgTextAlign(nvg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);
        nvgText(nvg, cx, cy, valueStr, nullptr);

        // label below knob
        nvgFontFace(nvg, "Inter-SemiBold");
        nvgFontSize(nvg, 9.0f);
        nvgFillColor(nvg, nvgHex(0x8a8a8a));
        nvgTextAlign(nvg, NVG_ALIGN_CENTER | NVG_ALIGN_TOP);
        nvgText(nvg, cx, cy + kobossKnobRadius + 8.0f, label, nullptr);
    }

    void renderKobossChorus(NVGcontext* nvg) {
        float const W = (float)getWidth();
        float const H = (float)getHeight();

        // Background
        nvgBeginPath(nvg);
        nvgRect(nvg, 0, 0, W, H);
        nvgFillColor(nvg, nvgHex(0xfafaf7));
        nvgFill(nvg);

        renderKobossHeader(nvg);
        renderKobossIcon(nvg);

        if (kobossInSettings) {
            // SETTINGS PAGE: two knobs
            char wetBuf[16];
            std::snprintf(wetBuf, sizeof(wetBuf), "%d%%", (int)std::round(editor->pd->kobossOutWet * 100.0f));
            auto cWet = kobossKnobCenter(0);
            renderKobossKnob(nvg, cWet.x, cWet.y, editor->pd->kobossOutWet, "WET", wetBuf);

            // gain knob — value 0..1 maps to 0..2x linear (0.5 = unity = 0 dB)
            char gainBuf[16];
            float linear = editor->pd->kobossOutGain * 2.0f;
            if (linear < 0.001f) {
                std::snprintf(gainBuf, sizeof(gainBuf), "-inf");
            } else {
                float dB = 20.0f * std::log10(linear);
                std::snprintf(gainBuf, sizeof(gainBuf), "%+.1f", dB);
            }
            auto cGain = kobossKnobCenter(1);
            renderKobossKnob(nvg, cGain.x, cGain.y, editor->pd->kobossOutGain, "GAIN  dB", gainBuf);
        } else {
            // PRESETS PAGE
            const char* numbers[] = { "01", "02", "03" };
            const char* labels[]  = { "SUBTLE", "CLASSIC", "WARM" };
            for (int i = 0; i < 3; ++i) {
                auto const btn = kobossButton(i);
                bool const active = (editor->pd->kobossActivePreset == i);
                nvgBeginPath(nvg);
                nvgRoundedRect(nvg, btn.getX(), btn.getY(), btn.getWidth(), btn.getHeight(), 4.0f);
                if (active) {
                    nvgFillColor(nvg, nvgHex(0x1a1a1a));
                    nvgFill(nvg);
                } else {
                    nvgStrokeColor(nvg, nvgHex(0xbababa));
                    nvgStrokeWidth(nvg, 1.0f);
                    nvgStroke(nvg);
                }
                nvgFontFace(nvg, "Inter-Bold");
                nvgFontSize(nvg, 16.0f);
                nvgTextAlign(nvg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);
                nvgFillColor(nvg, active ? nvgHex(0xfafaf7) : nvgHex(0x1a1a1a));
                nvgText(nvg, btn.getCentreX(), btn.getCentreY(), numbers[i], nullptr);
                if (active) {
                    float dotCx = btn.getRight() - 9.0f;
                    float dotCy = btn.getY() + 9.0f;
                    nvgBeginPath(nvg);
                    nvgCircle(nvg, dotCx, dotCy, 6.0f);
                    nvgFillColor(nvg, nvgHex(0xff6a3d, 0.20f));
                    nvgFill(nvg);
                    nvgBeginPath(nvg);
                    nvgCircle(nvg, dotCx, dotCy, 4.0f);
                    nvgFillColor(nvg, nvgHex(0xff6a3d, 0.40f));
                    nvgFill(nvg);
                    nvgBeginPath(nvg);
                    nvgCircle(nvg, dotCx, dotCy, 2.5f);
                    nvgFillColor(nvg, nvgHex(0xff6a3d));
                    nvgFill(nvg);
                }
                nvgFontFace(nvg, "Inter-SemiBold");
                nvgFontSize(nvg, 9.0f);
                nvgTextAlign(nvg, NVG_ALIGN_CENTER | NVG_ALIGN_TOP);
                nvgFillColor(nvg, active ? nvgHex(0x1a1a1a) : nvgHex(0x8a8a8a));
                nvgText(nvg, btn.getCentreX(), btn.getBottom() + 8, labels[i], nullptr);
            }
        }

        renderKobossFooter(nvg);
    }

    bool handleKobossClick(juce::MouseEvent const& e) {
        if (isKobossDelay()) return handleKobossDelayClick(e);
        if (!isKobossChorus()) return false;
        auto const p = e.getPosition();

        // icon (toggle settings)
        if (kobossIconBounds().contains(p)) {
            kobossInSettings = !kobossInSettings;
            if (editor != nullptr) editor->nvgSurface.invalidateAll();
            return true;
        }

        if (kobossInSettings) {
            int const k = kobossKnobAt(p);
            if (k >= 0) {
                // Double-click resets to default
                if (e.getNumberOfClicks() >= 2) {
                    float defaultVal = (k == 0) ? 1.0f : 0.5f;
                    const char* sendName = (k == 0) ? "out_wet" : "out_gain";
                    if (k == 0) editor->pd->kobossOutWet = defaultVal;
                    else        editor->pd->kobossOutGain = defaultVal;
                    if (editor != nullptr && editor->pd != nullptr) {
                        editor->pd->sendFloat(sendName, defaultVal);
                    }
                    if (editor != nullptr) editor->nvgSurface.invalidateAll();
                    kobossKnobDragging = -1;
                    return true;
                }
                kobossKnobDragging = k;
                kobossDragStartY = p.y;
                kobossDragStartValue = (k == 0) ? editor->pd->kobossOutWet : editor->pd->kobossOutGain;
                return true;
            }
        } else {
            int const idx = kobossPresetAt(p);
            if (idx >= 0 && idx != editor->pd->kobossActivePreset) {
                editor->pd->kobossActivePreset = idx;
                if (editor != nullptr && editor->pd != nullptr) {
                    editor->pd->sendFloat("preset", static_cast<float>(idx));
                }
                if (editor != nullptr) editor->nvgSurface.invalidateAll();
            }
            return true;
        }
        return true;
    }

    bool handleKobossDrag(juce::MouseEvent const& e) {
        if (isKobossDelay()) return handleKobossDelayDrag(e);
        if (kobossKnobDragging < 0) return false;
        int const deltaY = kobossDragStartY - e.getPosition().y;
        float newVal = juce::jlimit(0.0f, 1.0f, kobossDragStartValue + (float)deltaY / 150.0f);

        // Magnetic snap to default value (small zone — allows fine ±0.2 dB adjustments)
        float defaultVal = (kobossKnobDragging == 0) ? 1.0f : 0.5f;
        if (std::abs(newVal - defaultVal) < 0.010f) newVal = defaultVal;

        const char* sendName = nullptr;
        if (kobossKnobDragging == 0) {
            editor->pd->kobossOutWet = newVal;
            sendName = "out_wet";
        } else {
            editor->pd->kobossOutGain = newVal;
            sendName = "out_gain";
        }
        if (editor != nullptr && editor->pd != nullptr) {
            editor->pd->sendFloat(sendName, newVal);
        }
        if (editor != nullptr) editor->nvgSurface.invalidateAll();
        return true;
    }

    '''
        # Inyectar el bloque del Koboss Delay (render iso + controles + handlers)
        _delay_path = Path("../render-koboss-delay.cpp")
        if _delay_path.exists():
            _delay_block = _delay_path.read_text(encoding='utf-8')
            _dm = "// KBDELAY-BLOCK-START"
            if _dm in _delay_block:
                _delay_block = _delay_block[_delay_block.index(_dm):]
            # Inyectar versión desde config.json como #define (fuente única de verdad)
            _kb_version = "unknown"
            try:
                import json as _json
                _cfg = _json.loads(Path("config.json").read_text(encoding='utf-8'))
                if isinstance(_cfg, list) and _cfg:
                    _kb_version = _cfg[0].get("version", "unknown")
            except Exception:
                pass
            _version_define = f'#define KOBOSS_DELAY_VERSION "v{_kb_version}"\n'
            _delay_block = _version_define + _delay_block
            _custom_ui_block = _custom_ui_block + "\n    // ===== Koboss Delay custom UI =====\n" + _delay_block + "\n"
            print(f"Koboss patch: appended Koboss Delay UI block (v{_kb_version})")
        else:
            print("Koboss patch: WARNING render-koboss-delay.cpp no encontrado")
        _paint_marker = '    void paint(Graphics& g) override\n    {'
        if _paint_marker in _src:
            _src = _src.replace(_paint_marker, _custom_ui_block + _paint_marker, 1)
            print("Koboss patch: inserted custom Koboss UI methods")

    _plugin_mode_h.write_text(_src, encoding='utf-8')

    # 6b. Don't reserve titlebar height in plugin window size (so editor matches patch dimensions)
    _size_needle = 'auto newHeight = static_cast<int>(height * scale) + titlebarHeight + nativeTitleBarHeight;'
    _size_new = 'auto newHeight = static_cast<int>(height * scale) + (isKoboss() ? 0 : titlebarHeight) + nativeTitleBarHeight;'
    if _size_needle in _src and "isKoboss() ? 0 : titlebarHeight" not in _src:
        _src = _src.replace(_size_needle, _size_new, 1)
        _plugin_mode_h.write_text(_src, encoding='utf-8')
        print("Koboss patch: removed titlebar reservation in chorus size")

    # 6d. Save Koboss state in getStateInformation / restore in setStateInformation (survives project reopen)
    _proc_cpp = Path("plugdata/Source/PluginProcessor.cpp")
    if _proc_cpp.exists():
        _pcpp = _proc_cpp.read_text(encoding='utf-8')
        _get_needle = 'void PluginProcessor::getStateInformation(MemoryBlock& destData)\n{\n    setThis();\n\n    // Store pure-data and parameter state\n    MemoryOutputStream ostream(destData, false);'
        _get_new = '''void PluginProcessor::getStateInformation(MemoryBlock& destData)
{
    setThis();

    // Store pure-data and parameter state
    MemoryOutputStream ostream(destData, false);

    // Koboss state header v8: + cadena en serie (fxMix[4] + order[4])
    ostream.writeString("KBS8");
    ostream.writeInt(kobossActivePreset);
    ostream.writeFloat(kobossOutWet);
    ostream.writeFloat(kobossOutGain);
    ostream.writeFloat(kbTime);
    ostream.writeFloat(kbFeedback);
    ostream.writeFloat(kbWidth);
    ostream.writeFloat(kbMix);
    ostream.writeFloat(kbAmount);
    ostream.writeInt(kbFx);
    ostream.writeInt(kbCurve);
    ostream.writeInt(kbSyncMode);
    ostream.writeFloat(kbTimeMs);
    ostream.writeInt(kbPingpong);
    for (int _i = 0; _i < 4; ++_i) ostream.writeFloat(kbFxA[_i]);
    for (int _i = 0; _i < 4; ++_i) ostream.writeFloat(kbFxB[_i]);
    ostream.writeFloat(kbOut);
    ostream.writeFloat(kbDuck);
    for (int _i = 0; _i < 4; ++_i) ostream.writeFloat(kbFxMix[_i]);
    for (int _i = 0; _i < 4; ++_i) ostream.writeInt(kbOrder[_i]);'''
        if _get_needle in _pcpp and "Koboss state header" not in _pcpp:
            _pcpp = _pcpp.replace(_get_needle, _get_new, 1)
            print("Koboss patch: getStateInformation saves koboss state")

        _set_needle = '    MemoryInputStream istream(data, sizeInBytes, false);\n\n    audioLock.enter();'
        _set_new = '''    MemoryInputStream istream(data, sizeInBytes, false);

    // Koboss state restore — read magic header if present (los sendFloat van DESPUES de cargar el patch)
    bool _kbssRestored = false;
    auto const _kbssMagicPos = istream.getPosition();
    auto const _kbssMagic = istream.readString();
    if (_kbssMagic == "KBS8" || _kbssMagic == "KBS7" || _kbssMagic == "KBS6" || _kbssMagic == "KBS5" || _kbssMagic == "KBS4" || _kbssMagic == "KBS3" || _kbssMagic == "KBS2") {
        kobossActivePreset = istream.readInt();
        kobossOutWet = istream.readFloat();
        kobossOutGain = istream.readFloat();
        kbTime = istream.readFloat();
        kbFeedback = istream.readFloat();
        kbWidth = istream.readFloat();
        kbMix = istream.readFloat();
        kbAmount = istream.readFloat();
        kbFx = istream.readInt();
        kbCurve = istream.readInt();
        if (_kbssMagic == "KBS3" || _kbssMagic == "KBS4" || _kbssMagic == "KBS5" || _kbssMagic == "KBS6" || _kbssMagic == "KBS7") {
            kbSyncMode = istream.readInt();
            kbTimeMs = istream.readFloat();
        }
        if (_kbssMagic == "KBS4") {   // formato viejo: 1 par de params -> a todos
            kbPingpong = istream.readInt();
            float _a = istream.readFloat(), _b = istream.readFloat();
            for (int _i = 0; _i < 4; ++_i) { kbFxA[_i] = _a; kbFxB[_i] = _b; }
        }
        if (_kbssMagic == "KBS5" || _kbssMagic == "KBS6" || _kbssMagic == "KBS7" || _kbssMagic == "KBS8") {   // params POR efecto
            kbPingpong = istream.readInt();
            for (int _i = 0; _i < 4; ++_i) kbFxA[_i] = istream.readFloat();
            for (int _i = 0; _i < 4; ++_i) kbFxB[_i] = istream.readFloat();
        }
        if (_kbssMagic == "KBS6" || _kbssMagic == "KBS7" || _kbssMagic == "KBS8") kbOut = istream.readFloat();
        if (_kbssMagic == "KBS7" || _kbssMagic == "KBS8") kbDuck = istream.readFloat();
        if (_kbssMagic == "KBS8") {   // cadena en serie: mix por efecto + orden
            for (int _i = 0; _i < 4; ++_i) kbFxMix[_i] = istream.readFloat();
            for (int _i = 0; _i < 4; ++_i) kbOrder[_i] = istream.readInt();
        } else {   // formatos viejos (selector de UN efecto): derivar la cadena
            for (int _i = 0; _i < 4; ++_i) kbFxMix[_i] = (kbFx == _i) ? 1.0f : 0.0f;
            kbOrder[0] = 0; kbOrder[1] = 1; kbOrder[2] = 2; kbOrder[3] = 3;
            if (kbFx < 0 || kbFx > 3) kbFx = 0;   // kbFx pasa a ser el efecto enfocado
        }
        kbFreeze = 0;   // el freeze NO persiste activo (no abrir el proyecto en silencio)
        _kbssRestored = true;
    } else if (_kbssMagic == "KBSS") {
        kobossActivePreset = istream.readInt();
        kobossOutWet = istream.readFloat();
        kobossOutGain = istream.readFloat();
        _kbssRestored = true;
    } else {
        istream.setPosition(_kbssMagicPos);
    }

    audioLock.enter();'''
        if _set_needle in _pcpp and "Koboss state restore" not in _pcpp:
            _pcpp = _pcpp.replace(_set_needle, _set_new, 1)
            print("Koboss patch: setStateInformation restores koboss state")

        # Push del estado koboss al dsp DESPUES de cargar el patch (si no, el [loadbang]
        # del patch reescribe los defaults y pisa el estado restaurado → duplicar pista
        # o reabrir proyecto volvia al preset/wet por defecto aunque la GUI mostrara otro).
        _push_needle = '    audioLock.exit();\n\n    delete[] xmlData;'
        _push_new = '''    audioLock.exit();

    // Koboss: empujar estado restaurado al dsp DESPUES del loadbang del patch
    if (_kbssRestored) {
        sendFloat("preset", static_cast<float>(kobossActivePreset));
        sendFloat("out_wet", kobossOutWet);
        sendFloat("out_gain", kobossOutGain);
        // Estado restaurado -> parámetros del host (vía <NAME>-gui-s, normalizado). El patch
        // mapea y deja los params del DAW consistentes con el estado guardado.
        sendFloat("feedback-set", kbFeedback);
        sendFloat("mix-set", kbMix);
        sendFloat("pingpong-set", static_cast<float>(kbPingpong));
        static const char* _kFm[4] = {"filter_mix-set","drive_mix-set","crush_mix-set","chorus_mix-set"};
        static const char* _kFb[4] = {"filter_width-set","drive_tone-set","crush_rate-set","chorus_rate-set"};
        static const char* _kFa[4] = {"filter_freq-set","drive_amt-set","crush_bits-set","chorus_depth-set"};
        for (int _i = 0; _i < 4; ++_i) {   // fb ANTES de fa (cutoff del filtro: inlet caliente en a)
            sendFloat(_kFm[_i], kbFxMix[_i]);
            sendFloat(_kFb[_i], kbFxB[_i]);
            sendFloat(_kFa[_i], kbFxA[_i]);
        }
        for (int _k = 0; _k < 4; ++_k) sendFloat(("slot" + std::to_string(_k)).c_str(), static_cast<float>(kbOrder[_k]));
        sendFloat("out-set", kbOut);
        sendFloat("duck-set", kbDuck);
        sendFloat("freeze-set", 0.0f);
        if (kbSyncMode == 0) sendFloat("time", kbTimeMs);
    }

    delete[] xmlData;'''
        if _push_needle in _pcpp and "empujar estado restaurado al dsp" not in _pcpp:
            _pcpp = _pcpp.replace(_push_needle, _push_new, 1)
            print("Koboss patch: setStateInformation pushes koboss state after patch load")

        # Audio tap del Koboss Delay — RMS input (dry/centro) antes del proceso pd
        _tap_in_needle = '    auto targetBlock = dsp::AudioBlock<float>(buffer);'
        _tap_in_new = '''    // Koboss Delay audio tap — RMS input (dry/centro) antes del proceso pd
    {
        int _kbN = buffer.getNumSamples();
        if (_kbN > 0 && buffer.getNumChannels() > 0) {
            auto* _kbL = buffer.getReadPointer(0);
            auto* _kbR = buffer.getNumChannels() > 1 ? buffer.getReadPointer(1) : _kbL;
            float _kbS = 0.0f;
            for (int _i = 0; _i < _kbN; ++_i) { float _m = (_kbL[_i] + _kbR[_i]) * 0.5f; _kbS += _m * _m; }
            kobossDelayDryR.store(std::sqrt(_kbS / (float)_kbN));
        }
    }
    // Koboss Delay: BPM del host (fuente canonica para el sync; mas fiable que [r __playhead])
    if (auto* _kbPh = getPlayHead()) {
        auto _kbInfo = _kbPh->getPosition();
        if (_kbInfo.hasValue() && _kbInfo->getBpm().hasValue())
            kbHostBpm.store(static_cast<float>(*_kbInfo->getBpm()));
    }
    auto targetBlock = dsp::AudioBlock<float>(buffer);'''
        if _tap_in_needle in _pcpp and "Koboss Delay audio tap — RMS input" not in _pcpp:
            _pcpp = _pcpp.replace(_tap_in_needle, _tap_in_new, 1)
            print("Koboss patch: processBlock input RMS tap")

        # Audio tap del Koboss Delay — RMS output L/R (ecos ping pong) tras el proceso
        _tap_out_needle = '    auto const targetGain = volume->load();'
        _tap_out_new = '''    // Koboss Delay audio tap — RMS output L/R (ecos ping pong) tras el proceso
    {
        int _kbN = buffer.getNumSamples();
        if (_kbN > 0 && buffer.getNumChannels() > 0) {
            auto* _kbL = buffer.getReadPointer(0);
            auto* _kbR = buffer.getNumChannels() > 1 ? buffer.getReadPointer(1) : _kbL;
            float _kbSL = 0.0f, _kbSR = 0.0f, _kbPk = 0.0f;
            for (int _i = 0; _i < _kbN; ++_i) {
                _kbSL += _kbL[_i] * _kbL[_i]; _kbSR += _kbR[_i] * _kbR[_i];
                float _aL = std::abs(_kbL[_i]), _aR = std::abs(_kbR[_i]);
                if (_aL > _kbPk) _kbPk = _aL; if (_aR > _kbPk) _kbPk = _aR;
            }
            kobossDelayWL.store(std::sqrt(_kbSL / (float)_kbN));
            kobossDelayWR.store(std::sqrt(_kbSR / (float)_kbN));
            kobossDelayOutPeak.store(_kbPk);
        }
    }
    auto const targetGain = volume->load();'''
        if _tap_out_needle in _pcpp and "Koboss Delay audio tap — RMS output" not in _pcpp:
            _pcpp = _pcpp.replace(_tap_out_needle, _tap_out_new, 1)
            print("Koboss patch: processBlock output RMS tap")

        # 6f. Habilitar+nombrar los parámetros automatizables EN EL CONSTRUCTOR, para que el
        #     host los vea con nombre desde que instancia el plugin. plugdata nombra los params
        #     dinámicamente al cargar el patch (create), pero los hosts leen la lista al
        #     instanciar y se quedan con los nombres de construcción ("disabled_paramN") -> "none".
        _ctor_needle = '''    // General purpose automation parameters you can get by using "receive param1" etc.
    for (int n = 0; n < numParameters; n++) {
        auto* parameter = new PlugDataParameter(this, "param" + String(n + 1), 0.0f, false, n + 1, 0.0f, 1.0f);
        addParameter(parameter);
    }'''
        _ctor_new = _ctor_needle + '''

    // Koboss: 18 params automatizables — ranges nativos + defaults normalizados + setUnchanged
    {
        static const char* _kbN[] = {"mix","feedback","out","duck","pingpong","freeze",
            "fa0","fb0","fm0","fa1","fb1","fm1","fa2","fb2","fm2","fa3","fb3","fm3"};
        // Ranges NATIVOS (las mismas unidades que el patch recibe por [r NAME])
        static const float _kbMin[] = {0.f,0.f,0.f,0.f,0.f,0.f,
            0.f,0.f,0.f,0.f,0.f,0.f,0.f,0.f,0.f,0.f,0.f,0.f};
        static const float _kbMax[] = {100.f,100.f,2.f,1.f,1.f,1.f,
            1.f,1.f,1.f,1.f,1.f,1.f,1.f,1.f,1.f,1.f,1.f,1.f};
        // Defaults NORMALIZADOS (0-1): lo que getValue() devuelve y el host almacena
        // getUnscaledValue() = normalize * (max-min) + min = valor en unidades nativas
        static const float _kbDef[] = {0.35f,0.45f,0.5f,0.f,1.f,0.f,
            0.566f,1.f,0.43f,0.4f,0.5f,0.f,0.3f,0.5f,0.f,0.4f,0.4f,0.f};
        int _kbi = 0;
        for (auto* _p : getParameters()) {
            auto* _pp = dynamic_cast<PlugDataParameter*>(_p);
            if (!_pp || _pp->getTitle() == "volume") continue;
            if (_kbi >= 18) break;
            _pp->setEnabled(true);
            _pp->setName(_kbN[_kbi]);
            _pp->setIndex(_kbi + 1);
            _pp->setRange(_kbMin[_kbi], _kbMax[_kbi]);
            _pp->setDefaultValue(_kbDef[_kbi]);
            _pp->setValue(_kbDef[_kbi]);
            _pp->setUnchanged();  // CLAVE: evita que sendParameters pise el loadbang en el 1er bloque
            ++_kbi;
        }
        updateEnabledParameters();
    }'''
        if _ctor_needle in _pcpp and "Koboss: 18 params automatizables" not in _pcpp:
            _pcpp = _pcpp.replace(_ctor_needle, _ctor_new, 1)
            print("Koboss patch: ctor con ranges nativos + defaults normalizados")

        # 6g. Empuje EXPLÍCITO de los params automatizables al patch cada bloque. La ruta
        #     normal (enabledParameters + wasChanged en sendParameters) no llegaba al patch
        #     en hosts headless -> los valores de automatización no movían el DSP. Forzamos
        #     sendFloat("<title>", unscaled) para todos los params habilitados (no volume).
        _sp_needle = '''void PluginProcessor::sendParameters()
{
    ScopedLock lock(audioLock);
    for (auto* param : enabledParameters) {
        if (EXPECT_UNLIKELY(param->wasChanged())) {
            auto title = param->getTitle();
            sendFloat(title.data(), param->getUnscaledValue());
            param->setUnchanged();
        }
    }
    // Koboss: la automatización del host llega por aquí (sendFloat("<title>", valor) -> [r <title>]
    // en el patch). El patch enruta [r NAME] -> map -> [s NAMEv] -> DSP. Camino nativo de plugdata.
}'''
        _sp_new = '''void PluginProcessor::sendParameters()
{
    ScopedLock lock(audioLock);
    for (auto* param : enabledParameters) {
        if (EXPECT_UNLIKELY(param->wasChanged())) {
            auto title = param->getTitle();
            sendFloat(title.data(), param->getUnscaledValue());
            param->setUnchanged();
            // Koboss: sync GUI knob desde audio thread. wasChanged=true solo cuando el HOST
            // cambia el param (automatizacion); el drag del usuario NO llama setValueNotifyingHost
            // asi que no hay conflicto. float/int son atomic en plataformas modernas (x86/ARM64).
            const char* tn = title.data();
            float nv = param->getValue(); // 0..1 normalizado, igual que las vars GUI
            if      (!strcmp(tn,"mix"))      kbMix      = nv;
            else if (!strcmp(tn,"feedback")) kbFeedback = nv;
            else if (!strcmp(tn,"out"))      kbOut      = nv;
            else if (!strcmp(tn,"duck"))     kbDuck     = nv;
            else if (!strcmp(tn,"pingpong")) kbPingpong = (int)std::round(nv);
            else if (!strcmp(tn,"freeze"))   kbFreeze   = (int)std::round(nv);
            else if (!strcmp(tn,"fa0"))      kbFxA[0]   = nv;
            else if (!strcmp(tn,"fb0"))      kbFxB[0]   = nv;
            else if (!strcmp(tn,"fm0"))      kbFxMix[0] = nv;
            else if (!strcmp(tn,"fa1"))      kbFxA[1]   = nv;
            else if (!strcmp(tn,"fb1"))      kbFxB[1]   = nv;
            else if (!strcmp(tn,"fm1"))      kbFxMix[1] = nv;
            else if (!strcmp(tn,"fa2"))      kbFxA[2]   = nv;
            else if (!strcmp(tn,"fb2"))      kbFxB[2]   = nv;
            else if (!strcmp(tn,"fm2"))      kbFxMix[2] = nv;
            else if (!strcmp(tn,"fa3"))      kbFxA[3]   = nv;
            else if (!strcmp(tn,"fb3"))      kbFxB[3]   = nv;
            else if (!strcmp(tn,"fm3"))      kbFxMix[3] = nv;
        }
    }
}'''
        if _sp_needle in _pcpp and "Koboss: sync GUI knob" not in _pcpp:
            _pcpp = _pcpp.replace(_sp_needle, _sp_new, 1)
            print("Koboss patch: sendParameters sync GUI knobs desde automatizacion")

        _proc_cpp.write_text(_pcpp, encoding='utf-8')

    # 6b2. Fix de entorno: WelcomePanel.h captura un structured binding en una
    #      lambda (clang lo rechaza). Copiar a variable local. Necesario para que
    #      el build compile tras un checkout limpio del submodulo.
    _welcome_h = Path("plugdata/Source/Components/WelcomePanel.h")
    if _welcome_h.exists():
        _wh = _welcome_h.read_text(encoding='utf-8')
        _wneedle = '''                    for (auto& [name, file] : previousVersions) {
                        versionsSubMenu.addItem(name, [this, file] {
                            parent.editor->getTabComponent().openPatch(URL(file));
                        });
                    }'''
        _wnew = '''                    for (auto& [name, file] : previousVersions) {
                        auto fileCopy = file; // clang no permite capturar structured bindings
                        versionsSubMenu.addItem(name, [this, fileCopy] {
                            parent.editor->getTabComponent().openPatch(URL(fileCopy));
                        });
                    }'''
        if _wneedle in _wh and "auto fileCopy = file" not in _wh:
            _wh = _wh.replace(_wneedle, _wnew, 1)
            _welcome_h.write_text(_wh, encoding='utf-8')
            print("Koboss patch: WelcomePanel.h structured-binding capture fix")

    # 6c. Add persistent Koboss state to PluginProcessor (survives editor close/reopen)
    _processor_h = Path("plugdata/Source/PluginProcessor.h")
    if _processor_h.exists():
        _php = _processor_h.read_text(encoding='utf-8')
        _state_needle = 'class PluginProcessor final : public AudioProcessor\n    , public pd::Instance\n    , public SettingsFileListener {\npublic:\n    PluginProcessor();'
        _state_new = '''class PluginProcessor final : public AudioProcessor
    , public pd::Instance
    , public SettingsFileListener {
public:
    // Koboss Chorus persistent state (lives in processor so it survives editor close/reopen)
    int kobossActivePreset = 0;
    float kobossOutWet = 1.0f;
    float kobossOutGain = 0.5f;
    // Koboss Delay params (0..1 normalizados; kbFx/kbCurve enteros)
    float kbTime = 0.43f;      // en modo sync: posicion 0..1 del selector de division
    float kbFeedback = 0.35f;
    float kbWidth = 0.90f;
    float kbMix = 0.30f;
    float kbAmount = 0.55f;
    int kbFx = 0;              // efecto ENFOCADO en el editor (0=filter 1=drive 2=crush 3=chorus)
    int kbCurve = 1;
    int kbSyncMode = 1;         // 1 = sync (1/4..), 0 = free (ms)
    float kbTimeMs = 250.0f;    // en modo free: time en ms (30..2000)
    // Koboss Delay v2: ping-pong toggle + 2 params por efecto (sustituyen width/amount/curve)
    int kbPingpong = 1;         // 1 = ping-pong cruzado, 0 = estereo normal
    // params POR efecto (filter/drive/crush/chorus) — cada efecto recuerda los suyos
    float kbFxA[4] = { 0.566f, 0.4f, 0.5f, 0.45f };   // filter a = 1 kHz (como Ableton)
    float kbFxB[4] = { 1.0f, 0.4f, 0.4f, 0.4f };      // filter b = width 8 (abierto)
    // Koboss Delay v3: cadena en serie reordenable — mix por efecto + orden de las 4 posiciones
    float kbFxMix[4] = { 0.43f, 0.0f, 0.0f, 0.0f };   // mix dry/wet por efecto (0 = apagado)
    int   kbOrder[4] = { 0, 1, 2, 3 };                // id del efecto en cada posicion de la cadena
    float kbOut = 0.8f;        // salida: 0..1 -> dB (-24..+6), 0.8 = 0 dB
    int   kbFreeze = 0;        // 1 = congelar buffer (feedback infinito + sin entrada)
    float kbDuck = 0.0f;       // 0..1 = cantidad de sidechain del wet bajo la señal seca
    // Koboss Delay audio tap (lock-free, escrito en processBlock, leido por la GUI)
    std::atomic<float> kobossDelayDryR { 0.0f };
    std::atomic<float> kobossDelayWL { 0.0f };
    std::atomic<float> kobossDelayWR { 0.0f };
    std::atomic<float> kobossDelayOutPeak { 0.0f };  // pico |out| del último bloque (meter/clip)
    std::atomic<float> kbHostBpm { 0.0f };  // BPM del host (0 = no disponible)
    PluginProcessor();'''
        if _state_needle in _php and "kobossActivePreset" not in _php:
            _php = _php.replace(_state_needle, _state_new, 1)
            print("Koboss patch: added persistent state to PluginProcessor")
        if "#include <atomic>" not in _php:
            if "#pragma once" in _php:
                _php = _php.replace("#pragma once", "#pragma once\n#include <atomic>", 1)
            else:
                _php = "#include <atomic>\n" + _php
            print("Koboss patch: included <atomic> in PluginProcessor.h")
        if "static constexpr int numParameters = 512;" in _php:
            _php = _php.replace("static constexpr int numParameters = 512;",
                                "static constexpr int numParameters = 18;", 1)
            print("Koboss patch: numParameters 512 -> 18")
        elif "static constexpr int numParameters = 32;" in _php:
            _php = _php.replace("static constexpr int numParameters = 32;",
                                "static constexpr int numParameters = 18;", 1)
            print("Koboss patch: numParameters 32 -> 18")
        _processor_h.write_text(_php, encoding='utf-8')

    # 7. Make nvgSurface cover the FULL editor (no 40px gap reserved for plugdata toolbar)
    _editor_cpp = Path("plugdata/Source/PluginEditor.cpp")
    if _editor_cpp.exists():
        _ecpp = _editor_cpp.read_text(encoding='utf-8')
        _ec_changed = False
        _bounds_needle = 'nvgSurface.updateBounds(getLocalBounds().withTrimmedTop(pluginMode->isWindowFullscreen() ? 0 : 40));'
        _bounds_new = 'nvgSurface.updateBounds(getLocalBounds()); // Koboss: full editor, no toolbar gap'
        if _bounds_needle in _ecpp and "Koboss: full editor" not in _ecpp:
            _ecpp = _ecpp.replace(_bounds_needle, _bounds_new, 1)
            _ec_changed = True
            print("Koboss patch: nvgSurface covers full editor (no toolbar gap)")

        # RAÍZ del robo de foco MIDI: el constructor del editor hace
        # setWantsKeyboardFocus(true) -> AL CLICAR el plugin, el editor agarra el foco del
        # teclado y el host (Ableton) deja de recibir las notas del teclado del ordenador.
        # Este build es SOLO koboss (nunca usamos el editor de plugdata) -> false.
        _wkf_needle = '    setWantsKeyboardFocus(true);\n    commandManager.registerAllCommandsForTarget(this);'
        _wkf_new = '    setWantsKeyboardFocus(false); // Koboss: el host conserva el foco MIDI del teclado\n    commandManager.registerAllCommandsForTarget(this);'
        if _wkf_needle in _ecpp and "Koboss: el host conserva el foco MIDI" not in _ecpp:
            _ecpp = _ecpp.replace(_wkf_needle, _wkf_new, 1)
            _ec_changed = True
            print("Koboss patch: editor NO quiere foco de teclado (host conserva MIDI)")

        # RAÍZ REAL del MIDI muerto: PluginEditor::keyPressed devuelve TRUE para toda
        # tecla que no sea tab/space -> CONSUME la nota antes de que burbujee al holder
        # del wrapper AU (EditorCompHolder::keyPressed), que es quien la reenvia a Ableton
        # (codigo isAbletonLive de JUCE). En koboss no manejamos esas teclas -> return false
        # para que burbujeen y el host reciba el MIDI del teclado del ordenador.
        _kp_needle = '''bool PluginEditor::keyPressed(KeyPress const& key)
{
    if (!getCurrentCanvas())
        return false;'''
        _kp_new = '''bool PluginEditor::keyPressed(KeyPress const& key)
{
    if (pluginMode && pluginMode->isKoboss())
        return false; // Koboss: no consumir -> burbujea al wrapper AU (reenvio MIDI a Ableton)
    if (!getCurrentCanvas())
        return false;'''
        if _kp_needle in _ecpp and "burbujea al wrapper AU" not in _ecpp:
            _ecpp = _ecpp.replace(_kp_needle, _kp_new, 1)
            _ec_changed = True
            print("Koboss patch: PluginEditor::keyPressed no consume teclas (host recibe MIDI)")

        # NO robar el foco del teclado para koboss (que el host reciba MIDI/teclas).
        # broughtToFront() se dispara AL CLICAR el plugin -> era lo que robaba el foco.
        _bf_needle = '''void PluginEditor::broughtToFront()
{
    if (isShowing() || isOnDesktop())
        grabKeyboardFocus();'''
        _bf_new = '''void PluginEditor::broughtToFront()
{
    if (pluginMode && pluginMode->isKoboss()) return; // Koboss: no robar foco al host
    if (isShowing() || isOnDesktop())
        grabKeyboardFocus();'''
        if _bf_needle in _ecpp and "Koboss: no robar foco al host" not in _ecpp:
            _ecpp = _ecpp.replace(_bf_needle, _bf_new, 1)
            _ec_changed = True
            print("Koboss patch: broughtToFront no roba el foco")

        _ph_needle = '''void PluginEditor::parentHierarchyChanged()
{
    if (isShowing() || isOnDesktop())
        grabKeyboardFocus();
}'''
        _ph_new = '''void PluginEditor::parentHierarchyChanged()
{
    if (pluginMode && pluginMode->isKoboss()) return; // Koboss: no robar foco
    if (isShowing() || isOnDesktop())
        grabKeyboardFocus();
}'''
        if _ph_needle in _ecpp and "Koboss: no robar foco" not in _ph_needle and _ph_new not in _ecpp:
            _ecpp = _ecpp.replace(_ph_needle, _ph_new, 1)
            _ec_changed = True
            print("Koboss patch: parentHierarchyChanged no roba el foco")

        # timer de arranque (Linux/Logic) que agarra el foco
        _tf_needle = '''        if (auto* window = _this->getTopLevelComponent()) {
            window->toFront(false);
        }
        _this->grabKeyboardFocus();'''
        _tf_new = '''        if (auto* window = _this->getTopLevelComponent()) {
            window->toFront(false);
        }
        if (!(_this->pluginMode && _this->pluginMode->isKoboss()))
            _this->grabKeyboardFocus();'''
        if _tf_needle in _ecpp and "_this->pluginMode && _this->pluginMode->isKoboss()" not in _ecpp:
            _ecpp = _ecpp.replace(_tf_needle, _tf_new, 1)
            _ec_changed = True
            print("Koboss patch: timer de arranque no roba el foco")

        if _ec_changed:
            _editor_cpp.write_text(_ecpp, encoding='utf-8')

    # 7b. FIX primer-clic (macOS): la NSView de Metal de nanovg (OSUtils::MTLCreateView)
    # es una NSView genérica que NO sobrescribe acceptsFirstMouse: -> devuelve NO -> cuando
    # la ventana del plugin no es la activa, el PRIMER clic solo activa la ventana y no llega
    # al control (hay que clicar dos veces). Subclase que devuelve YES = el primer clic actúa.
    _osutils = Path("plugdata/Source/Utility/OSUtils.mm")
    if _osutils.exists():
        _osu = _osutils.read_text(encoding='utf-8')
        _fm_needle = '''void* OSUtils::MTLCreateView(void* parent, int x, int y, int width, int height)
{
    // Create child view
    NSView *childView = [[NSView alloc] initWithFrame:NSMakeRect(x, y, width, height)];'''
        _fm_new = '''@interface KobossFirstMouseView : NSView
@end
@implementation KobossFirstMouseView
- (BOOL)acceptsFirstMouse:(NSEvent*)event { return YES; }
@end

void* OSUtils::MTLCreateView(void* parent, int x, int y, int width, int height)
{
    // Create child view
    NSView *childView = [[KobossFirstMouseView alloc] initWithFrame:NSMakeRect(x, y, width, height)]; // Koboss: primer clic en ventana inactiva llega al control'''
        if _fm_needle in _osu and "KobossFirstMouseView" not in _osu:
            _osu = _osu.replace(_fm_needle, _fm_new, 1)
            _osutils.write_text(_osu, encoding='utf-8')
            print("Koboss patch: NSView de Metal acepta primer clic (acceptsFirstMouse)")

    # 8. Ensure required headers are included in PluginMode.h
    _src = _plugin_mode_h.read_text(encoding='utf-8')
    _hdr_changed = False
    if '#include "Utility/Fonts.h"' not in _src:
        _src = _src.replace('#include "PluginEditor.h"',
                            '#include "PluginEditor.h"\n#include "Utility/Fonts.h"',
                            1)
        _hdr_changed = True
        print("Koboss patch: included Fonts.h in PluginMode.h")
    if '#include <vector>' not in _src:
        _src = _src.replace('#include "PluginEditor.h"',
                            '#include "PluginEditor.h"\n#include <vector>\n#include <algorithm>\n#include <cmath>\n#include <cstdio>\n#include <atomic>\n#include <mutex>',
                            1)
        _hdr_changed = True
        print("Koboss patch: included STL headers in PluginMode.h")
    # (resize de ventana DESCARTADO de momento: plugdata oculta el resizer detrás del
    #  overlay de plugin-mode -> requiere meter mano profunda en su gestión de ventana,
    #  imposible de testear sin DAW aquí. Aplazado a post-lanzamiento.)
    if _hdr_changed:
        _plugin_mode_h.write_text(_src, encoding='utf-8')

system = platform.system()
if system == "Windows":
    cmake_compiler = ["-DCMAKE_C_COMPILER=cl", "-DCMAKE_CXX_COMPILER=cl"]
else:
    cmake_compiler = []

if args.generator == "xcode":
    cmake_generator = ["-GXcode"]
elif args.generator == "visualstudio":
    cmake_generator = ["-GVisual Studio 17 2022", "-A x64"]
    cmake_compiler = []
else:
    cmake_generator = ["-GNinja"]

plugdata_dir = Path("plugdata").resolve()
builds_parent_dir = plugdata_dir.parent

plugins_dir = os.path.join("plugdata", "Plugins")
build_output_dir = os.path.join("Build")
os.makedirs(build_output_dir, exist_ok=True)

if not plugdata_dir.is_dir():
    print(f"FATAL: plugdata directory not found at '{plugdata_dir}'. "
          f"Make sure you're running this script from the repo root and that "
          f"the plugdata submodule has been initialised (git submodule update --init).")
    sys.exit(1)

for plugin in plugins_config:
    name = plugin["name"]
    zip_path = Path(plugin["path"]).resolve()
    patch = plugin["patch"]
    formats = plugin.get("formats", [])
    is_fx = plugin.get("type", "").lower() == "fx"

    build_dir = builds_parent_dir / f"{args.generator}-{name}"
    print(f"\nProcessing: {name}")

    author = plugin.get("author", False)
    version = plugin.get("version", "1.0.0")
    enable_gem = plugin.get("enable_gem", False)
    enable_sfizz = plugin.get("enable_sfizz", False)
    enable_ffmpeg = plugin.get("enable_ffmpeg", False)

    cmake_configure = [
        "cmake",
        "-GNinja",
        *cmake_generator,
        *cmake_compiler,
        f"-B{build_dir}",
        f"-DCUSTOM_PLUGIN_NAME={name}",
        f"-DCUSTOM_PLUGIN_PATCH={patch}",
        f"-DCUSTOM_PLUGIN_PATH={zip_path}",
        f"-DCUSTOM_PLUGIN_COMPANY={author}",
        f"-DCUSTOM_PLUGIN_VERSION={version}",
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DENABLE_GEM={'1' if enable_gem else '0'}",
        f"-DENABLE_SFIZZ={'1' if enable_sfizz else '0'}",
        f"-DENABLE_FFMPEG={'1' if enable_ffmpeg else '0'}",
        f"-DCUSTOM_PLUGIN_IS_FX={'1' if is_fx else '0'}"
    ]

    if args.compiler_launcher:
        cmake_configure.append(f"-DCMAKE_C_COMPILER_LAUNCHER={args.compiler_launcher}")
        cmake_configure.append(f"-DCMAKE_CXX_COMPILER_LAUNCHER={args.compiler_launcher}")

    result_configure = subprocess.run(cmake_configure, cwd=plugdata_dir)
    if result_configure.returncode != 0:
        print(f"Failed cmake configure for {name}")
        continue

    if not args.configure_only:
        for fmt in formats:
            if system != "Darwin" and fmt == "AU":
                continue
            target = f"plugdata_{'fx_' if is_fx else ''}{fmt}"
            if fmt == "Standalone":
                target = "plugdata_standalone"

            cmake_build = [
                "cmake",
                "--build", str(build_dir),
                "--target", target,
                "--config Release"
            ]
            print(f"Building target: {target}")
            result_build = subprocess.run(cmake_build, cwd=plugdata_dir)
            if result_build.returncode != 0:
                print(f"Failed to build target: {target}")
            else:
                print(f"Successfully built: {target}")
            format_path = os.path.join(plugins_dir, fmt)
            target_dir = os.path.join(build_output_dir, fmt)

            if fmt == "Standalone":
                if os.path.isdir(format_path):
                    if os.path.exists(target_dir):
                        shutil.rmtree(target_dir)
                    shutil.copytree(format_path, target_dir)
            else:
                extension = ""
                if fmt == "VST3":
                    extension = ".vst3"
                elif fmt == "AU":
                    extension = ".component"
                elif fmt == "LV2":
                    extension = ".lv2"
                elif fmt == "CLAP":
                    extension = ".clap"

                plugin_filename = name + extension
                os.makedirs(target_dir, exist_ok=True)
                src = os.path.join(format_path, plugin_filename)
                dst = os.path.join(target_dir, plugin_filename)
                if os.path.isdir(src):
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                else:
                    if os.path.exists(dst):
                        os.remove(dst)
                    shutil.copy2(src, dst)
