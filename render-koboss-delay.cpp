// ============================================================================
// Koboss Delay — bloque de GUI nativa (NVG/C++) para inyectar en PluginMode.h
// ============================================================================
// Port del visual JS `proto/index-iso-d.html` a NVG: panel reactivo isométrico
// "tall columns" (height field) que sube con dry (centro) + ecos wet (lados),
// con paleta cálida Koboss y naranja como acumulación de feedback. Incluye la
// fila de controles inferior (5 knobs + selector de 7 efectos) y los handlers
// de ratón propios del delay.
//
// CÓMO LO INTEGRA build.py (Task 3 del plan):
//   - Inyecta TODO este archivo como bloque dentro de la class PluginMode,
//     junto al bloque "// Koboss Chorus custom UI".
//   - render():            if (isKobossDelay()) { renderKobossDelay(nvg); return; }
//   - handleKobossClick():  if (isKobossDelay()) return handleKobossDelayClick(e);
//   - handleKobossDrag():   if (isKobossDelay()) return handleKobossDelayDrag(e);
//   - Estado kb* + atomics del audio tap viven en PluginProcessor (editor->pd->...).
//   - reutiliza renderKobossKnob()/nvgHex() definidos por el bloque del Chorus.
// ============================================================================

// KBDELAY-BLOCK-START (build.py inyecta desde aquí hacia abajo)
// ── structs (declarar ANTES de los miembros que los usan) ───────────────────
// col = color del efecto activo en el momento de emitir el pulso -> el cambio de
// color es GRADUAL (las ondas viejas mantienen su color; las nuevas salen del centro
// con el nuevo). type 0=wet 1=dry ; side 0=L 1=R 2=center.
struct DelayPulse { double tEmit; int side; int type; float energy; uint32_t col; };
// wave = fase de onda firmada (-1..1) en ese punto: oscila al pasar el anillo del eco
// -> el COLOR ondula con las ondas reales que salen del centro/lados.
struct DelayHeight { float hDry; float hWet; float mag; float cr, cg, cb; float wave; };
// ── preset: snapshot completo. sync=1 -> timeN = kbTime(0..1, division); sync=0 -> ms.
//    fx = efecto activo (0 filter,1 drive,2 crush,3 chorus). a[]/b[] = params por efecto.
struct DelayPreset { const char* name; int sync; float timeN, fb, mix, out; int pp, fx;
                     float a[4], b[4]; float mx[4]; int ord[4]; };

// tabla de presets (static local -> válida dentro de la clase inyectada).
// a[]/b[] por efecto: filter(a=freq log, b=width), drive, crush, chorus.
// mx[] = mix dry/wet por efecto (0 = apagado). ord[] = orden de la cadena.
// fx = efecto enfocado en el editor al cargar. div sync: kbTime = idx/7.
static const DelayPreset* kobossDelayPresetTable(int& n) {
    static const int O[4] = {0,1,2,3};  // orden por defecto
    #define M0 {0,0,0,0}
    static const DelayPreset P[] = {
      // ── BÁSICOS (sin efecto: lucen el carácter de cinta + lo-cut + stereo width) ──
      {"1/4 ping pong", 1, 3/7.f, .35f,.35f,.8f, 1,0, {.5f,.4f,.5f,.45f},{.4f,.4f,.4f,.4f}, M0, {0,1,2,3}},
      {"1/8 ping pong", 1, 5/7.f, .35f,.32f,.8f, 1,0, {.5f,.4f,.5f,.45f},{.4f,.4f,.4f,.4f}, M0, {0,1,2,3}},
      {"slapback",      0, 110.f, .12f,.35f,.8f, 0,0, {.5f,.4f,.5f,.45f},{.4f,.4f,.4f,.4f}, M0, {0,1,2,3}},
      {"quarter",       1, 3/7.f, .32f,.32f,.8f, 0,0, {.5f,.4f,.5f,.45f},{.4f,.4f,.4f,.4f}, M0, {0,1,2,3}},
      {"1/8 mono",      1, 5/7.f, .30f,.30f,.8f, 0,0, {.5f,.4f,.5f,.45f},{.4f,.4f,.4f,.4f}, M0, {0,1,2,3}},
      {"16th tight",    1, 1.0f,  .28f,.28f,.8f, 1,0, {.5f,.4f,.5f,.45f},{.4f,.4f,.4f,.4f}, M0, {0,1,2,3}},
      {"triplet",       1, 6/7.f, .40f,.35f,.8f, 1,0, {.5f,.4f,.5f,.45f},{.4f,.4f,.4f,.4f}, M0, {0,1,2,3}},
      {"dotted 1/8",    1, 4/7.f, .40f,.35f,.8f, 1,0, {.5f,.4f,.5f,.45f},{.4f,.4f,.4f,.4f}, M0, {0,1,2,3}},
      {"long half",     1, 1/7.f, .45f,.38f,.8f, 1,0, {.5f,.4f,.5f,.45f},{.4f,.4f,.4f,.4f}, M0, {0,1,2,3}},
      {"doubler",       0, 45.f,  .06f,.30f,.8f, 0,0, {.5f,.4f,.5f,.45f},{.4f,.4f,.4f,.4f}, M0, {0,1,2,3}},
      // ── CON CARÁCTER (un efecto, filtro band-pass nuevo) ──
      {"dark dub",      1, 5/7.f, .55f,.40f,.8f, 1, 0, {.30f,.4f,.5f,.45f},{.40f,.4f,.4f,.4f}, {.7f,0,0,0}, {0,1,2,3}},
      {"telephone",     1, 5/7.f, .38f,.42f,.8f, 0, 0, {.50f,.4f,.5f,.45f},{.15f,.4f,.4f,.4f}, {1.f,0,0,0}, {0,1,2,3}},
      {"warm drive",    1, 3/7.f, .45f,.35f,.8f, 1, 1, {.6f,.35f,.5f,.45f},{1.f,.40f,.4f,.4f}, {0,.4f,0,0}, {1,0,2,3}},
      {"space chorus",  1, 3/7.f, .48f,.45f,.78f,1, 3, {.6f,.4f,.5f,.55f},{1.f,.4f,.4f,.45f}, {0,0,0,.5f}, {0,1,2,3}},
      {"ambient wash",  1, 1/7.f, .58f,.45f,.78f,1, 3, {.6f,.4f,.5f,.60f},{1.f,.4f,.4f,.35f}, {0,0,0,.6f}, {0,1,2,3}},
      {"lofi echo",     1, 5/7.f, .42f,.40f,.8f, 1, 2, {.6f,.4f,.55f,.45f},{1.f,.4f,.45f,.4f}, {0,0,.4f,0}, {0,1,2,3}},
      // ── EXTREMOS / RAROS ──
      {"1/8 overdub",   1, 5/7.f, .72f,.45f,.78f,1,0, {.5f,.4f,.5f,.45f},{.4f,.4f,.4f,.4f}, M0, {0,1,2,3}},
      {"infinity",      1, 3/7.f, .90f,.40f,.78f,1,0, {.5f,.4f,.5f,.45f},{.4f,.4f,.4f,.4f}, M0, {0,1,2,3}},
      {"dub sirena",    1, 5/7.f, .78f,.45f,.78f,1, 0, {.40f,.4f,.5f,.45f},{.20f,.4f,.4f,.4f}, {.8f,0,0,0}, {0,1,2,3}},
      {"acid drive",    1, 5/7.f, .60f,.40f,.78f,1, 1, {.45f,.80f,.5f,.45f},{.25f,.45f,.4f,.4f}, {.5f,.6f,0,0}, {1,0,2,3}},
      {"crush echo",    1, 5/7.f, .55f,.42f,.78f,1, 2, {.6f,.4f,.75f,.45f},{1.f,.4f,.60f,.4f}, {0,0,.5f,0}, {0,1,2,3}},
      {"deep chorus",   1, 2/7.f, .60f,.50f,.78f,1, 3, {.6f,.4f,.5f,.70f},{1.f,.4f,.4f,.50f}, {0,0,0,.6f}, {0,1,2,3}},
      {"runaway",       1, 1/7.f, .92f,.50f,.76f,1,0, {.5f,.4f,.5f,.45f},{.4f,.4f,.4f,.4f}, M0, {0,1,2,3}},
      // ── COMBOS (varios efectos a la vez, cadena reordenada) ──
      {"dub + crush",   1, 5/7.f, .60f,.42f,.78f,1, 0, {.30f,.4f,.70f,.45f},{.45f,.4f,.55f,.4f}, {.5f,0,.4f,0}, {2,0,1,3}},
      {"warm space",    1, 3/7.f, .50f,.42f,.78f,1, 3, {.6f,.35f,.5f,.55f},{1.f,.40f,.4f,.45f}, {0,.35f,0,.5f}, {1,3,0,2}},
      {"phone drive",   1, 5/7.f, .48f,.40f,.78f,1, 0, {.52f,.55f,.5f,.45f},{.22f,.40f,.4f,.4f}, {.6f,.4f,0,0}, {0,1,2,3}},
      {"lofi wash",     1, 2/7.f, .58f,.46f,.76f,1, 2, {.45f,.4f,.65f,.55f},{.35f,.4f,.55f,.45f},{.4f,0,.5f,.5f}, {2,0,3,1}},
    };
    #undef M0
    (void)O;
    n = (int)(sizeof(P)/sizeof(P[0])); return P;
}

// ── member state ────────────────────────────────────────────────────────────
std::vector<DelayPulse> kobossDelayPulses;
double kobossDelayLastTime = 0.0;
float  kobossDelayPrevDryPeak = 0.0f;
float  kobossDelayPrevWetL = 0.0f;
float  kobossDelayPrevWetR = 0.0f;
float  kobossDelayEnvDry = 0.0f;
float  kobossDelayEnvWetL = 0.0f;
float  kobossDelayEnvWetR = 0.0f;
double kobossDelayNextEmit = 0.0;
int    kobossDelayRecirc = 0;
int    kobossDelayKnobDrag = -1;   // 0..4 = time/fbk/width/mix/amt
int    kobossDelayDragY = 0;
float  kobossDelayDragV = 0.0f;
float  kobossDelayLastSentMs = -1.0f;  // ultimo time(ms) enviado a pd (sync host bpm)
bool   kobossDelayInitSent = false;    // one-shot: empuja estado C++ -> patch al arrancar
// caches del push CONTINUO desde render() (el handler de raton no entrega fiable los
// sendFloat en el plugin; render() sí — el time ya iba así). -999 fuerza el 1er envio.
float  kobossDelayLastFb = -999.0f, kobossDelayLastMix = -999.0f;
float  kobossDelayLastOut = -999.0f, kobossDelayLastDuck = -999.0f;
int    kobossDelayLastPp = -999, kobossDelayLastFreeze = -999;
// caches de la cadena: params por efecto (fa/fb/fm) + orden (slot). -999 fuerza 1er envío.
float  kbLastFa[4] = {-999,-999,-999,-999}, kbLastFb[4] = {-999,-999,-999,-999}, kbLastFm[4] = {-999,-999,-999,-999};
int    kbLastSlot[4] = {-999,-999,-999,-999};
float  kobossDelayOutMeter = 0.0f;   // nivel de salida suavizado (meter)
float  kobossDelayClipHold = 0.0f;   // hold del indicador de clip (decae)
float  kobossDelayCurCol[3] = { 202.0f, 191.0f, 159.0f };  // color actual suavizado, 0..255 (tan)
int    kobossActivePresetLast = -1;                      // para detectar cambio de preset
// === panel FX (esquema clásico: abre/cierra a un lado) ===
bool   kobossDelayFxExpanded = false;                    // panel FX colapsado por defecto (CTA "+ fx")
bool   kobossDelayXYActive = false;                      // arrastrando el XY pad del efecto enfocado
bool   kobossDelayMixDrag = false;                       // arrastrando la barra de mix del efecto enfocado
// presets de USUARIO (guardados en disco, ~/.../Koboss/Delay Presets/*.kbd)
std::vector<DelayPreset> kobossUserPresets;
std::vector<std::string> kobossUserNames;                // strings vivos para DelayPreset.name
bool   kobossUserLoaded = false;
bool        kobossDelayRenaming = false;   // modo rename activo (input de nombre)
std::string kobossDelayRenameText;         // texto acumulado por el usuario
// Acento PROPIO de la zona FX (teal): distinto del naranja de marca para diferenciar
// que es una sección opcional. Un único sitio — cambia el hex a ojo si quieres otro tono.
static NVGcolor kobossFxAccent() { return nvgRGB(0x1f,0x9e,0x93); }   // teal
// filtro band-pass tipo Ableton (portado del mockup): a=freq centro (X), b=width (Y).
// smootherstep quintic, sin ramas duras -> transición continua, sin escalón.
static float kobossSmoothstep(float e0, float e1, float x) {
    float t = (x - e0) / (e1 - e0); t = t < 0 ? 0 : (t > 1 ? 1 : t);
    return t*t*t*(t*(t*6.0f - 15.0f) + 10.0f);
}
static float kobossFilterMag(float fr, float a, float b) {
    float width = 0.06f + b*0.70f, tr = 0.13f;
    float lo = a - width, hi = a + width;
    float rise = kobossSmoothstep(lo - tr, lo + tr, fr);
    float fall = 1.0f - kobossSmoothstep(hi - tr, hi + tr, fr);
    return rise * fall;
}

