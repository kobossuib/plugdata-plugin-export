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
    _resized_hide_new = '''        } else if (isKobossChorus()) {
            // Koboss: hide chrome; NVG render() will do all drawing
            pluginModeScale = 1.0f;
            titleBar.setVisible(false);
            scaleComboBox.setVisible(false);
            editorButton->setVisible(false);
            cnv->setVisible(false);
            // Let clicks fall through nvgSurface to reach PluginMode's mouseDown
            editor->nvgSurface.setInterceptsMouseClicks(false, false);
            setInterceptsMouseClicks(true, true);
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

    # 4. Replace render() body with our NVG custom drawing for chorus mode
    _render_needle = '    void render(NVGcontext* nvg, Rectangle<int> const area)\n    {\n        NVGScopedState scopedState(nvg);'
    _render_new = '    void render(NVGcontext* nvg, Rectangle<int> const area)\n    {\n        if (isKobossChorus()) { renderKobossChorus(nvg); return; }\n        NVGScopedState scopedState(nvg);'
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
    _mouseup_new = 'void mouseUp(MouseEvent const& e) override\n    {\n        kobossKnobDragging = -1;\n        isDraggingWindow = false;\n    }'
    if _mouseup_needle in _src and "kobossKnobDragging = -1" not in _src:
        _src = _src.replace(_mouseup_needle, _mouseup_new, 1)
        print("Koboss patch: hooked mouseUp")

    # 6. Inject custom UI methods before paint() definition
    _custom_ui_marker = "// Koboss Chorus custom UI"
    if _custom_ui_marker not in _src:
        _custom_ui_block = '''    // Koboss Chorus custom UI (NVG drawing)
    int kobossActivePreset = 0;
    bool kobossInSettings = false;
    float kobossOutWet = 1.0f;
    float kobossOutGain = 0.5f;
    int kobossKnobDragging = -1;
    int kobossDragStartY = 0;
    float kobossDragStartValue = 0.0f;

    bool isKobossChorus() const { return true; }

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
        nvgText(nvg, W - 22, H - 12, "v0.2.0", nullptr);
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
            std::snprintf(wetBuf, sizeof(wetBuf), "%d%%", (int)std::round(kobossOutWet * 100.0f));
            auto cWet = kobossKnobCenter(0);
            renderKobossKnob(nvg, cWet.x, cWet.y, kobossOutWet, "WET", wetBuf);

            // gain knob — value 0..1 maps to 0..2x linear (0.5 = unity = 0 dB)
            char gainBuf[16];
            float linear = kobossOutGain * 2.0f;
            if (linear < 0.001f) {
                std::snprintf(gainBuf, sizeof(gainBuf), "-inf");
            } else {
                float dB = 20.0f * std::log10(linear);
                std::snprintf(gainBuf, sizeof(gainBuf), "%+.1f", dB);
            }
            auto cGain = kobossKnobCenter(1);
            renderKobossKnob(nvg, cGain.x, cGain.y, kobossOutGain, "GAIN  dB", gainBuf);
        } else {
            // PRESETS PAGE
            const char* numbers[] = { "01", "02", "03" };
            const char* labels[]  = { "SUBTLE", "CLASSIC", "WARM" };
            for (int i = 0; i < 3; ++i) {
                auto const btn = kobossButton(i);
                bool const active = (kobossActivePreset == i);
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
                    if (k == 0) kobossOutWet = defaultVal;
                    else        kobossOutGain = defaultVal;
                    if (editor != nullptr && editor->pd != nullptr) {
                        editor->pd->sendFloat(sendName, defaultVal);
                    }
                    if (editor != nullptr) editor->nvgSurface.invalidateAll();
                    kobossKnobDragging = -1;
                    return true;
                }
                kobossKnobDragging = k;
                kobossDragStartY = p.y;
                kobossDragStartValue = (k == 0) ? kobossOutWet : kobossOutGain;
                return true;
            }
        } else {
            int const idx = kobossPresetAt(p);
            if (idx >= 0 && idx != kobossActivePreset) {
                kobossActivePreset = idx;
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
        if (kobossKnobDragging < 0) return false;
        int const deltaY = kobossDragStartY - e.getPosition().y;
        float newVal = juce::jlimit(0.0f, 1.0f, kobossDragStartValue + (float)deltaY / 150.0f);

        // Magnetic snap to default value (small zone — allows fine ±0.2 dB adjustments)
        float defaultVal = (kobossKnobDragging == 0) ? 1.0f : 0.5f;
        if (std::abs(newVal - defaultVal) < 0.010f) newVal = defaultVal;

        const char* sendName = nullptr;
        if (kobossKnobDragging == 0) {
            kobossOutWet = newVal;
            sendName = "out_wet";
        } else {
            kobossOutGain = newVal;
            sendName = "out_gain";
        }
        if (editor != nullptr && editor->pd != nullptr) {
            editor->pd->sendFloat(sendName, newVal);
        }
        if (editor != nullptr) editor->nvgSurface.invalidateAll();
        return true;
    }

    '''
        _paint_marker = '    void paint(Graphics& g) override\n    {'
        if _paint_marker in _src:
            _src = _src.replace(_paint_marker, _custom_ui_block + _paint_marker, 1)
            print("Koboss patch: inserted custom Chorus UI methods")

    _plugin_mode_h.write_text(_src, encoding='utf-8')

    # 6b. Don't reserve titlebar height in plugin window size (so editor matches patch dimensions)
    _size_needle = 'auto newHeight = static_cast<int>(height * scale) + titlebarHeight + nativeTitleBarHeight;'
    _size_new = 'auto newHeight = static_cast<int>(height * scale) + (isKobossChorus() ? 0 : titlebarHeight) + nativeTitleBarHeight;'
    if _size_needle in _src and "isKobossChorus() ? 0 : titlebarHeight" not in _src:
        _src = _src.replace(_size_needle, _size_new, 1)
        _plugin_mode_h.write_text(_src, encoding='utf-8')
        print("Koboss patch: removed titlebar reservation in chorus size")

    # 7. Make nvgSurface cover the FULL editor (no 40px gap reserved for plugdata toolbar)
    _editor_cpp = Path("plugdata/Source/PluginEditor.cpp")
    if _editor_cpp.exists():
        _ecpp = _editor_cpp.read_text(encoding='utf-8')
        _bounds_needle = 'nvgSurface.updateBounds(getLocalBounds().withTrimmedTop(pluginMode->isWindowFullscreen() ? 0 : 40));'
        _bounds_new = 'nvgSurface.updateBounds(getLocalBounds()); // Koboss: full editor, no toolbar gap'
        if _bounds_needle in _ecpp and "Koboss: full editor" not in _ecpp:
            _ecpp = _ecpp.replace(_bounds_needle, _bounds_new, 1)
            _editor_cpp.write_text(_ecpp, encoding='utf-8')
            print("Koboss patch: nvgSurface covers full editor (no toolbar gap)")

    # 8. Ensure Fonts.h is included (used elsewhere too — keep for safety)
    if '#include "Utility/Fonts.h"' not in _src:
        _src = _plugin_mode_h.read_text(encoding='utf-8')
        _src = _src.replace('#include "PluginEditor.h"',
                            '#include "PluginEditor.h"\n#include "Utility/Fonts.h"',
                            1)
        _plugin_mode_h.write_text(_src, encoding='utf-8')
        print("Koboss patch: included Fonts.h in PluginMode.h")

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
