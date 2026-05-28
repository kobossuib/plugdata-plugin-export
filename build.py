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

# Koboss patches to PluginMode.h
# IMPORTANT order: more-specific patches (with longer context) must run BEFORE
# more-generic patches that would otherwise gobble up their needles.
_plugin_mode_h = Path("plugdata/Source/PluginMode.h")
if _plugin_mode_h.exists():
    _src = _plugin_mode_h.read_text(encoding='utf-8')

    # 0a. Hide titleBar + cnv when chorus (rewrites resized()'s else branch — must run FIRST)
    _resized_hide_needle = '        } else {\n            float scale = getWidth() / width;\n            pluginModeScale = scale;\n            \n            scaleComboBox.setVisible(true);\n            editorButton->setVisible(true);\n\n            titleBar.setBounds(0, 0, getWidth(), titlebarHeight);\n            scaleComboBox.setBounds(8, 8, 74, titlebarHeight - 16);\n            editorButton->setBounds(getWidth() - titlebarHeight, 0, titlebarHeight, titlebarHeight);'
    _resized_hide_new = '''        } else if (isKobossChorus()) {
            // Koboss: hide titleBar and canvas — custom paint does everything
            pluginModeScale = 1.0f;
            titleBar.setBounds(0, 0, 0, 0);
            scaleComboBox.setVisible(false);
            editorButton->setVisible(false);
            cnv->setBounds(-9999, -9999, 1, 1);
        } else {
            float scale = getWidth() / width;
            pluginModeScale = scale;

            scaleComboBox.setVisible(true);
            editorButton->setVisible(true);

            titleBar.setBounds(0, 0, getWidth(), titlebarHeight);
            scaleComboBox.setBounds(8, 8, 74, titlebarHeight - 16);
            editorButton->setBounds(getWidth() - titlebarHeight, 0, titlebarHeight, titlebarHeight);'''
    if _resized_hide_needle in _src and "Koboss: hide titleBar and canvas" not in _src:
        _src = _src.replace(_resized_hide_needle, _resized_hide_new, 1)
        print("Koboss patch: resized() hides titleBar and canvas for chorus")

    # 1. Hide the "Plugin Info" (P) button (replaces remaining editorButton lines)
    _needle = 'editorButton->setBounds(getWidth() - titlebarHeight, 0, titlebarHeight, titlebarHeight);'
    _new = 'editorButton->setBounds(-9999, -9999, 1, 1); // Koboss: hide info button'
    if _needle in _src and _new not in _src:
        _src = _src.replace(_needle, _new)
        print("Koboss patch: hid PluginMode info button")

    # 2. Hide centered patch title text
    _title_needle = 'g.drawText(cnv->patch.getTitle().upToLastOccurrenceOf(".pd", false, true), titleBar.getBounds(), Justification::centred);'
    _title_new = '// Koboss: title hidden'
    if _title_needle in _src and _title_new not in _src:
        _src = _src.replace(_title_needle, _title_new)
        print("Koboss patch: hid PluginMode title text")

    # 2b. Inject handleKobossClick call into existing mouseDown
    _mousedown_needle = 'void mouseDown(MouseEvent const& e) override\n    {\n\n        if (scaleComboBox.contains(e.getEventRelativeTo(&scaleComboBox).getPosition()) || !e.mods.isLeftButtonDown())'
    _mousedown_new = 'void mouseDown(MouseEvent const& e) override\n    {\n        if (handleKobossClick(e)) return;\n\n        if (scaleComboBox.contains(e.getEventRelativeTo(&scaleComboBox).getPosition()) || !e.mods.isLeftButtonDown())'
    if _mousedown_needle in _src and "handleKobossClick(e)" not in _src:
        _src = _src.replace(_mousedown_needle, _mousedown_new, 1)
        print("Koboss patch: hooked mouseDown for chorus clicks")

    # 3. Inject custom Koboss Chorus UI (paint + mouseDown) before paint() definition
    _custom_ui_marker = "// Koboss Chorus custom UI"
    if _custom_ui_marker not in _src:
        _custom_ui_block = '''    // Koboss Chorus custom UI ─────────────────────────────────────────────────
    int kobossActivePreset = 0;

    bool isKobossChorus() const {
        if (!cnv) return false;
        auto const name = cnv->patch.getTitle().upToLastOccurrenceOf(".pd", false, true);
        return name == "chorus" || name == "Koboss Chorus";
    }

    juce::Rectangle<float> kobossButton(int idx) const {
        constexpr int cellSize = 44;
        constexpr int gap = 6;
        constexpr int totalW = 3 * cellSize + 2 * gap;
        int const startX = (getWidth() - totalW) / 2;
        int const btnY = 50;
        return juce::Rectangle<float>(static_cast<float>(startX + idx * (cellSize + gap)),
                                      static_cast<float>(btnY),
                                      static_cast<float>(cellSize),
                                      static_cast<float>(cellSize));
    }

    int kobossPresetAt(juce::Point<int> p) const {
        for (int i = 0; i < 3; ++i)
            if (kobossButton(i).contains(p.toFloat())) return i;
        return -1;
    }

    void paintKobossChorus(Graphics& g) {
        using juce::Colour;
        using juce::Justification;
        using juce::Rectangle;

        auto const W = static_cast<float>(getWidth());
        auto const H = static_cast<float>(getHeight());

        auto const bgColor = Colour(0xfffafaf7);
        auto const fgColor = Colour(0xff1a1a1a);
        auto const subColor = Colour(0xff8a8a8a);
        auto const dimColor = Colour(0xffbababa);
        auto const accentColor = Colour(0xffff6a3d);

        // Background
        g.fillAll(bgColor);

        // Header: KOBOSS (bold) + CHORUS (light grey)
        g.setColour(fgColor);
        g.setFont(Fonts::getBoldFont().withHeight(14.0f).withExtraKerningFactor(0.04f));
        g.drawText("KOBOSS", 22, 14, 80, 18, Justification::topLeft, false);

        g.setColour(subColor);
        g.setFont(Fonts::getDefaultFont().withHeight(9.5f).withExtraKerningFactor(0.20f));
        g.drawText("CHORUS", 80, 17, 60, 14, Justification::topLeft, false);

        // Version (top right, monospace, grey)
        g.setColour(subColor);
        g.setFont(Fonts::getMonospaceFont().withHeight(9.0f).withExtraKerningFactor(0.05f));
        g.drawText(JUCE_STRINGIFY(CUSTOM_PLUGIN_VERSION), (int)W - 70, 17, 48, 14, Justification::topRight, false);

        // 3 preset buttons
        constexpr float cornerRadius = 4.0f;
        constexpr float borderThickness = 1.5f;
        char const* numbers[] = { "01", "02", "03" };
        char const* labels[]  = { "SUBTLE", "CLASSIC", "WARM" };

        for (int i = 0; i < 3; ++i) {
            auto const btn = kobossButton(i);
            bool const active = (kobossActivePreset == i);

            // Fill or border
            if (active) {
                g.setColour(fgColor);
                g.fillRoundedRectangle(btn, cornerRadius);
            } else {
                g.setColour(dimColor);
                g.drawRoundedRectangle(btn, cornerRadius, 1.0f);
            }

            // Border (active also gets stronger border)
            if (active) {
                g.setColour(fgColor);
                g.drawRoundedRectangle(btn, cornerRadius, borderThickness);
            }

            // Number inside button (centered)
            g.setColour(active ? bgColor : fgColor);
            g.setFont(Fonts::getBoldFont().withHeight(15.0f).withExtraKerningFactor(0.02f));
            g.drawText(numbers[i],
                       (int)btn.getX(), (int)btn.getY(),
                       (int)btn.getWidth(), (int)btn.getHeight(),
                       Justification::centred, false);

            // Dot — naranja con halo, solo en el activo
            if (active) {
                float const dotCx = btn.getRight() - 9.0f;
                float const dotCy = btn.getY() + 9.0f;
                // Halo
                g.setColour(accentColor.withAlpha(0.25f));
                g.fillEllipse(dotCx - 6.0f, dotCy - 6.0f, 12.0f, 12.0f);
                g.setColour(accentColor.withAlpha(0.45f));
                g.fillEllipse(dotCx - 4.5f, dotCy - 4.5f, 9.0f, 9.0f);
                // Core
                g.setColour(accentColor);
                g.fillEllipse(dotCx - 2.5f, dotCy - 2.5f, 5.0f, 5.0f);
            }

            // Sub-label below button (SUBTLE / CLASSIC / WARM)
            g.setColour(active ? fgColor.withAlpha(0.85f) : subColor);
            g.setFont(Fonts::getSemiBoldFont().withHeight(8.5f).withExtraKerningFactor(0.18f));
            g.drawText(labels[i],
                       (int)btn.getX() - 10,
                       (int)btn.getBottom() + 8,
                       (int)btn.getWidth() + 20,
                       12,
                       Justification::centred, false);
        }

        // Footer
        g.setColour(subColor);
        g.setFont(Fonts::getMonospaceFont().withHeight(8.5f).withExtraKerningFactor(0.10f));
        g.drawText("KOBOSSBEATS.COM", 22, (int)H - 18, 150, 12, Justification::topLeft, false);
        g.drawText("FOR STEREO", (int)W - 100, (int)H - 18, 78, 12, Justification::topRight, false);
    }

    bool handleKobossClick(juce::MouseEvent const& e) {
        if (!isKobossChorus()) return false;
        int const p = kobossPresetAt(e.getPosition());
        if (p >= 0 && p != kobossActivePreset) {
            kobossActivePreset = p;
            editor->pd->sendFloat("preset", static_cast<float>(p));
            repaint();
        }
        return true; // swallow click in chorus mode
    }
    // ──────────────────────────────────────────────────────────────────────────

    '''
        # Insert before the first paint(Graphics& g) method
        _paint_marker = '    void paint(Graphics& g) override\n    {'
        if _paint_marker in _src:
            _src = _src.replace(_paint_marker, _custom_ui_block + _paint_marker, 1)
            print("Koboss patch: inserted custom Chorus UI methods")

    # 4. Make paint() early-return into paintKobossChorus when patch is chorus
    _paint_early_needle = '    void paint(Graphics& g) override\n    {\n        if (!cnv)\n            return;'
    _paint_early_new = '''    void paint(Graphics& g) override
    {
        if (isKobossChorus()) {
            paintKobossChorus(g);
            return;
        }
        if (!cnv)
            return;'''
    if _paint_early_needle in _src:
        _src = _src.replace(_paint_early_needle, _paint_early_new, 1)
        print("Koboss patch: paint() early-returns for chorus")

    _plugin_mode_h.write_text(_src, encoding='utf-8')

    # Ensure Fonts.h is included in PluginMode.h
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