// ── aviso de nueva versión (fetch único en background, sin telemetría) ──────
// Al abrir el plugin se lee 1 vez un JSON remoto con la última versión. Si es más
// nueva que la compilada, el footer muestra "vX.Y disponible →" (naranja) que abre
// la web. No instala nada — solo avisa. Anónimo (un GET, sin enviar datos).
// Estado y helpers son STATIC: el bloque se inyecta como miembros de PluginMode, y
// el hilo de fondo no captura 'this' -> deben ser accesibles sin instancia. Además
// así el chequeo es único y compartido (sin riesgo si se cierra una instancia).
static std::atomic<bool>& kobossDelayUpd()    { static std::atomic<bool> f{false}; return f; }  // hay versión nueva
static char*              kobossDelayUpdVer() { static char b[16] = {0}; return b; }            // "v0.5.0" a mostrar
// parse "v1.2.3"/"1.2.3" -> entero comparable (1*10000 + 2*100 + 3). -1 si inválido.
static long kobossDelayVerNum(const char* s) {
    if (!s) return -1;
    while (*s == 'v' || *s == 'V' || *s == ' ') ++s;
    int part[3] = {0,0,0}, idx = 0; bool any = false;
    for (; *s && idx < 3; ++s) {
        if (*s >= '0' && *s <= '9') { part[idx] = part[idx]*10 + (*s - '0'); any = true; }
        else if (*s == '.') { ++idx; }
        else break;
    }
    return any ? (long)part[0]*10000 + part[1]*100 + part[2] : -1;
}
static void kobossDelayCheckUpdate() {
    static std::once_flag once;
    std::call_once(once, []{
        juce::Thread::launch([]{
            juce::URL url("https://files.kobossbeats.com/koboss-delay-version.json");
            auto txt = url.readEntireTextStream(false);   // bloquea -> por eso va en hilo
            if (txt.isEmpty()) return;
            auto json = juce::JSON::parse(txt);
            auto latest = json.getProperty("version", juce::var()).toString();   // "0.5.0"
            if (latest.isEmpty()) return;
            long localN  = kobossDelayVerNum(KOBOSS_DELAY_VERSION);
            long remoteN = kobossDelayVerNum(latest.toRawUTF8());
            if (remoteN > localN && localN >= 0) {
                juce::String disp = latest.startsWithChar('v') ? latest : ("v" + latest);
                disp.copyToUTF8(kobossDelayUpdVer(), 16);
                kobossDelayUpd().store(true);
            }
        });
    });
}

// ── helpers de color / dibujo iso ───────────────────────────────────────────
static NVGcolor kobossMixHex(uint32_t a, uint32_t b, float t) {
    float ar = ((a >> 16) & 0xFF), ag = ((a >> 8) & 0xFF), ab = (a & 0xFF);
    float br = ((b >> 16) & 0xFF), bg = ((b >> 8) & 0xFF), bb = (b & 0xFF);
    return nvgRGB((unsigned char)(ar + (br - ar) * t),
                  (unsigned char)(ag + (bg - ag) * t),
                  (unsigned char)(ab + (bb - ab) * t));
}
static uint32_t kobossColToHex(NVGcolor c) {
    // ⚠️ En ESTE nanovg NVGcolor.r/g/b ya son 0..255 (uint8), no 0..1 -> empaquetar
    // directo (multiplicar por 255 era el bug del arcoíris: desbordaba al byte de arriba).
    return (((unsigned)c.r)<<16) | (((unsigned)c.g)<<8) | ((unsigned)c.b);
}
// color base por efecto (el visualizador SÍ usa varios colores — es señal, no UI):
// filter=verde · drive=naranja/rojo · crush=amarillo · chorus=rosa · dry=tan cálido.
static uint32_t kobossDelayFxHex(int fx) {
    switch (fx) {
        case 0: return 0x57a85f;  // filter -> verde
        case 1: return 0xd8552e;  // drive  -> naranja/rojo
        case 2: return 0xe0b321;  // crush  -> amarillo
        case 3: return 0xcf5fa0;  // chorus -> rosa
    }
    return 0xcabf9f;
}
// HSL (h 0..360, s/l 0..1) -> r,g,b en 0..255 (escala de este nanovg)
static void kobossHSL(float h, float s, float l, float& r, float& g, float& b) {
    s = s < 0 ? 0 : (s > 1 ? 1 : s);  l = l < 0 ? 0 : (l > 1 ? 1 : l);
    float c = (1.0f - std::fabs(2.0f*l - 1.0f)) * s;
    float hp = std::fmod(std::fmod(h, 360.0f) + 360.0f, 360.0f) / 60.0f;
    float x = c * (1.0f - std::fabs(std::fmod(hp, 2.0f) - 1.0f));
    float r1=0,g1=0,b1=0;
    if (hp<1){r1=c;g1=x;} else if (hp<2){r1=x;g1=c;} else if (hp<3){g1=c;b1=x;}
    else if (hp<4){g1=x;b1=c;} else if (hp<5){r1=x;b1=c;} else {r1=c;b1=x;}
    float m = l - c/2.0f;
    r = (r1+m)*255.0f; g = (g1+m)*255.0f; b = (b1+m)*255.0f;
}
// hue (0..360) de un color en 0..255
static float kobossHue(float r, float g, float b) {
    r/=255.0f; g/=255.0f; b/=255.0f;
    float mx = std::fmax(r, std::fmax(g,b)), mn = std::fmin(r, std::fmin(g,b)), d = mx-mn;
    if (d < 1e-6f) return 0.0f;
    float h;
    if (mx==r) h = std::fmod((g-b)/d, 6.0f);
    else if (mx==g) h = (b-r)/d + 2.0f;
    else h = (r-g)/d + 4.0f;
    h *= 60.0f; if (h<0) h += 360.0f; return h;
}

void renderKobossDelayColumn(NVGcontext* nvg, float cx, float cy,
                              float tw, float th, float h,
                              NVGcolor top, NVGcolor left, NVGcolor right) {
    if (h < 0.5f) {
        // sólo la tapa (rombo) a la altura base
        nvgBeginPath(nvg);
        nvgMoveTo(nvg, cx,      cy - th);
        nvgLineTo(nvg, cx + tw, cy);
        nvgLineTo(nvg, cx,      cy + th);
        nvgLineTo(nvg, cx - tw, cy);
        nvgClosePath(nvg);
        nvgFillColor(nvg, top); nvgFill(nvg);
        return;
    }
    // cara izquierda
    nvgBeginPath(nvg);
    nvgMoveTo(nvg, cx - tw, cy);
    nvgLineTo(nvg, cx,      cy + th);
    nvgLineTo(nvg, cx,      cy + th - h);
    nvgLineTo(nvg, cx - tw, cy - h);
    nvgClosePath(nvg);
    nvgFillColor(nvg, left); nvgFill(nvg);
    // cara derecha
    nvgBeginPath(nvg);
    nvgMoveTo(nvg, cx + tw, cy);
    nvgLineTo(nvg, cx,      cy + th);
    nvgLineTo(nvg, cx,      cy + th - h);
    nvgLineTo(nvg, cx + tw, cy - h);
    nvgClosePath(nvg);
    nvgFillColor(nvg, right); nvgFill(nvg);
    // tapa rombo
    nvgBeginPath(nvg);
    nvgMoveTo(nvg, cx,      cy - th - h);
    nvgLineTo(nvg, cx + tw, cy - h);
    nvgLineTo(nvg, cx,      cy + th - h);
    nvgLineTo(nvg, cx - tw, cy - h);
    nvgClosePath(nvg);
    nvgFillColor(nvg, top); nvgFill(nvg);
}

DelayHeight kobossDelayHeightAt(float gx, float gz, double now,
                                 float envDry, float envWetL, float envWetR,
                                 const std::vector<DelayPulse>& pulses,
                                 float curR, float curG, float curB) {
    // ── "WAVE-SHAPE" (lab v3, validado contra el bloque del DAW) ─────────────────
    // El bloque macizo era inevitable mientras la altura fuera SUMA de energía rectificada
    // (siempre positiva -> con señal fuerte todas las columnas saturan). La solución de
    // raíz: la altura ONDULA con la FASE de la onda (wave −1..1) -> crestas arriba, valles
    // abajo. Por diseño matemático NUNCA se vuelve bloque, aunque la energía sature.
    constexpr float SPEED   = 14.0f;
    constexpr float FREQ    = 0.72f;
    constexpr float AMP_MUL = 44.0f;
    constexpr float DECAY_D = 0.26f;
    constexpr float DECAY_T = 0.40f;
    constexpr float INST_MUL = 6.0f;   // dome MUY bajo -> solo aporta color/presencia, no altura

    // AC-coupling: media lenta (~2s) de cada envolvente.
    // A 60fps con 196 cubos/frame → ~11760 updates/s. τ~2s → α = 1/(2*11760) ≈ 0.0000425
    static float kbEnvDryAvg = 0.0f, kbEnvWetLAvg = 0.0f, kbEnvWetRAvg = 0.0f;
    kbEnvDryAvg  = kbEnvDryAvg  * 0.999957f + envDry  * 0.000043f;
    kbEnvWetLAvg = kbEnvWetLAvg * 0.999957f + envWetL * 0.000043f;
    kbEnvWetRAvg = kbEnvWetRAvg * 0.999957f + envWetR * 0.000043f;
    float acDry  = std::fmax(0.0f, envDry  - kbEnvDryAvg  * 0.85f);
    float acWetL = std::fmax(0.0f, envWetL - kbEnvWetLAvg * 0.85f);
    float acWetR = std::fmax(0.0f, envWetR - kbEnvWetRAvg * 0.85f);

    float hDry = 0.0f, hWet = 0.0f;
    float wSig = 0.0f, wMag = 0.0f;   // fase de onda firmada (rings que salen del centro)
    for (const auto& p : pulses) {
        float dt = (float)(now - p.tEmit);
        if (dt < 0 || dt > 4) continue;
        float ex = 0, ez = 0;
        if (p.type == 1) { ex = 0; ez = 0; }
        else { ex = p.side ? 3.0f : -3.0f; ez = 0; }
        float dx = gx - ex, dz = gz - ez;
        float d = std::sqrt(dx*dx + dz*dz);
        float r = dt * SPEED;
        float dist = std::fabs(d - r);
        if (dist > 5) continue;
        float env2 = std::exp(-dist * DECAY_D) * std::pow(p.energy, 0.6f) * AMP_MUL * std::exp(-dt * DECAY_T);
        float sc = std::cos(dist * FREQ);
        wSig += sc * env2;                       // suma FIRMADA -> fase de onda (cresta/valle)
        wMag += env2;                            // energía local de onda (mag, para altura + color)
        float contrib = std::fabs(sc) * env2;    // rectificada -> solo para color/presencia
        if (p.type == 1) hDry += contrib; else hWet += contrib;
    }
    // Mapeo PERCEPTUAL (pow 0.55): el eco flojo (3er/4º hit) aún SE VE.
    // Domes usan valores AC-coupled -> no se quedan planos con sonido sostenido.
    constexpr float GATE = 0.0030f;
    auto perc = [](float env) { return std::pow(std::fmin(1.0f, env * 1.5f), 0.55f); };
    for (int side = 0; side < 2; ++side) {
        float env = side ? acWetR : acWetL;
        if (env < GATE) continue;
        float ex = side ? 3.0f : -3.0f;
        float dx = gx - ex, dz = gz;
        float d = std::sqrt(dx*dx + dz*dz);
        float falloff = std::exp(-d * 0.12f);
        hWet += perc(env) * INST_MUL * falloff;
    }
    if (acDry > GATE) {
        float d = std::sqrt(gx*gx + gz*gz);
        float falloff = std::exp(-d * 0.10f);
        hDry += perc(acDry) * INST_MUL * 0.9f * falloff;
    }
    // Color base = color actual suavizado (el TONO lo modula la fase de onda en el draw).
    DelayHeight r; r.hDry = hDry; r.hWet = hWet; r.mag = wMag; r.cr = curR; r.cg = curG; r.cb = curB;
    r.wave = (wMag > 0.01f) ? std::fmax(-1.0f, std::fmin(1.0f, wSig / wMag)) : 0.0f;
    return r;
}

// ── LAYOUT MODULAR (#6): módulos con borde 1px + cabecera [ … ] ─────────────
static constexpr float kobossDelayKnobR = 15.0f;
float kobossMHdr() const { return 13.0f; }   // alto de la cabecera del módulo
juce::Rectangle<float> kobossModPreset() const { float W=(float)getWidth(); return {8.0f,26.0f,W-16.0f,28.0f}; }
juce::Rectangle<float> kobossModOut()    const { float W=(float)getWidth(),H=(float)getHeight(); return {8.0f,H-58.0f,W-16.0f,50.0f}; }
// panel FX clásico: delay a media anchura cuando el panel FX está abierto, panel a un lado.
juce::Rectangle<float> kobossModDelay()  const { float W=(float)getWidth(),H=(float)getHeight(); float w = kobossDelayFxExpanded ? (W-20.0f)*0.5f : (W-16.0f); return {8.0f,H-200.0f,w,132.0f}; }
juce::Rectangle<float> kobossModFx()     const { auto d=kobossModDelay(); float W=(float)getWidth(); return {d.getRight()+4.0f,d.getY(),(W-20.0f)*0.5f,132.0f}; }
juce::Rectangle<float> kobossModVisual() const { float W=(float)getWidth(); auto p=kobossModPreset(); auto d=kobossModDelay(); return {8.0f,p.getBottom()+4.0f,W-16.0f,d.getY()-p.getBottom()-16.0f}; }
juce::Rectangle<float> kobossContent(juce::Rectangle<float> m) const { return m.withTrimmedTop(kobossMHdr()).reduced(5.0f,3.0f); }

juce::Rectangle<float> kobossDelayPresetPrevRect() const { auto c=kobossContent(kobossModPreset()); return {c.getX(),c.getCentreY()-9.0f,18.0f,18.0f}; }
juce::Rectangle<float> kobossDelayPresetNextRect() const { auto c=kobossContent(kobossModPreset()); return {c.getX()+22.0f,c.getCentreY()-9.0f,18.0f,18.0f}; }
juce::Rectangle<float> kobossDelaySaveRect()       const { auto c=kobossContent(kobossModPreset()); return {c.getRight()-40.0f,c.getCentreY()-8.0f,40.0f,16.0f}; }

// módulo DELAY: 2x2 -> slot0=toggle pp, slot1=time, slot2=fbk, slot3=mix
juce::Point<float> kobossDelayDelaySlot(int s) const {
    auto c=kobossContent(kobossModDelay());
    float W=(float)getWidth();
    float halfW = ((W-20.0f)*0.5f) - 10.0f;   // ancho de contenido en modo "half" -> los knobs NO se mueven al expandir/colapsar
    float cw=halfW/2.0f, ch=c.getHeight()/2.0f;
    return { c.getX()+(s%2)*cw+cw*0.5f, c.getY()+(s/2)*ch+ch*0.30f };
}
juce::Rectangle<float> kobossDelayPpRect() const { auto p=kobossDelayDelaySlot(0); return {p.x-24.0f,p.y-11.0f,48.0f,22.0f}; }
// 7 knobs: 0 time 1 fbk 2 mix (módulo delay) ; 5 out, 6 duck (módulo out)
juce::Point<float> kobossDelayKnobCenter(int i) const {
    if (i==0) return kobossDelayDelaySlot(1);
    if (i==1) return kobossDelayDelaySlot(2);
    if (i==2) return kobossDelayDelaySlot(3);
    if (i==5) { auto c=kobossContent(kobossModOut()); return {c.getX()+15.0f,c.getCentreY()}; }
    if (i==6) { auto c=kobossContent(kobossModOut()); return {c.getX()+105.0f,c.getCentreY()}; }
    return {0.0f,0.0f};
}
// toggle FREEZE + meter de salida, dentro del módulo OUT (a la derecha del knob duck)
juce::Rectangle<float> kobossDelayFreezeRect() const { auto c=kobossContent(kobossModOut()); return {c.getX()+176.0f,c.getCentreY()-10.0f,56.0f,20.0f}; }
juce::Rectangle<float> kobossDelayMeterRect()  const { auto c=kobossContent(kobossModOut()); return {c.getX()+242.0f,c.getCentreY()-8.0f,46.0f,16.0f}; }
int kobossDelayKnobAt(juce::Point<int> p) const {
    static const int ks[5]={0,1,2,5,6};
    for (int i : ks) {
        auto c=kobossDelayKnobCenter(i);
        float dx=(float)p.x-c.x, dy=(float)p.y-c.y;
        if (dx*dx+dy*dy <= (kobossDelayKnobR+8)*(kobossDelayKnobR+8)) return i;
    }
    return -1;
}
// módulo FX: pestañas (fila arriba) + pad/EQ + barra de MIX del efecto enfocado (abajo)
static constexpr float kobossDelayFxRowH = 16.0f;
juce::Rectangle<float> kobossDelayFxCell(int i) const {
    auto c=kobossContent(kobossModFx());
    float cellW=c.getWidth()/4.0f;
    return {c.getX()+(float)i*cellW, c.getY(), cellW-2.0f, kobossDelayFxRowH};
}
int kobossDelayFxAt(juce::Point<int> p) const { for(int i=0;i<4;++i) if(kobossDelayFxCell(i).contains(p.toFloat())) return i; return -1; }
// barra de MIX del efecto enfocado (franja inferior del panel)
juce::Rectangle<float> kobossDelayMixRect() const {
    auto c=kobossContent(kobossModFx());
    return { c.getX(), c.getBottom()-19.0f, c.getWidth(), 12.0f };
}
juce::Rectangle<float> kobossDelayXYRect() const {
    auto c=kobossContent(kobossModFx());
    float top=c.getY()+kobossDelayFxRowH+9.0f;   // más aire bajo las pestañas
    return {c.getX(), top, c.getWidth(), kobossDelayMixRect().getY()-top-9.0f};
}
// pestaña FX: COLAPSADO = CTA "+ fx" en la mitad derecha de la caja delay (ancho completo);
// EXPANDIDO = botón "x" para colapsar, esquina sup-dcha del módulo fx.
juce::Rectangle<float> kobossDelayFxTabRect() const {
    float W=(float)getWidth();
    if (!kobossDelayFxExpanded) {
        auto c=kobossContent(kobossModDelay());
        float l = 8.0f + (W-20.0f)*0.5f + 14.0f;
        float rgt = (W-8.0f) - 14.0f;
        float avail = rgt - l;
        float bw = juce::jmin(150.0f, avail);
        float bx = l + (avail - bw)*0.5f;
        return { bx, c.getCentreY()-13.0f, bw, 26.0f };
    } else {
        auto f=kobossModFx();
        return { f.getRight()-17.0f, f.getY()+1.5f, 12.0f, 10.0f };
    }
}
// aplica un preset: vuelca TODOS los params + fuerza el reenvío al patch
// ── PRESETS DE USUARIO (guardados en disco) ─────────────────────────────────
juce::File kobossUserDir() const {
    auto d = juce::File::getSpecialLocation(juce::File::userApplicationDataDirectory)
               .getChildFile("Koboss").getChildFile("Delay Presets");
    d.createDirectory();
    return d;
}
void kobossLoadUserPresets() {
    kobossUserPresets.clear(); kobossUserNames.clear();
    auto files = kobossUserDir().findChildFiles(juce::File::findFiles, false, "*.kbd");
    files.sort();
    for (auto& f : files) {
        juce::String content = f.loadFileAsString();
        // formato nuevo: 1ª línea "name <nombre a mostrar>", 2ª línea = params.
        // formato viejo (sin línea name): todo params -> nombre = nombre de archivo.
        juce::StringArray lines = juce::StringArray::fromLines(content);
        std::string dispName; juce::String paramLine;
        if (lines.size() >= 2 && lines[0].trim().startsWith("name ")) {
            dispName = lines[0].trim().substring(5).trim().toStdString();
            paramLine = lines[1];
        } else {
            dispName = f.getFileNameWithoutExtension().toStdString();
            paramLine = content;
        }
        auto toks = juce::StringArray::fromTokens(paramLine, " \n\r\t", "");
        toks.removeEmptyStrings();
        if (toks.size() < 15) continue;
        DelayPreset p; int t=0;
        p.sync=toks[t++].getIntValue(); p.timeN=toks[t++].getFloatValue();
        p.fb=toks[t++].getFloatValue(); p.mix=toks[t++].getFloatValue(); p.out=toks[t++].getFloatValue();
        p.pp=toks[t++].getIntValue(); p.fx=toks[t++].getIntValue();
        for(int i=0;i<4;i++) p.a[i]=toks[t++].getFloatValue();
        for(int i=0;i<4;i++) p.b[i]=toks[t++].getFloatValue();
        if (toks.size() >= 23) {   // formato nuevo: mix por efecto + orden de cadena
            for(int i=0;i<4;i++) p.mx[i]=toks[t++].getFloatValue();
            for(int i=0;i<4;i++) p.ord[i]=toks[t++].getIntValue();
        } else {   // formato viejo (selector de UN efecto): derivar la cadena
            for(int i=0;i<4;i++) p.mx[i] = (p.fx==i) ? 1.0f : 0.0f;
            p.ord[0]=0; p.ord[1]=1; p.ord[2]=2; p.ord[3]=3;
            if (p.fx<0||p.fx>3) p.fx=0;
        }
        if (dispName.empty()) dispName = "user";
        p.name=""; kobossUserPresets.push_back(p);
        kobossUserNames.push_back(dispName);
    }
    for (size_t i=0;i<kobossUserPresets.size();++i) kobossUserPresets[i].name = kobossUserNames[i].c_str();
    kobossUserLoaded = true;
}
int kobossTotalPresets() { int n; kobossDelayPresetTable(n); if(!kobossUserLoaded) kobossLoadUserPresets(); return n + (int)kobossUserPresets.size(); }
DelayPreset kobossPresetAt(int idx) {
    int nf; auto* F=kobossDelayPresetTable(nf);
    if (idx < nf) return F[idx];
    int u = idx - nf; if (u < 0 || u >= (int)kobossUserPresets.size()) return F[0];
    return kobossUserPresets[(size_t)u];
}
void kobossSaveUserPreset() {
    if (!kobossUserLoaded) kobossLoadUserPresets();
    auto pd = editor->pd;
    int g = (int)kobossUserPresets.size() + 1; juce::File file; juce::String name;
    do { name = "user " + juce::String(g).paddedLeft('0',2); file = kobossUserDir().getChildFile(name+".kbd"); ++g; }
    while (file.existsAsFile());
    juce::String body;
    body << "name " << name << "\n";   // nombre a mostrar DENTRO del archivo
    body << pd->kbSyncMode << " " << (pd->kbSyncMode ? pd->kbTime : pd->kbTimeMs) << " "
         << pd->kbFeedback << " " << pd->kbMix << " " << pd->kbOut << " "
         << pd->kbPingpong << " " << pd->kbFx;
    for(int i=0;i<4;i++) body << " " << pd->kbFxA[i];
    for(int i=0;i<4;i++) body << " " << pd->kbFxB[i];
    for(int i=0;i<4;i++) body << " " << pd->kbFxMix[i];
    for(int k=0;k<4;k++) body << " " << pd->kbOrder[k];
    file.replaceWithText(body);
    kobossLoadUserPresets();
    int nf; kobossDelayPresetTable(nf);
    pd->kobossActivePreset = nf + (int)kobossUserPresets.size() - 1;   // selecciona el recién guardado
    if (editor) editor->nvgSurface.invalidateAll();
}

void kobossSaveUserPresetNamed(const std::string& displayName) {
    if (!kobossUserLoaded) kobossLoadUserPresets();
    auto pd = editor->pd;
    // disp = nombre A MOSTRAR (tal cual, conserva / espacios, etc.) -> va DENTRO del archivo
    std::string disp;
    for (unsigned char c : displayName) {
        if (c >= 32 && c < 127) disp += (char)c;
        if (disp.size() >= 24) break;
    }
    if (disp.empty()) disp = "user";
    // safe = nombre de ARCHIVO (sin caracteres problemáticos para el sistema de archivos)
    std::string safe;
    for (unsigned char c : disp) {
        if (c != '/' && c != '\\' && c != ':' && c != '*'
                && c != '?' && c != '"' && c != '<' && c != '>' && c != '|')
            safe += (char)c;
    }
    if (safe.empty()) safe = "user";

    // Overwrite si el nombre A MOSTRAR coincide con el preset de usuario activo
    int nfact; kobossDelayPresetTable(nfact);
    int curIdx = pd->kobossActivePreset;
    if (curIdx >= nfact) {
        std::string curName(kobossPresetAt(curIdx).name);
        if (disp == curName) {
            // borrar el viejo primero, luego guardar nuevo con el mismo nombre
            auto files = kobossUserDir().findChildFiles(juce::File::findFiles, false, "*.kbd");
            files.sort();
            int u = curIdx - nfact;
            if (u < files.size()) files[u].deleteFile();
        }
    }

    // evitar colisión con archivo existente (si no es overwrite)
    juce::File file = kobossUserDir().getChildFile(juce::String(safe) + ".kbd");
    int g = 2;
    while (file.existsAsFile())
        file = kobossUserDir().getChildFile(juce::String(safe) + " " + juce::String(g++) + ".kbd");
    juce::String body;
    body << "name " << juce::String(disp) << "\n";   // nombre a mostrar DENTRO del archivo
    body << pd->kbSyncMode << " " << (pd->kbSyncMode ? pd->kbTime : pd->kbTimeMs) << " "
         << pd->kbFeedback << " " << pd->kbMix << " " << pd->kbOut << " "
         << pd->kbPingpong << " " << pd->kbFx;
    for(int i=0;i<4;i++) body << " " << pd->kbFxA[i];
    for(int i=0;i<4;i++) body << " " << pd->kbFxB[i];
    for(int i=0;i<4;i++) body << " " << pd->kbFxMix[i];
    for(int k=0;k<4;k++) body << " " << pd->kbOrder[k];
    file.replaceWithText(body);
    kobossLoadUserPresets();
    int nf; kobossDelayPresetTable(nf);
    pd->kobossActivePreset = nf + (int)kobossUserPresets.size() - 1;
    if (editor) editor->nvgSurface.invalidateAll();
}

void kobossDeleteUserPreset(int idx) {
    int nf; kobossDelayPresetTable(nf);
    if (idx < nf) return;   // no borrar presets de fábrica
    int u = idx - nf;
    if (u < 0 || u >= (int)kobossUserPresets.size()) return;
    // construir el nombre de archivo del preset
    auto files = kobossUserDir().findChildFiles(juce::File::findFiles, false, "*.kbd");
    files.sort();
    if (u < files.size()) files[u].deleteFile();
    kobossLoadUserPresets();
    auto pd = editor->pd;
    int total = kobossTotalPresets();
    if (pd->kobossActivePreset >= total) pd->kobossActivePreset = nf - 1;
    if (editor) editor->nvgSurface.invalidateAll();
}

juce::Rectangle<float> kobossDelayDeleteRect() const {
    auto c = kobossContent(kobossModPreset());
    return {c.getRight()-66.0f, c.getCentreY()-8.0f, 22.0f, 16.0f};   // a la izq de save (right-40), 4px de gap
}

// handler de teclado cuando el modo rename está activo; retorna true si consume la tecla
bool handleKobossDelayKey(juce::KeyPress const& key) {
    if (!kobossDelayRenaming) return false;
    if (key.getKeyCode() == juce::KeyPress::returnKey || key.getKeyCode() == 13) {
        std::string name = kobossDelayRenameText.empty() ? "user" : kobossDelayRenameText;
        kobossDelayRenaming = false;
        giveAwayKeyboardFocus(); setWantsKeyboardFocus(false);
        kobossSaveUserPresetNamed(name);
        return true;
    }
    if (key.getKeyCode() == juce::KeyPress::escapeKey) {
        kobossDelayRenaming = false;
        giveAwayKeyboardFocus(); setWantsKeyboardFocus(false);
        if (editor) editor->nvgSurface.invalidateAll();
        return true;
    }
    if (key.getKeyCode() == juce::KeyPress::backspaceKey) {
        if (!kobossDelayRenameText.empty()) { kobossDelayRenameText.pop_back(); if (editor) editor->nvgSurface.invalidateAll(); }
        return true;
    }
    juce::juce_wchar c = key.getTextCharacter();
    if (c >= 32 && c < 127 && kobossDelayRenameText.size() < 24) {
        kobossDelayRenameText += (char)c;
        if (editor) editor->nvgSurface.invalidateAll();
    }
    return true;
}

void kobossDelayApplyPreset(int idx) {
    int n = kobossTotalPresets();
    idx = (idx % n + n) % n;
    auto q = kobossPresetAt(idx);
    auto pd = editor->pd;
    pd->kobossActivePreset = idx;
    pd->kbSyncMode = q.sync;
    if (q.sync) pd->kbTime = q.timeN; else pd->kbTimeMs = q.timeN;
    pd->kbFeedback = q.fb; pd->kbMix = q.mix; pd->kbOut = q.out;
    pd->kbPingpong = q.pp; pd->kbFx = (q.fx < 0 ? 0 : (q.fx > 3 ? 3 : q.fx));
    for (int i = 0; i < 4; ++i) { pd->kbFxA[i] = q.a[i]; pd->kbFxB[i] = q.b[i]; pd->kbFxMix[i] = q.mx[i]; }
    for (int k = 0; k < 4; ++k) pd->kbOrder[k] = q.ord[k];
    // forzar que render() reenvíe todo al patch (caches a sentinel)
    kobossDelayLastFb = kobossDelayLastMix = kobossDelayLastOut = -999.0f;
    kobossDelayLastPp = -999;
    for (int i = 0; i < 4; ++i) { kbLastFa[i] = kbLastFb[i] = kbLastFm[i] = -999.0f; kbLastSlot[i] = -999; }
    kobossDelayLastSentMs = -1.0f;
    pd->sendFloat("tmute", 1.0f);   // dip breve del output -> mata el smear del varispeed
    if (editor) editor->nvgSurface.invalidateAll();
}

// ── knob propio del delay: TICK MINIMALISTA (aguja naranja + número) ─────────
// Pequeño, sin anillo ni arco (no toca renderKobossKnob, que usa el Chorus).
void renderKobossDelayKnob(NVGcontext* nvg, float cx, float cy, float value,
                           const char* label, const char* valueStr) {
    const float R = 14.0f;
    const float PI = 3.14159265f;
    float a = PI * 0.75f + value * (PI * 1.5f);   // barrido 270° (135° -> +270°)
    // guía tenue del recorrido (muy fina, soft) para dar referencia sin ruido visual
    nvgBeginPath(nvg);
    nvgArc(nvg, cx, cy, R, PI * 0.75f, PI * 0.75f + PI * 1.5f, NVG_HOLE);
    nvgStrokeColor(nvg, nvgRGB(0xc8, 0xc5, 0xbd));
    nvgStrokeWidth(nvg, 1.0f); nvgStroke(nvg);
    // aguja naranja desde cerca del centro hasta el radio
    float x0 = cx + std::cos(a) * 2.5f, y0 = cy + std::sin(a) * 2.5f;
    float x1 = cx + std::cos(a) * R,    y1 = cy + std::sin(a) * R;
    nvgBeginPath(nvg);
    nvgMoveTo(nvg, x0, y0); nvgLineTo(nvg, x1, y1);
    nvgStrokeColor(nvg, nvgRGB(0xd8, 0x55, 0x2e));
    nvgStrokeWidth(nvg, 2.0f); nvgLineCap(nvg, NVG_ROUND); nvgStroke(nvg);
    // punto central tinta
    nvgBeginPath(nvg); nvgCircle(nvg, cx, cy, 1.6f);
    nvgFillColor(nvg, nvgRGB(0x1a, 0x1a, 0x1a)); nvgFill(nvg);
    // valor grande debajo
    nvgFontFace(nvg, "Inter-Bold"); nvgFontSize(nvg, 11.0f);
    nvgFillColor(nvg, nvgRGB(0x1a, 0x1a, 0x1a));
    nvgTextAlign(nvg, NVG_ALIGN_CENTER | NVG_ALIGN_TOP);
    nvgText(nvg, cx, cy + R + 2.0f, valueStr, nullptr);
    // label pequeño debajo
    nvgFontFace(nvg, "Inter-SemiBold"); nvgFontSize(nvg, 8.0f);
    nvgFillColor(nvg, nvgRGB(0x8a, 0x81, 0x70));
    nvgText(nvg, cx, cy + R + 13.0f, label, nullptr);
}

// ── render principal ────────────────────────────────────────────────────────
void renderKobossDelay(NVGcontext* nvg) {
    const float W = (float)getWidth();
    const float H = (float)getHeight();
    const double now = juce::Time::getMillisecondCounterHiRes() / 1000.0;
    kobossDelayLastTime = now;

    kobossDelayCheckUpdate();   // 1 sola vez (guard interno): mira si hay versión nueva

    // one-shot al arrancar: empujar TODO el estado C++ al patch. El [loadbang]
    // del .pd no entrega fiable en el plugin -> el DSP arrancaba mudo y solo
    // sonaba al tocar un knob (que disparaba un sendFloat). Aqui forzamos que el
    // patch reciba feedback/width/mix/amount/fx/curve ya en el primer frame, y
    // marcamos lastSentMs=-1 para que el time tambien se reenvie abajo.
    // PUSH CONTINUO de parámetros desde render() — el handler de ratón no entrega
    // fiable sus sendFloat en el plugin (por eso los efectos "no funcionaban": fx/fxa/fxb
    // se mandaban desde el click/drag). El time ya iba así y SÍ funcionaba. Mandar cada
    // param cuando cambie respecto al último enviado. Bulletproof e idempotente.
    {
        auto ip = editor->pd;

        if (ip->kbForceResend) {
            // Restauracion de estado (p.ej. duplicar pista): los last* son globales de proceso y
            // pueden coincidir con el valor de otra instancia -> forzar re-envio poniendolos a sentinel.
            kobossDelayLastSentMs = -1.0f;
            kobossDelayLastFb = kobossDelayLastMix = kobossDelayLastOut = kobossDelayLastDuck = -999.0f;
            kobossDelayLastPp = kobossDelayLastFreeze = -999;
            for (int _i = 0; _i < 4; ++_i) { kbLastFa[_i] = kbLastFb[_i] = kbLastFm[_i] = -999.0f; kbLastSlot[_i] = -999; }
            ip->kbForceResend = false;
        }

        // feedback con CURVA media: 30% = pocos golpes, 50% = unos cuantos, 100% = cola
        // larga (sin swell infinito). pow(.,0.7)*90 -> patch /100, min 0.95.
        float fbSend = std::pow(ip->kbFeedback, 0.7f) * 90.0f;
        if (fbSend != kobossDelayLastFb)          { ip->sendFloat("feedback", fbSend);                  kobossDelayLastFb = fbSend; }
        if (ip->kbMix      != kobossDelayLastMix) { ip->sendFloat("mix",      ip->kbMix * 100.0f);      kobossDelayLastMix = ip->kbMix; }
        if (ip->kbPingpong != kobossDelayLastPp)  { ip->sendFloat("pingpong", (float)ip->kbPingpong);   kobossDelayLastPp = ip->kbPingpong; }
        // params por efecto: fb ANTES de fa (el cutoff del filtro tiene el inlet caliente en a;
        // si b llega después no recalcula). mix también por efecto. nombres fa{i}/fb{i}/fm{i}.
        static const char* kFaN[4]={"fa0","fa1","fa2","fa3"}, *kFbN[4]={"fb0","fb1","fb2","fb3"}, *kFmN[4]={"fm0","fm1","fm2","fm3"};
        static const char* kSlotN[4]={"slot0","slot1","slot2","slot3"};
        for (int i = 0; i < 4; ++i) {
            if (ip->kbFxMix[i] != kbLastFm[i]) { ip->sendFloat(kFmN[i], ip->kbFxMix[i]); kbLastFm[i] = ip->kbFxMix[i]; }
            if (ip->kbFxB[i]   != kbLastFb[i]) { ip->sendFloat(kFbN[i], ip->kbFxB[i]);   kbLastFb[i] = ip->kbFxB[i]; }
            if (ip->kbFxA[i]   != kbLastFa[i]) { ip->sendFloat(kFaN[i], ip->kbFxA[i]);   kbLastFa[i] = ip->kbFxA[i]; }
        }
        for (int k = 0; k < 4; ++k)
            if (ip->kbOrder[k] != kbLastSlot[k]) { ip->sendFloat(kSlotN[k], (float)ip->kbOrder[k]); kbLastSlot[k] = ip->kbOrder[k]; }
        // OUT: kbOut(0..1) -> dB(-24..+6) -> ganancia lineal
        float outLin = std::pow(10.0f, (-24.0f + ip->kbOut * 30.0f) / 20.0f);
        if (outLin != kobossDelayLastOut) { ip->sendFloat("out", outLin); kobossDelayLastOut = outLin; }
        if (ip->kbDuck != kobossDelayLastDuck)     { ip->sendFloat("duck",   ip->kbDuck);          kobossDelayLastDuck = ip->kbDuck; }
        if (ip->kbFreeze != kobossDelayLastFreeze) { ip->sendFloat("freeze", (float)ip->kbFreeze); kobossDelayLastFreeze = ip->kbFreeze; }
    }

    // fondo crema
    nvgBeginPath(nvg); nvgRect(nvg, 0, 0, W, H);
    nvgFillColor(nvg, nvgRGB(0xfa, 0xf6, 0xec)); nvgFill(nvg);

    // === audio tap (atomics escritos por el processor) ===
    float dryR = editor->pd->kobossDelayDryR.load();
    float wL   = editor->pd->kobossDelayWL.load();
    float wR   = editor->pd->kobossDelayWR.load();

    // attack rápido, RELEASE LENTO: mientras haya sonido el visual se mantiene vivo y
    // no se apaga de golpe entre ecos (lo más importante para Toni). El gate de
    // kobossDelayHeightAt lo lleva a 0 cuando hay silencio real.
    auto smooth = [](float& env, float target) {
        float c = (target > env) ? 0.5f : 0.018f;
        env += (target - env) * c;
    };
    smooth(kobossDelayEnvDry,  dryR);
    smooth(kobossDelayEnvWetL, wL);
    smooth(kobossDelayEnvWetR, wR);

    // === meter de salida + clip ===  (pico del último bloque; bar = RMS L/R suavizado)
    float outPk = editor->pd->kobossDelayOutPeak.load();
    float outRms = std::fmax(wL, wR);
    // attack rápido, release lento -> barra estable, no parpadea
    kobossDelayOutMeter += (outRms - kobossDelayOutMeter) * (outRms > kobossDelayOutMeter ? 0.6f : 0.08f);
    if (outPk >= 0.99f) kobossDelayClipHold = 1.0f;          // dispara el clip
    else kobossDelayClipHold *= 0.97f;                       // y decae poco a poco

    // === time efectivo (ms): sync = bpm host * division ; free = ms directo ===
    static const float kDivFactorQ[8] = { 4.0f, 2.0f, 1.5f, 1.0f, 0.75f, 0.5f, 0.333f, 0.25f };
    int _di = (int)std::round(editor->pd->kbTime * 7.0f);
    _di = _di < 0 ? 0 : (_di > 7 ? 7 : _di);
    float hostBpm = editor->pd->kbHostBpm.load();
    float bpmEff = hostBpm >= 20.0f ? hostBpm : 120.0f;
    bool syncMode = (editor->pd->kbSyncMode != 0);
    float effMs = syncMode ? (60000.0f / bpmEff) * kDivFactorQ[_di]
                           : editor->pd->kbTimeMs;
    if (effMs < 30.0f)   effMs = 30.0f;
    if (effMs > 2000.0f) effMs = 2000.0f;
    // reloj de animacion = periodo real del eco
    float Tparam = effMs / 1000.0f;
    if (Tparam < 0.03f) Tparam = 0.03f;
    if (kobossDelayNextEmit == 0) kobossDelayNextEmit = now + Tparam;
    int guard = 0;
    // === color del efecto activo (modulado por la CANTIDAD = fxA) ===
    // Cada pulso nuevo guarda ESTE color -> el cambio es gradual (ondas viejas con su
    // color, nuevas con el nuevo, emanando del centro). dome (sonido actual) = color
    // actual suavizado.
    int fxc = editor->pd->kbFx; fxc = fxc < 0 ? 0 : (fxc > 3 ? 3 : fxc);
    float viv = std::fmin(1.0f, 0.30f + 0.70f * editor->pd->kbFxA[fxc]);
    NVGcolor wetC = kobossMixHex(0xcabf9f, kobossDelayFxHex(fxc), viv);
    uint32_t wetCol = kobossColToHex(wetC);
    uint32_t dryCol = 0x9a8a60;   // dry = tan cálido neutro
    // suavizar el color del dome hacia el actual (no salta)
    kobossDelayCurCol[0] += (wetC.r - kobossDelayCurCol[0]) * 0.05f;
    kobossDelayCurCol[1] += (wetC.g - kobossDelayCurCol[1]) * 0.05f;
    kobossDelayCurCol[2] += (wetC.b - kobossDelayCurCol[2]) * 0.05f;
    // energía PERCEPTUAL del pulso: un eco flojo (4º hit) aún emite una onda visible.
    auto pEnergy = [](float e){ return std::pow(std::fmin(1.0f, e * 6.0f), 0.5f); };
    while (now >= kobossDelayNextEmit && guard++ < 64) {
        int side = kobossDelayRecirc % 2;
        float e = side ? wR : wL;
        if (e > 0.0012f)
            kobossDelayPulses.push_back({now, side, 0, pEnergy(e), wetCol});
        kobossDelayRecirc++;
        kobossDelayNextEmit += Tparam;
        if (kobossDelayRecirc > 40) kobossDelayRecirc = 40;
    }
    // peak detection wet (transients que el reloj perdería)
    constexpr float peakThresh = 0.04f;
    if (wL > peakThresh && wL > kobossDelayPrevWetL * 1.5f)
        kobossDelayPulses.push_back({now, 0, 0, pEnergy(wL), wetCol});
    if (wR > peakThresh && wR > kobossDelayPrevWetR * 1.5f)
        kobossDelayPulses.push_back({now, 1, 0, pEnergy(wR), wetCol});
    kobossDelayPrevWetL = wL; kobossDelayPrevWetR = wR;
    if (dryR > 0.02f && dryR > kobossDelayPrevDryPeak * 1.4f)
        kobossDelayPulses.push_back({now, 2, 1, pEnergy(dryR), dryCol});
    kobossDelayPrevDryPeak = dryR;
    kobossDelayPulses.erase(
        std::remove_if(kobossDelayPulses.begin(), kobossDelayPulses.end(),
                       [now](const DelayPulse& p){ return now - p.tEmit > 4; }),
        kobossDelayPulses.end());
    // Cap: no más de 64 pulsos vivos (quitar los más viejos del frente)
    while (kobossDelayPulses.size() > 64)
        kobossDelayPulses.erase(kobossDelayPulses.begin());

    // === enviar time(ms) a pd si cambio (sync recalcula con bpm; free es directo) ===
    if (std::fabs(effMs - kobossDelayLastSentMs) > 0.5f) {
        editor->pd->sendFloat("time", effMs);
        kobossDelayLastSentMs = effMs;
    }

    // === escena iso DENTRO del módulo [ visual ] (#6) ===
    auto _vm = kobossContent(kobossModVisual());
    const int   GRID = 14;
    const float zoneTop = _vm.getY(), zoneBot = _vm.getBottom();
    const float availW = _vm.getWidth();
    const float availH = zoneBot - zoneTop;
    const float AMP_TOTAL = 65.0f + 35.0f;
    const float BASELINE = 2.0f;
    const float M = (float)(GRID - 1);
    const float colBudget = availH * 0.40f;
    float TW = std::fmin(availW / (2.0f * M),
                         (availH - colBudget - 26.0f) / (float)GRID);   // margen abajo (punto intermedio)
    const float TH = TW * 0.5f;
    const float ocx = _vm.getCentreX();
    const float ocy = zoneTop + 8.0f + colBudget + BASELINE + (float)GRID * TH;

    // Grid estático precomputado: posiciones x/y y orden de cubos son constantes.
    struct KbCubePos { float gx, gz, cubeX, cubeY; int depth, j; };
    static std::vector<KbCubePos> kbGridPos;
    static bool kbGridReady = false;
    static float kbLastOcx = -1e9f, kbLastOcy = -1e9f, kbLastTW = -1e9f;
    if (!kbGridReady || std::fabs(ocx - kbLastOcx) > 0.5f || std::fabs(ocy - kbLastOcy) > 0.5f || std::fabs(TW - kbLastTW) > 0.01f) {
        kbGridPos.clear(); kbGridPos.reserve(GRID * GRID);
        for (int i = 0; i < GRID; ++i) for (int j = 0; j < GRID; ++j) {
            float gx = i - GRID/2 + 0.5f;
            float gz = j - GRID/2 + 0.5f;
            float cubeX = ocx + (gx - gz) * TW;
            float cubeY = ocy + (gx + gz) * TH;
            kbGridPos.push_back({gx, gz, cubeX, cubeY, i+j, j});
        }
        std::sort(kbGridPos.begin(), kbGridPos.end(), [](const KbCubePos& a, const KbCubePos& b){
            if (a.depth != b.depth) return a.depth < b.depth;
            return a.j < b.j;
        });
        kbGridReady = true; kbLastOcx = ocx; kbLastOcy = ocy; kbLastTW = TW;
    }

    struct Cube { float x, y, hDry, hWet, mag; int depth, j; float cr, cg, cb, wave; float kgx, kgz; };
    std::vector<Cube> cubes; cubes.reserve(GRID * GRID);

    // Early-out en silencio: si todas las envolventes < 0.003 y sin pulsos,
    // dibujar cubos base sin calcular heightAt (196 llamadas ahorradas).
    bool kbSilent = (kobossDelayEnvDry < 0.003f && kobossDelayEnvWetL < 0.003f &&
                     kobossDelayEnvWetR < 0.003f && kobossDelayPulses.empty());
    for (const auto& gp : kbGridPos) {
        float hDry = 0.0f, hWet = 0.0f, mag = 0.0f, cr = kobossDelayCurCol[0], cg = kobossDelayCurCol[1], cb = kobossDelayCurCol[2], wave = 0.0f;
        if (!kbSilent) {
            auto h = kobossDelayHeightAt(gp.gx, gp.gz, now,
                                          kobossDelayEnvDry, kobossDelayEnvWetL, kobossDelayEnvWetR,
                                          kobossDelayPulses, cr, cg, cb);
            hDry = h.hDry; hWet = h.hWet; mag = h.mag; cr = h.cr; cg = h.cg; cb = h.cb; wave = h.wave;
        }
        cubes.push_back({gp.cubeX, gp.cubeY, hDry, hWet, mag, gp.depth, gp.j, cr, cg, cb, wave, gp.gx, gp.gz});
    }

    nvgSave(nvg); nvgIntersectScissor(nvg, _vm.getX(), _vm.getY(), _vm.getWidth(), _vm.getHeight());
    for (const auto& c : cubes) {
        float liveH = std::fmax(0.0f, c.hDry + c.hWet);
        float intensity = std::fmin(1.0f, liveH / AMP_TOTAL);
        // WAVE-SHAPE (lab v3): la altura ONDULA con la fase. act = energía local comprimida;
        // la fase (c.wave) decide cresta (alto) o valle (bajo). Con señal fuerte hay crestas
        // Y valles -> relieve de lago, NUNCA bloque. Amplitud escala con la energía -> el
        // decay baja suave (sin la "sobredosis" de antes).
        float act = c.mag / (c.mag + 40.0f);            // 0..1
        float waveShape = 0.5f + 0.5f * c.wave;         // 0..1
        float kbLvlFrac = act * 0.18f + act * 0.82f * waveShape;
        float totalH = BASELINE + colBudget * kbLvlFrac;
        // ── PROPUESTA #10 "NEÓN SOBRE CREMA" (elegida por Toni en el color-lab) ──
        // Color del efecto MUY saturado y luminoso; el hue ondula con la fase real de la
        // onda (c.wave) -> anillos de neón saliendo del centro. En reposo = crema claro,
        // transición suave por presencia (sin negros ni saltos). cl + c.cr/cg/cb en 0..255.
        auto cl = [](float x){ return (unsigned char)(x < 0.0f ? 0.0f : (x > 255.0f ? 255.0f : x)); };
        float hue = kobossHue(c.cr, c.cg, c.cb) + c.wave * 34.0f;       // + swing de hue (más contraste)
        float ll  = 0.52f + std::fmin(1.0f, intensity) * 0.10f + c.wave * 0.12f;  // brillo ondula con la onda
        float nr, ng, nb; kobossHSL(hue, 0.95f, ll, nr, ng, nb);        // neón (sat 0.95)
        float pres = std::fmin(1.0f, (liveH / (liveH + 22.0f)) * 1.25f);// 0(silencio)..1, suave
        float tr = 232.0f + (nr - 232.0f) * pres;                       // mezcla desde crema
        float tg = 228.0f + (ng - 228.0f) * pres;
        float tb = 214.0f + (nb - 214.0f) * pres;
        NVGcolor base   = nvgRGB(cl(tr), cl(tg), cl(tb));                          // tapa
        NVGcolor colorL = nvgRGB(cl(tr * 0.85f), cl(tg * 0.85f), cl(tb * 0.85f));  // cara L
        NVGcolor colorR = nvgRGB(cl(tr * 0.72f), cl(tg * 0.72f), cl(tb * 0.72f));  // cara R
        renderKobossDelayColumn(nvg, c.x, c.y, TW, TH, totalH, base, colorL, colorR);
    }
    nvgRestore(nvg);

    auto pd = editor->pd;
    // ── helper para dibujar un módulo (borde 1px + cabecera [ … ]) ──
    auto drawMod = [&](juce::Rectangle<float> m, const char* label){
        nvgBeginPath(nvg); nvgRect(nvg, m.getX(), m.getY(), m.getWidth(), m.getHeight());
        nvgStrokeColor(nvg, nvgRGB(0x1a,0x1a,0x1a)); nvgStrokeWidth(nvg, 1.0f); nvgStroke(nvg);
        nvgBeginPath(nvg); nvgMoveTo(nvg, m.getX(), m.getY()+kobossMHdr()); nvgLineTo(nvg, m.getRight(), m.getY()+kobossMHdr());
        nvgStrokeColor(nvg, nvgRGB(0xc8,0xc5,0xbd)); nvgStrokeWidth(nvg, 1.0f); nvgStroke(nvg);
        nvgFontFace(nvg, "Inter-SemiBold"); nvgFontSize(nvg, 8.0f);
        nvgFillColor(nvg, nvgRGB(0x8a,0x81,0x70));
        nvgTextAlign(nvg, NVG_ALIGN_LEFT | NVG_ALIGN_MIDDLE);
        nvgText(nvg, m.getX()+6.0f, m.getY()+kobossMHdr()*0.5f+1.0f, label, nullptr);
    };
    // === cabecera superior ===
    nvgFontFace(nvg, "Inter-Bold"); nvgFontSize(nvg, 13.0f);
    nvgTextAlign(nvg, NVG_ALIGN_LEFT | NVG_ALIGN_TOP);
    nvgFillColor(nvg, nvgRGB(0x1a,0x1a,0x1a));
    nvgText(nvg, 9, 9, "koboss", nullptr);
    float bnd[4]; nvgTextBounds(nvg, 9, 9, "koboss", nullptr, bnd);
    nvgFillColor(nvg, nvgRGB(0x8a,0x81,0x70));
    nvgText(nvg, bnd[2]+6, 10, "· delay", nullptr);
    {   char info[48];
        if (syncMode && hostBpm >= 20.0f) std::snprintf(info, 48, "$ %.0fbpm %dms", hostBpm, (int)std::round(effMs));
        else                              std::snprintf(info, 48, "$ free %dms", (int)std::round(effMs));
        nvgFontFace(nvg, "Inter-Regular"); nvgFontSize(nvg, 9.0f);
        nvgFillColor(nvg, nvgRGB(0x8a,0x81,0x70));
        nvgTextAlign(nvg, NVG_ALIGN_RIGHT | NVG_ALIGN_TOP);
        nvgText(nvg, W-9, 11, info, nullptr);
    }
    // === módulos ===
    drawMod(kobossModPreset(), "[ preset ]");
    drawMod(kobossModVisual(), "[ visual ]");
    drawMod(kobossModDelay(),  "[ delay ]");
    if (kobossDelayFxExpanded) drawMod(kobossModFx(), "[ fx ]");
    drawMod(kobossModOut(),    "[ out ]");

    // === barra de PRESET (< nombre > [save]) — fábrica + usuario ===
    {
      if (kobossDelayRenaming) {
        // ── modo rename: input de texto ──────────────────────────────────────
        auto pm = kobossModPreset();
        auto pc = kobossContent(pm);
        // borde naranja = campo activo
        nvgBeginPath(nvg); nvgRect(nvg, pm.getX(), pm.getY(), pm.getWidth(), pm.getHeight());
        nvgStrokeColor(nvg, nvgRGB(0xd8,0x55,0x2e)); nvgStrokeWidth(nvg,1.5f); nvgStroke(nvg);
        // label "name"
        nvgFontFace(nvg, "Inter-SemiBold"); nvgFontSize(nvg, 8.0f);
        nvgFillColor(nvg, nvgRGB(0x8a,0x81,0x70));
        nvgTextAlign(nvg, NVG_ALIGN_LEFT | NVG_ALIGN_MIDDLE);
        nvgText(nvg, pc.getX()+2.0f, pc.getCentreY(), "name", nullptr);
        nvgBeginPath(nvg); nvgMoveTo(nvg, pc.getX()+33.0f, pc.getY()); nvgLineTo(nvg, pc.getX()+33.0f, pc.getBottom());
        nvgStrokeColor(nvg, nvgRGB(0xc8,0xc5,0xbd)); nvgStrokeWidth(nvg,1.0f); nvgStroke(nvg);
        // texto + cursor parpadeante (alterna cada ~0.5s)
        bool cursorOn = ((int)(now * 2.0) % 2) == 0;
        char tbuf[32]; std::snprintf(tbuf, 32, "%s%s", kobossDelayRenameText.c_str(), cursorOn ? "|" : " ");
        nvgFontFace(nvg, "Inter-SemiBold"); nvgFontSize(nvg, 10.5f);
        nvgFillColor(nvg, nvgRGB(0x1a,0x1a,0x1a));
        nvgTextAlign(nvg, NVG_ALIGN_LEFT | NVG_ALIGN_MIDDLE);
        nvgText(nvg, pc.getX()+38.0f, pc.getCentreY(), tbuf, nullptr);
        // botones [ok] y [×]
        auto okR = juce::Rectangle<float>(pc.getRight()-50.0f, pc.getCentreY()-8.0f, 22.0f, 16.0f);
        auto cxR = juce::Rectangle<float>(pc.getRight()-25.0f, pc.getCentreY()-8.0f, 21.0f, 16.0f);
        nvgBeginPath(nvg); nvgRect(nvg, okR.getX(), okR.getY(), okR.getWidth(), okR.getHeight());
        nvgFillColor(nvg, nvgRGB(0xd8,0x55,0x2e)); nvgFill(nvg);
        nvgFontFace(nvg, "Inter-Bold"); nvgFontSize(nvg, 9.0f); nvgFillColor(nvg, nvgRGB(0xfa,0xf6,0xec));
        nvgTextAlign(nvg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);
        nvgText(nvg, okR.getCentreX(), okR.getCentreY(), "ok", nullptr);
        nvgBeginPath(nvg); nvgRect(nvg, cxR.getX(), cxR.getY(), cxR.getWidth(), cxR.getHeight());
        nvgStrokeColor(nvg, nvgRGB(0xc8,0xc5,0xbd)); nvgStrokeWidth(nvg,1.0f); nvgStroke(nvg);
        nvgFontFace(nvg, "Inter-SemiBold"); nvgFontSize(nvg, 9.0f); nvgFillColor(nvg, nvgRGB(0x8a,0x81,0x70));
        nvgText(nvg, cxR.getCentreX(), cxR.getCentreY(), "x", nullptr);
      } else {
        int np = kobossTotalPresets();
        int pi = editor->pd->kobossActivePreset; pi = pi < 0 ? 0 : (pi >= np ? np-1 : pi);
        auto pr = kobossDelayPresetPrevRect(), nx = kobossDelayPresetNextRect();
        auto sv = kobossDelaySaveRect();
        auto pc = kobossContent(kobossModPreset());
        nvgFontFace(nvg, "Inter-Bold"); nvgFontSize(nvg, 13.0f);
        nvgFillColor(nvg, nvgRGB(0x1a,0x1a,0x1a));
        nvgTextAlign(nvg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);
        nvgText(nvg, pr.getCentreX(), pr.getCentreY(), "<", nullptr);
        nvgText(nvg, nx.getCentreX(), nx.getCentreY(), ">", nullptr);
        char pbuf[48]; std::snprintf(pbuf, 48, "%02d  %s", pi+1, kobossPresetAt(pi).name);
        nvgFontFace(nvg, "Inter-SemiBold"); nvgFontSize(nvg, 10.5f);
        nvgFillColor(nvg, nvgRGB(0xd8,0x55,0x2e));
        nvgTextAlign(nvg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);
        nvgText(nvg, (nx.getRight()+(pc.getRight()-70.0f))*0.5f, pc.getCentreY(), pbuf, nullptr);   // centrado en el hueco entre flechas y del/save
        // botón [del] — solo visible cuando el preset activo es de usuario
        {
            int nfact; kobossDelayPresetTable(nfact);
            if (pi >= nfact) {
                auto dl = kobossDelayDeleteRect();
                nvgBeginPath(nvg); nvgRect(nvg, dl.getX(), dl.getY(), dl.getWidth(), dl.getHeight());
                nvgFillColor(nvg, nvgRGB(0xd8,0x55,0x2e)); nvgFill(nvg);
                nvgFontFace(nvg, "Inter-SemiBold"); nvgFontSize(nvg, 8.5f);
                nvgFillColor(nvg, nvgRGB(0xfa,0xf6,0xec));
                nvgTextAlign(nvg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);
                nvgText(nvg, dl.getCentreX(), dl.getCentreY(), "del", nullptr);
            }
        }
        // botón save (guarda el estado actual como preset de usuario)
        nvgBeginPath(nvg); nvgRect(nvg, sv.getX(), sv.getY(), sv.getWidth(), sv.getHeight());
        nvgStrokeColor(nvg, nvgRGB(0xc8,0xc5,0xbd)); nvgStrokeWidth(nvg,1.0f); nvgStroke(nvg);
        nvgFontFace(nvg, "Inter-SemiBold"); nvgFontSize(nvg, 8.5f);
        nvgFillColor(nvg, nvgRGB(0x8a,0x81,0x70));
        nvgTextAlign(nvg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);
        nvgText(nvg, sv.getCentreX(), sv.getCentreY(), "+ save", nullptr);
      } // end else (modo normal)
    }
    // === módulo OUT: knob COMPACTO (valor al lado, no debajo) + footer ===
    {
        auto oc = kobossContent(kobossModOut());
        float cx=oc.getX()+15.0f, cy=oc.getCentreY(), R=13.0f;
        const float PI=3.14159265f; float a=PI*0.75f + pd->kbOut*PI*1.5f;
        nvgBeginPath(nvg); nvgArc(nvg,cx,cy,R,PI*0.75f,PI*0.75f+PI*1.5f,NVG_HOLE);
        nvgStrokeColor(nvg,nvgRGB(0xc8,0xc5,0xbd)); nvgStrokeWidth(nvg,1.0f); nvgStroke(nvg);
        nvgBeginPath(nvg); nvgMoveTo(nvg,cx+std::cos(a)*2.5f,cy+std::sin(a)*2.5f); nvgLineTo(nvg,cx+std::cos(a)*R,cy+std::sin(a)*R);
        nvgStrokeColor(nvg,nvgRGB(0xd8,0x55,0x2e)); nvgStrokeWidth(nvg,2.0f); nvgLineCap(nvg,NVG_ROUND); nvgStroke(nvg);
        nvgBeginPath(nvg); nvgCircle(nvg,cx,cy,1.5f); nvgFillColor(nvg,nvgRGB(0x1a,0x1a,0x1a)); nvgFill(nvg);
        char bo[16]; float outDb=-24.0f+pd->kbOut*30.0f; std::snprintf(bo,16,"%+ddB",(int)std::round(outDb));
        nvgFontFace(nvg,"Inter-SemiBold"); nvgFontSize(nvg,8.0f); nvgFillColor(nvg,nvgRGB(0x8a,0x81,0x70));
        nvgTextAlign(nvg,NVG_ALIGN_LEFT|NVG_ALIGN_BOTTOM); nvgText(nvg,cx+R+6.0f,cy,"out",nullptr);
        nvgFontFace(nvg,"Inter-Bold"); nvgFontSize(nvg,10.0f); nvgFillColor(nvg,nvgRGB(0x1a,0x1a,0x1a));
        nvgTextAlign(nvg,NVG_ALIGN_LEFT|NVG_ALIGN_TOP); nvgText(nvg,cx+R+6.0f,cy+1.0f,bo,nullptr);

        // --- knob DUCK (compacto, valor al lado) ---
        { float dcx=oc.getX()+105.0f, dcy=cy; float da=PI*0.75f + pd->kbDuck*PI*1.5f;
          nvgBeginPath(nvg); nvgArc(nvg,dcx,dcy,R,PI*0.75f,PI*0.75f+PI*1.5f,NVG_HOLE);
          nvgStrokeColor(nvg,nvgRGB(0xc8,0xc5,0xbd)); nvgStrokeWidth(nvg,1.0f); nvgStroke(nvg);
          nvgBeginPath(nvg); nvgMoveTo(nvg,dcx+std::cos(da)*2.5f,dcy+std::sin(da)*2.5f); nvgLineTo(nvg,dcx+std::cos(da)*R,dcy+std::sin(da)*R);
          nvgStrokeColor(nvg,nvgRGB(0xd8,0x55,0x2e)); nvgStrokeWidth(nvg,2.0f); nvgLineCap(nvg,NVG_ROUND); nvgStroke(nvg);
          nvgBeginPath(nvg); nvgCircle(nvg,dcx,dcy,1.5f); nvgFillColor(nvg,nvgRGB(0x1a,0x1a,0x1a)); nvgFill(nvg);
          char bd[16]; std::snprintf(bd,16,"%d%%",(int)std::round(pd->kbDuck*100));
          nvgFontFace(nvg,"Inter-SemiBold"); nvgFontSize(nvg,8.0f); nvgFillColor(nvg,nvgRGB(0x8a,0x81,0x70));
          nvgTextAlign(nvg,NVG_ALIGN_LEFT|NVG_ALIGN_BOTTOM); nvgText(nvg,dcx+R+6.0f,dcy,"duck",nullptr);
          nvgFontFace(nvg,"Inter-Bold"); nvgFontSize(nvg,10.0f); nvgFillColor(nvg,nvgRGB(0x1a,0x1a,0x1a));
          nvgTextAlign(nvg,NVG_ALIGN_LEFT|NVG_ALIGN_TOP); nvgText(nvg,dcx+R+6.0f,dcy+1.0f,bd,nullptr); }

        // --- toggle FREEZE ---
        { auto fr=kobossDelayFreezeRect(); bool on=(pd->kbFreeze!=0);
          nvgBeginPath(nvg); nvgRect(nvg,fr.getX(),fr.getY(),fr.getWidth(),fr.getHeight());
          if (on){ nvgFillColor(nvg,nvgRGB(0xd8,0x55,0x2e)); nvgFill(nvg); }
          else   { nvgStrokeColor(nvg,nvgRGB(0xc8,0xc5,0xbd)); nvgStrokeWidth(nvg,1.0f); nvgStroke(nvg); }
          nvgFontFace(nvg,"Inter-SemiBold"); nvgFontSize(nvg,9.0f);
          nvgTextAlign(nvg,NVG_ALIGN_CENTER|NVG_ALIGN_MIDDLE);
          nvgFillColor(nvg, on?nvgRGB(0xfa,0xf6,0xec):nvgRGB(0x8a,0x81,0x70));
          nvgText(nvg,fr.getCentreX(),fr.getCentreY(),"freeze",nullptr); }

        // --- meter de salida (L/R) + clip ---
        { auto mr=kobossDelayMeterRect();
          float lvl=std::fmin(1.0f, kobossDelayOutMeter*1.4f);
          // raíl
          nvgBeginPath(nvg); nvgRect(nvg,mr.getX(),mr.getY(),mr.getWidth(),mr.getHeight());
          nvgFillColor(nvg,nvgRGB(0xe6,0xe3,0xd9)); nvgFill(nvg);
          // barra (tinta; naranja en la zona alta)
          float bw=mr.getWidth()*lvl;
          nvgBeginPath(nvg); nvgRect(nvg,mr.getX(),mr.getY(),bw,mr.getHeight());
          nvgFillColor(nvg, lvl>0.85f?nvgRGB(0xd8,0x55,0x2e):nvgRGB(0x1a,0x1a,0x1a)); nvgFill(nvg);
          // marca de clip a la derecha
          float cl=kobossDelayClipHold;
          nvgBeginPath(nvg); nvgCircle(nvg,mr.getRight()+6.0f,mr.getCentreY(),3.0f);
          nvgFillColor(nvg, cl>0.05f?nvgRGB(0xd8,0x55,0x2e):nvgRGB(0xc8,0xc5,0xbd)); nvgFill(nvg);
          nvgFontFace(nvg,"Inter-SemiBold"); nvgFontSize(nvg,7.0f); nvgFillColor(nvg,nvgRGB(0x8a,0x81,0x70));
          nvgTextAlign(nvg,NVG_ALIGN_LEFT|NVG_ALIGN_BOTTOM); nvgText(nvg,mr.getX(),mr.getY()-1.0f,"out",nullptr); }

        nvgFontFace(nvg,"Inter-Regular"); nvgFontSize(nvg,8.0f); nvgFillColor(nvg,nvgRGB(0x8a,0x81,0x70));
        nvgTextAlign(nvg,NVG_ALIGN_RIGHT|NVG_ALIGN_MIDDLE); nvgText(nvg,oc.getRight(),oc.getCentreY()-5.0f,"kobossbeats.com",nullptr);
        if (kobossDelayUpd().load()) {   // hay versión nueva -> aviso clickable (abre la web)
            char up[28]; std::snprintf(up,28,"%s disponible \xe2\x86\x92",kobossDelayUpdVer());
            nvgFontFace(nvg,"Inter-Bold"); nvgFillColor(nvg,nvgRGB(0xd8,0x55,0x2e));
            nvgText(nvg,oc.getRight(),oc.getCentreY()+6.0f,up,nullptr);
        } else {
            nvgFillColor(nvg,nvgRGB(0xd8,0x55,0x2e)); nvgText(nvg,oc.getRight(),oc.getCentreY()+6.0f,KOBOSS_DELAY_VERSION,nullptr);
        }
    }

    // === SECCIÓN DELAY: toggle ping-pong + TIME/FBK/MIX ===
    static const char* kDivNames[8] = { "1/1","1/2","1/4.","1/4","1/8.","1/8","1/8t","1/16" };
    // toggle ping-pong
    {
        auto r = kobossDelayPpRect();
        bool on = (pd->kbPingpong != 0);
        nvgBeginPath(nvg); nvgRect(nvg, r.getX(), r.getY(), r.getWidth(), r.getHeight());
        if (on) { nvgFillColor(nvg, nvgRGB(0xd8,0x55,0x2e)); nvgFill(nvg); }
        else    { nvgStrokeColor(nvg, nvgRGB(0xc8,0xc5,0xbd)); nvgStrokeWidth(nvg,1.0f); nvgStroke(nvg); }
        nvgFontFace(nvg, "Inter-SemiBold"); nvgFontSize(nvg, 8.5f);
        nvgTextAlign(nvg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);
        nvgFillColor(nvg, on ? nvgRGB(0xfa,0xf6,0xec) : nvgRGB(0x8a,0x81,0x70));
        nvgText(nvg, r.getCentreX(), r.getCentreY(), "ping-pong", nullptr);
        nvgFontSize(nvg, 7.5f); nvgFillColor(nvg, nvgRGB(0x8a,0x81,0x70));
        nvgTextAlign(nvg, NVG_ALIGN_CENTER | NVG_ALIGN_TOP);
        nvgText(nvg, r.getCentreX(), r.getBottom() + 3, on ? "on" : "off", nullptr);
    }
    // TIME / FBK / MIX (OUT va en su propio módulo)
    char b0[16], b1[16], b2[16];
    float kval0; const char* klab0;
    if (syncMode) { std::snprintf(b0,16,"%s",kDivNames[_di]); kval0=_di/7.0f; klab0="sync"; }
    else { std::snprintf(b0,16,"%dms",(int)std::round(effMs)); kval0=(effMs-30.0f)/1970.0f; klab0="time"; }
    std::snprintf(b1, 16, "%d%%", (int)std::round(pd->kbFeedback * 100));
    std::snprintf(b2, 16, "%d%%", (int)std::round(pd->kbMix * 100));
    { auto c=kobossDelayKnobCenter(0); renderKobossDelayKnob(nvg,c.x,c.y,kval0,klab0,b0); }
    { auto c=kobossDelayKnobCenter(1); renderKobossDelayKnob(nvg,c.x,c.y,pd->kbFeedback,"fbk",b1); }
    { auto c=kobossDelayKnobCenter(2); renderKobossDelayKnob(nvg,c.x,c.y,pd->kbMix,"mix",b2); }

    // === SECCIÓN FX (panel clásico que abre/cierra; los 4 efectos suenan a la vez) ===
    static const char* fxNames[4] = { "filter","drive","crush","chorus" };
    static const char* fxParamLab[4][2] = {
        {"freq","width"}, {"drive","tono"}, {"bits","rate"}, {"depth","rate"}
    };
    if (kobossDelayFxExpanded) {
    NVGcolor TEAL = kobossFxAccent();
    int fxi = pd->kbFx; if (fxi < 0) fxi = 0; if (fxi > 3) fxi = 3;
    // pestañas: enfocada = relleno teal ; activa (mix>0) = texto teal + dot ; inactiva = muted
    for (int i = 0; i < 4; ++i) {
        auto r = kobossDelayFxCell(i);
        bool foc = (pd->kbFx == i);
        bool on  = (pd->kbFxMix[i] > 0.0001f);
        nvgBeginPath(nvg); nvgRect(nvg, r.getX(), r.getY(), r.getWidth(), r.getHeight());
        if (foc) { nvgFillColor(nvg, TEAL); nvgFill(nvg); }
        else     { nvgStrokeColor(nvg, on ? TEAL : nvgRGB(0xc8,0xc5,0xbd)); nvgStrokeWidth(nvg, 1.0f); nvgStroke(nvg); }
        nvgFontFace(nvg, "Inter-SemiBold"); nvgFontSize(nvg, 9.0f);
        nvgTextAlign(nvg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);
        nvgFillColor(nvg, foc ? nvgRGB(0xfa,0xf6,0xec) : (on ? TEAL : nvgRGB(0x8a,0x81,0x70)));
        nvgText(nvg, r.getCentreX(), r.getCentreY(), fxNames[i], nullptr);
        if (!foc && on) { nvgBeginPath(nvg); nvgCircle(nvg, r.getRight()-6.0f, r.getY()+5.0f, 2.2f); nvgFillColor(nvg, TEAL); nvgFill(nvg); }
    }
    // === XY PAD del efecto enfocado: X=param A, Y=param B ===
    {
        auto r = kobossDelayXYRect();
        float ax = pd->kbFxA[fxi], by = pd->kbFxB[fxi];
        bool fxOn = (pd->kbFxMix[fxi] > 0.0001f);
        nvgBeginPath(nvg); nvgRect(nvg, r.getX(), r.getY(), r.getWidth(), r.getHeight());
        nvgFillColor(nvg, nvgRGB(0xfb,0xf8,0xf0)); nvgFill(nvg);
        nvgStrokeColor(nvg, nvgRGB(0xc8,0xc5,0xbd)); nvgStrokeWidth(nvg, 0.75f); nvgStroke(nvg);   // marco fino
        nvgSave(nvg); nvgIntersectScissor(nvg, r.getX(), r.getY(), r.getWidth(), r.getHeight());
        nvgGlobalAlpha(nvg, fxOn ? 1.0f : 0.32f);   // apagado = atenuado (se ve que está off)
        float X=r.getX(), Y=r.getY(), Wp=r.getWidth(), Hp=r.getHeight();
        float nodeX = X + ax*Wp, nodeY = r.getBottom() - by*Hp;
        if (fxi == 0) {
            // FILTER band-pass tipo Ableton: curva real (filterMag), handle X=freq Y=width
            nvgStrokeColor(nvg, nvgRGB(0xe6,0xe3,0xd9)); nvgStrokeWidth(nvg, 0.75f);
            float gp[3] = {0.233f, 0.566f, 0.899f};
            for (int g=0; g<3; ++g){ float gx=X+gp[g]*Wp; nvgBeginPath(nvg); nvgMoveTo(nvg,gx,Y); nvgLineTo(nvg,gx,r.getBottom()); nvgStroke(nvg); }
            auto cyf = [&](float gmag){ return r.getBottom() - 6.0f - (Hp-14.0f)*gmag; };
            nvgBeginPath(nvg);
            for (int i=0;i<=(int)Wp;i++){ float fr=i/Wp; float gy=cyf(kobossFilterMag(fr,ax,by)); i?nvgLineTo(nvg,X+i,gy):nvgMoveTo(nvg,X+i,gy); }
            nvgLineTo(nvg, r.getRight(), r.getBottom()); nvgLineTo(nvg, X, r.getBottom());
            nvgFillColor(nvg, nvgRGBAf(0.122f,0.62f,0.576f,0.10f)); nvgFill(nvg);
            nvgBeginPath(nvg);
            for (int i=0;i<=(int)Wp;i++){ float fr=i/Wp; float gy=cyf(kobossFilterMag(fr,ax,by)); i?nvgLineTo(nvg,X+i,gy):nvgMoveTo(nvg,X+i,gy); }
            nvgStrokeColor(nvg, TEAL); nvgStrokeWidth(nvg, 1.8f); nvgStroke(nvg);
            // handle libre X=freq Y=width
            nodeX = X + (ax<0?0:(ax>1?1:ax))*Wp; nodeY = r.getBottom() - (by<0?0:(by>1?1:by))*Hp;
        } else if (fxi == 1) {
            float gain = 1.0f + pd->kbFxA[1] * 9.0f;
            nvgStrokeColor(nvg, nvgRGB(0xe6,0xe3,0xd9)); nvgStrokeWidth(nvg, 0.75f);
            nvgBeginPath(nvg); nvgMoveTo(nvg, X, r.getBottom()); nvgLineTo(nvg, r.getRight(), Y); nvgStroke(nvg);
            nvgStrokeColor(nvg, TEAL); nvgStrokeWidth(nvg, 1.8f); nvgBeginPath(nvg);
            for (int k=0;k<=28;k++){ float t=k/28.0f; float in=(t-0.5f)*2.0f; float out=std::tanh(in*gain)/std::tanh(gain); float xx=X+t*Wp; float yy=r.getBottom()-(out*0.5f+0.5f)*Hp; k==0?nvgMoveTo(nvg,xx,yy):nvgLineTo(nvg,xx,yy); }
            nvgStroke(nvg);
        } else if (fxi == 2) {
            int steps = std::max(2, (int)(16.0f - pd->kbFxA[2] * 14.0f));
            nvgStrokeColor(nvg, nvgRGB(0xc8,0xc5,0xbd)); nvgStrokeWidth(nvg, 1.0f);
            for (int k=0;k<steps;k++){ float yy=r.getBottom()-(float)(k+1)*Hp/(float)(steps+1); nvgBeginPath(nvg); nvgMoveTo(nvg,X,yy); nvgLineTo(nvg,r.getRight(),yy); nvgStroke(nvg); }
            nvgStrokeColor(nvg, nvgRGB(0x1a,0x1a,0x1a)); nvgStrokeWidth(nvg, 1.8f); nvgBeginPath(nvg);
            for (int k=0;k<steps;k++){ float x0=X+(float)k*Wp/(float)steps; float x1=X+(float)(k+1)*Wp/(float)steps; float y0=r.getBottom()-(float)(k+1)*Hp/(float)(steps+1); (k==0)?nvgMoveTo(nvg,x0,y0):nvgLineTo(nvg,x0,y0); nvgLineTo(nvg,x1,y0); }
            nvgStroke(nvg);
        } else {
            float freq2 = 1.5f + pd->kbFxB[3] * 2.5f, phase2 = pd->kbFxA[3] * 3.14159f;
            nvgStrokeColor(nvg, nvgRGB(0xc8,0xc5,0xbd)); nvgStrokeWidth(nvg, 1.0f); nvgBeginPath(nvg);
            for (int k=0;k<=40;k++){ float t=k/40.0f; float xx=X+t*Wp; float yy=r.getCentreY()-std::sin(t*6.2832f*1.5f)*Hp*0.28f; k==0?nvgMoveTo(nvg,xx,yy):nvgLineTo(nvg,xx,yy); }
            nvgStroke(nvg);
            nvgStrokeColor(nvg, TEAL); nvgStrokeWidth(nvg, 1.8f); nvgBeginPath(nvg);
            for (int k=0;k<=40;k++){ float t=k/40.0f; float xx=X+t*Wp; float yy=r.getCentreY()-std::sin(t*6.2832f*freq2+phase2)*Hp*0.28f; k==0?nvgMoveTo(nvg,xx,yy):nvgLineTo(nvg,xx,yy); }
            nvgStroke(nvg);
        }
        nvgRestore(nvg);
        // nodo de control
        nvgBeginPath(nvg); nvgCircle(nvg, nodeX, nodeY, 4.0f); nvgFillColor(nvg, TEAL); nvgFill(nvg);
        nvgBeginPath(nvg); nvgCircle(nvg, nodeX, nodeY, 4.0f); nvgStrokeColor(nvg, nvgRGB(0x1a,0x1a,0x1a)); nvgStrokeWidth(nvg,1.0f); nvgStroke(nvg);
        // etiquetas
        char la[28], lb[28];
        if (fxi == 0) { std::snprintf(la, 28, "freq"); std::snprintf(lb, 28, "width %d", (int)std::round(by*100)); }
        else { std::snprintf(la, 28, "%s %d", fxParamLab[fxi][0], (int)std::round(ax*100)); std::snprintf(lb, 28, "%s %d", fxParamLab[fxi][1], (int)std::round(by*100)); }
        nvgFontFace(nvg, "Inter-SemiBold"); nvgFontSize(nvg, 8.0f); nvgFillColor(nvg, nvgRGB(0x8a,0x81,0x70));
        nvgTextAlign(nvg, NVG_ALIGN_LEFT | NVG_ALIGN_TOP); nvgText(nvg, X+4, Y+3, lb, nullptr);
        // OFF: aviso claro de que el efecto está desactivado (mix a 0) -> activar abajo
        if (!fxOn) {
            nvgTextAlign(nvg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);
            nvgFontFace(nvg, "Inter-Bold"); nvgFontSize(nvg, 10.0f); nvgFillColor(nvg, TEAL);
            nvgText(nvg, r.getCentreX(), r.getCentreY()-5.0f, "desactivado", nullptr);
            nvgFontFace(nvg, "Inter-SemiBold"); nvgFontSize(nvg, 8.0f); nvgFillColor(nvg, nvgRGB(0x8a,0x81,0x70));
            nvgText(nvg, r.getCentreX(), r.getCentreY()+8.0f, "sube el mix para activar \xe2\x86\x93", nullptr);
        }
    }
    // === barra de MIX del efecto enfocado ===
    {
        auto mr = kobossDelayMixRect(); float mix = pd->kbFxMix[fxi];
        bool on = (mix > 0.0001f);
        // raíl
        nvgBeginPath(nvg); nvgRect(nvg, mr.getX(), mr.getY(), mr.getWidth(), mr.getHeight());
        nvgFillColor(nvg, nvgRGB(0xe6,0xe3,0xd9)); nvgFill(nvg);
        // relleno = mix
        nvgBeginPath(nvg); nvgRect(nvg, mr.getX(), mr.getY(), mr.getWidth()*mix, mr.getHeight());
        nvgFillColor(nvg, on ? TEAL : nvgRGB(0xc8,0xc5,0xbd)); nvgFill(nvg);
        if (on) {
            char mb[16]; std::snprintf(mb, 16, "mix %d", (int)std::round(mix*100));
            nvgFontFace(nvg, "Inter-SemiBold"); nvgFontSize(nvg, 8.0f);
            nvgFillColor(nvg, nvgRGB(0xfa,0xf6,0xec));
            nvgTextAlign(nvg, NVG_ALIGN_LEFT | NVG_ALIGN_MIDDLE);
            nvgText(nvg, mr.getX()+5.0f, mr.getCentreY(), mb, nullptr);
        } else {
            // CTA: a 0 la barra se perfila en teal y lo dice -> el ojo va aquí
            nvgBeginPath(nvg); nvgRect(nvg, mr.getX()+0.5f, mr.getY()+0.5f, mr.getWidth()-1.0f, mr.getHeight()-1.0f);
            nvgStrokeColor(nvg, TEAL); nvgStrokeWidth(nvg, 1.0f); nvgStroke(nvg);
            nvgFontFace(nvg, "Inter-SemiBold"); nvgFontSize(nvg, 8.0f);
            nvgFillColor(nvg, TEAL);
            nvgTextAlign(nvg, NVG_ALIGN_LEFT | NVG_ALIGN_MIDDLE);
            nvgText(nvg, mr.getX()+5.0f, mr.getCentreY(), "mix 0 · arrastra para activar", nullptr);
        }
    }
    // botón COLAPSAR (x)
    { auto tb=kobossDelayFxTabRect();
      nvgBeginPath(nvg); nvgRoundedRect(nvg,tb.getX(),tb.getY(),tb.getWidth(),tb.getHeight(),2.0f);
      nvgFillColor(nvg,TEAL); nvgFill(nvg);
      nvgFontFace(nvg,"Inter-Bold"); nvgFontSize(nvg,8.5f); nvgFillColor(nvg,nvgRGB(0xfa,0xf6,0xec));
      nvgTextAlign(nvg,NVG_ALIGN_CENTER|NVG_ALIGN_MIDDLE); nvgText(nvg,tb.getCentreX(),tb.getCentreY()-0.5f,"x",nullptr); }
    } else {
        // COLAPSADO: CTA "+ fx" (o "fx · N on" si hay efectos activos)
        auto tb=kobossDelayFxTabRect(); NVGcolor FXA=kobossFxAccent();
        int nOn = 0; for (int i=0;i<4;++i) if (pd->kbFxMix[i] > 0.0001f) ++nOn;
        nvgBeginPath(nvg); nvgRoundedRect(nvg,tb.getX(),tb.getY(),tb.getWidth(),tb.getHeight(),3.0f);
        if (nOn>0) { nvgFillColor(nvg,FXA); nvgFill(nvg); }
        else { nvgStrokeColor(nvg,FXA); nvgStrokeWidth(nvg,1.2f); nvgStroke(nvg); }
        nvgFontFace(nvg,"Inter-SemiBold"); nvgFontSize(nvg,10.5f);
        nvgFillColor(nvg, nOn>0?nvgRGB(0xfa,0xf6,0xec):FXA);
        nvgTextAlign(nvg,NVG_ALIGN_CENTER|NVG_ALIGN_MIDDLE);
        char cta[24];
        if (nOn>0) std::snprintf(cta,24,"fx · %d on",nOn);
        else       std::snprintf(cta,24,"+ fx");
        nvgText(nvg,tb.getCentreX(),tb.getCentreY()-1.0f,cta,nullptr);
    }
}

// ── handlers de ratón del delay ─────────────────────────────────────────────
void kobossDelaySendParam(int idx) {
    auto pd = editor->pd;
    switch (idx) {
        case 0: break; // el time lo reenvia render() desde bpm_host + division
        case 1: pd->sendFloat("feedback", pd->kbFeedback * 95.0f); break;
        case 2: pd->sendFloat("mix",      pd->kbMix * 100.0f); break;
        // 3/4 (params de efecto) y mix/orden de la cadena los reenvía render() desde kbFxA/B/Mix/Order
        case 5: pd->sendFloat("out", std::pow(10.0f, (-24.0f + pd->kbOut * 30.0f) / 20.0f)); break;
        case 6: pd->sendFloat("duck", pd->kbDuck); break;
    }
}
// valor por defecto de cada knob (para reset con doble-click / rueda)
static float kobossDelayKnobDefault(int k) {
    switch (k) { case 1: return 0.35f; case 2: return 0.35f; case 5: return 0.8f; case 6: return 0.0f; }
    return 0.5f;
}

bool handleKobossDelayClick(juce::MouseEvent const& e) {
    auto const p = e.getPosition();
    // ── modo rename activo: solo procesar ok/cancel (o click fuera = cancelar) ──
    if (kobossDelayRenaming) {
        auto pc = kobossContent(kobossModPreset());
        auto okR = juce::Rectangle<float>(pc.getRight()-50.0f, pc.getCentreY()-8.0f, 22.0f, 16.0f);
        auto cxR = juce::Rectangle<float>(pc.getRight()-25.0f, pc.getCentreY()-8.0f, 21.0f, 16.0f);
        if (okR.contains(p.toFloat())) {
            std::string name = kobossDelayRenameText.empty() ? "user" : kobossDelayRenameText;
            kobossDelayRenaming = false; giveAwayKeyboardFocus(); setWantsKeyboardFocus(false);
            kobossSaveUserPresetNamed(name);
        } else {
            kobossDelayRenaming = false; giveAwayKeyboardFocus(); setWantsKeyboardFocus(false);
            if (editor) editor->nvgSurface.invalidateAll();
        }
        return true;
    }
    // aviso de update en el footer (esquina inf-dcha del módulo OUT) -> abre la web
    if (kobossDelayUpd().load()) {
        auto oc = kobossContent(kobossModOut());
        auto hit = juce::Rectangle<float>(oc.getRight()-98.0f, oc.getCentreY()+1.0f, 98.0f, 12.0f);
        if (hit.contains(p.toFloat())) {
            juce::URL("https://kobossbeats.com/delay").launchInDefaultBrowser();
            return true;
        }
    }
    // pestaña FX: abrir / cerrar el panel lateral
    if (kobossDelayFxTabRect().contains(p.toFloat())) {
        kobossDelayFxExpanded = !kobossDelayFxExpanded;
        if (editor) editor->nvgSurface.invalidateAll();
        return true;
    }
    // limpiar estados de arrastre (se re-arman si se pulsa el control correspondiente)
    kobossDelayKnobDrag = -1;
    kobossDelayXYActive = false;
    kobossDelayMixDrag = false;
    if (kobossDelayFxExpanded) {
        int fxi = editor->pd->kbFx < 0 ? 0 : (editor->pd->kbFx > 3 ? 3 : editor->pd->kbFx);
        // XY pad del efecto enfocado: X=param A, Y=param B (doble-clic = reset)
        if (kobossDelayXYRect().contains(p.toFloat())) {
            auto r = kobossDelayXYRect();
            // si el efecto está apagado (mix 0), tocar el pad lo ACTIVA a un default audible
            // -> nunca pasa que tocas los params y no suena
            static const float kFxOnDefault[4] = { 1.0f, 0.5f, 0.4f, 0.5f };
            if (editor->pd->kbFxMix[fxi] <= 0.0001f) editor->pd->kbFxMix[fxi] = kFxOnDefault[fxi];
            if (e.getNumberOfClicks() >= 2) {
                editor->pd->kbFxA[fxi] = 0.5f; editor->pd->kbFxB[fxi] = (fxi==0 ? 1.0f : 0.5f);
            } else {
                editor->pd->kbFxA[fxi] = juce::jlimit(0.0f, 1.0f, ((float)p.x - r.getX()) / r.getWidth());
                editor->pd->kbFxB[fxi] = juce::jlimit(0.0f, 1.0f, (r.getBottom() - (float)p.y) / r.getHeight());
                kobossDelayXYActive = true;
            }
            if (editor) editor->nvgSurface.invalidateAll();
            return true;
        }
        // barra de MIX del efecto enfocado (a 0 = efecto apagado)
        if (kobossDelayMixRect().contains(p.toFloat())) {
            auto mr = kobossDelayMixRect();
            editor->pd->kbFxMix[fxi] = juce::jlimit(0.0f, 1.0f, ((float)p.x - mr.getX()) / mr.getWidth());
            kobossDelayMixDrag = true;
            if (editor) editor->nvgSurface.invalidateAll();
            return true;
        }
        // pestañas: enfocar el efecto (no es on/off — el mix lo enciende/apaga)
        int ft = kobossDelayFxAt(p);
        if (ft >= 0) { editor->pd->kbFx = ft; if (editor) editor->nvgSurface.invalidateAll(); return true; }
    }
    // módulo PRESET: flechas explícitas (izq) + [del] (user) + [save] (dcha), sin solapes
    {
        auto pm = kobossModPreset();
        if (pm.contains(p.toFloat())) {
            int nfact; kobossDelayPresetTable(nfact);
            bool isUser = (editor->pd->kobossActivePreset >= nfact);
            if (kobossDelaySaveRect().contains(p.toFloat())) {
                // overwrite si el preset activo es user → pre-rellenar el nombre
                kobossDelayRenameText = isUser ? std::string(kobossPresetAt(editor->pd->kobossActivePreset).name) : "";
                kobossDelayRenaming = true;
                setWantsKeyboardFocus(true); grabKeyboardFocus();
            }
            // [del] SOLO intercepta en presets de usuario (antes robaba el clic del next en los de fábrica)
            else if (isUser && kobossDelayDeleteRect().contains(p.toFloat())) {
                kobossDeleteUserPreset(editor->pd->kobossActivePreset);
            }
            // flechas EXPLÍCITAS (con un pelín de holgura), agrupadas a la izquierda
            else if (kobossDelayPresetPrevRect().expanded(2.0f).contains(p.toFloat())) kobossDelayApplyPreset(editor->pd->kobossActivePreset - 1);
            else if (kobossDelayPresetNextRect().expanded(2.0f).contains(p.toFloat())) kobossDelayApplyPreset(editor->pd->kobossActivePreset + 1);
            // clic en zona vacía del módulo: no hace nada (no avanza preset por error)
            return true;
        }
    }
    // toggle ping-pong
    if (kobossDelayPpRect().contains(p.toFloat())) {
        editor->pd->kbPingpong = (editor->pd->kbPingpong != 0) ? 0 : 1;
        editor->pd->sendFloat("pingpong", (float)editor->pd->kbPingpong);
        if (editor) editor->nvgSurface.invalidateAll();
        return true;
    }
    // toggle FREEZE
    if (kobossDelayFreezeRect().contains(p.toFloat())) {
        editor->pd->kbFreeze = (editor->pd->kbFreeze != 0) ? 0 : 1;
        editor->pd->sendFloat("freeze", (float)editor->pd->kbFreeze);
        if (editor) editor->nvgSurface.invalidateAll();
        return true;
    }
    int k = kobossDelayKnobAt(p);
    if (k >= 0) {
        // doble-click en el knob de time alterna sync (1/4..) <-> free (ms)
        if (k == 0 && e.getNumberOfClicks() >= 2) {
            editor->pd->kbSyncMode = (editor->pd->kbSyncMode != 0) ? 0 : 1;
            kobossDelayKnobDrag = -1;
            kobossDelayLastSentMs = -1.0f; // forzar reenvio del time en el nuevo modo
            if (editor) editor->nvgSurface.invalidateAll();
            return true;
        }
        // doble-click en cualquier otro knob = reset a su valor por defecto
        if (k != 0 && e.getNumberOfClicks() >= 2) {
            float dv = kobossDelayKnobDefault(k);
            switch (k) { case 1: editor->pd->kbFeedback=dv; break; case 2: editor->pd->kbMix=dv; break;
                         case 5: editor->pd->kbOut=dv; break; case 6: editor->pd->kbDuck=dv; break; }
            kobossDelaySendParam(k); kobossDelayKnobDrag = -1;
            if (editor) editor->nvgSurface.invalidateAll();
            return true;
        }
        kobossDelayKnobDrag = k;
        kobossDelayDragY = p.y;
        if (k == 0 && editor->pd->kbSyncMode == 0) {
            float ms = juce::jlimit(30.0f, 2000.0f, editor->pd->kbTimeMs);
            kobossDelayDragV = (ms - 30.0f) / 1970.0f;
        } else if (k == 6) {
            kobossDelayDragV = editor->pd->kbDuck;
        } else {
            int fxi = editor->pd->kbFx < 0 ? 0 : (editor->pd->kbFx > 3 ? 3 : editor->pd->kbFx);
            float cur[6] = { editor->pd->kbTime, editor->pd->kbFeedback,
                             editor->pd->kbMix, editor->pd->kbFxA[fxi], editor->pd->kbFxB[fxi],
                             editor->pd->kbOut };
            kobossDelayDragV = cur[k];
        }
        return true;
    }
    return true; // consumir clicks dentro del editor del delay
}

bool handleKobossDelayDrag(juce::MouseEvent const& e) {
    auto const mp = e.getPosition();
    int fxi = editor->pd->kbFx < 0 ? 0 : (editor->pd->kbFx > 3 ? 3 : editor->pd->kbFx);
    // XY pad del efecto enfocado: set absoluto a la posición del ratón
    if (kobossDelayXYActive) {
        auto r = kobossDelayXYRect();
        editor->pd->kbFxA[fxi] = juce::jlimit(0.0f, 1.0f, ((float)mp.x - r.getX()) / r.getWidth());
        editor->pd->kbFxB[fxi] = juce::jlimit(0.0f, 1.0f, (r.getBottom() - (float)mp.y) / r.getHeight());
        if (editor) editor->nvgSurface.invalidateAll();
        return true;
    }
    // barra de MIX del efecto enfocado
    if (kobossDelayMixDrag) {
        auto mr = kobossDelayMixRect();
        editor->pd->kbFxMix[fxi] = juce::jlimit(0.0f, 1.0f, ((float)mp.x - mr.getX()) / mr.getWidth());
        if (editor) editor->nvgSurface.invalidateAll();
        return true;
    }
    if (kobossDelayKnobDrag < 0) return false;
    int dY = kobossDelayDragY - e.getPosition().y;
    float nv = juce::jlimit(0.0f, 1.0f, kobossDelayDragV + (float)dY / 150.0f);
    switch (kobossDelayKnobDrag) {
        case 0:
            if (editor->pd->kbSyncMode == 0) editor->pd->kbTimeMs = 30.0f + nv * 1970.0f; // ms libres
            else                              editor->pd->kbTime = nv;                     // posicion division
            break;
        case 1: editor->pd->kbFeedback = nv; break;
        case 2: editor->pd->kbMix = nv; break;
        case 5: editor->pd->kbOut = nv; break;
        case 6: editor->pd->kbDuck = nv; break;
    }
    kobossDelaySendParam(kobossDelayKnobDrag);
    if (editor) editor->nvgSurface.invalidateAll();
    return true;
}

// soltar el ratón: terminar los arrastres del panel FX
void handleKobossDelayUp() {
    kobossDelayXYActive = false;
    kobossDelayMixDrag = false;
}

// ── rueda del ratón: ajuste fino del knob/pad bajo el cursor ─────────────────
bool handleKobossDelayWheel(juce::MouseEvent const& e, juce::MouseWheelDetails const& w) {
    auto const p = e.getPosition();
    float d = w.deltaY * (w.isReversed ? -1.0f : 1.0f);
    if (d == 0.0f) return false;
    float step = juce::jlimit(-0.08f, 0.08f, d * 0.6f);   // adapta rueda(notch grande)/trackpad(fino)
    if (kobossDelayFxExpanded) {
        int fxi = editor->pd->kbFx < 0 ? 0 : (editor->pd->kbFx > 3 ? 3 : editor->pd->kbFx);
        // sobre la barra de mix -> ajusta el mix del efecto enfocado
        if (kobossDelayMixRect().contains(p.toFloat())) {
            editor->pd->kbFxMix[fxi] = juce::jlimit(0.0f, 1.0f, editor->pd->kbFxMix[fxi] + step);
            if (editor) editor->nvgSurface.invalidateAll(); return true;
        }
        // sobre el XY pad -> ajusta el param B (vertical)
        if (kobossDelayXYRect().contains(p.toFloat())) {
            editor->pd->kbFxB[fxi] = juce::jlimit(0.0f, 1.0f, editor->pd->kbFxB[fxi] + step);
            if (editor) editor->nvgSurface.invalidateAll(); return true;
        }
    }
    int k = kobossDelayKnobAt(p);
    if (k < 0) return false;
    switch (k) {
        case 0:
            if (editor->pd->kbSyncMode == 0) editor->pd->kbTimeMs = juce::jlimit(30.0f, 2000.0f, editor->pd->kbTimeMs + step * 1970.0f);
            else                              editor->pd->kbTime   = juce::jlimit(0.0f, 1.0f, editor->pd->kbTime + step);
            kobossDelayLastSentMs = -1.0f;
            break;
        case 1: editor->pd->kbFeedback = juce::jlimit(0.0f, 1.0f, editor->pd->kbFeedback + step); break;
        case 2: editor->pd->kbMix      = juce::jlimit(0.0f, 1.0f, editor->pd->kbMix + step); break;
        case 5: editor->pd->kbOut      = juce::jlimit(0.0f, 1.0f, editor->pd->kbOut + step); break;
        case 6: editor->pd->kbDuck     = juce::jlimit(0.0f, 1.0f, editor->pd->kbDuck + step); break;
    }
    if (editor) editor->nvgSurface.invalidateAll();
    return true;
}
