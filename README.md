# Open WebUI + Draw Things — részletes útmutató

Cél: **chat** az Open WebUI-ban, **képgenerálás** a `draw-things-cli generate` parancs meghívásával (HTTP bridge), **stílus-presetek**, **varázsló JSON**, **opcionális élő progress** és **CLI-szerű** alapútvonal (minőség).

---

## 1. Architektúra (rétegek)

```
┌─────────────────────┐     HTTP / SSE      ┌──────────────────────────┐
│  Open WebUI (bárhol)│ ──────────────────► │  drawthings_bridge       │
│  Pipe (Python)      │  POST /generate    │  FastAPI (Python)        │
│                     │  vagy /stream      │  localhost:8787 (tipik.) │
└─────────────────────┘                    └────────────┬─────────────┘
                                                          │
                                                          │ subprocess
                                                          ▼
                                               ┌──────────────────────┐
                                               │  draw-things-cli     │
                                               │  (macOS, a Macen)    │
                                               │  Models mappa + .ckpt│
                                               └──────────────────────┘
```

| Réteg | Szerep |
|--------|--------|
| **Open WebUI** | Chat UI; a képet **nem** a böngésző rajzolja — a **Pipe** HTTP-n hívja a bridge-et. |
| **Pipe** (`openwebui_pipe/drawthings_bridge_pipe.py`) | Parsolja a user üzenetet / JSON-t, **összeállítja a promptot**, **stílus preset**, merge-eli a **Valves**-t, **POST** a bridge-re. |
| **drawthings_bridge** | `POST /generate` (szinkron) vagy `POST /generate/stream` (SSE) → `draw-things-cli generate` + `--config-json` **csak ha kell**. |
| **draw-things-cli** | Ugyanaz, mint a **konzolból** futtatott parancs; **nem** a bridge „generál”, csak **meghívja** a CLI-t. |

**Fontos:** A bridge-nek **azon a gépen** kell futnia, ahol a Draw Things modellek és a **`draw-things-cli`** elérhető (tipikusan a Mac).

---

## 2. Bridge: telepítés és futtatás

### 2.1 Manuális indítás

```bash
cd drawthings_bridge
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DRAWTHINGS_BRIDGE_PORT=8787
# opcionális: export DRAWTHINGS_MODELS_DIR="$HOME/Library/Containers/com.liuliu.draw-things/Data/Documents/Models"
uvicorn main:app --host 0.0.0.0 --port 8787
```

### 2.2 Autostart (macOS LaunchAgent)

```bash
cd /path/to/OpenWebUI_MAX/drawthings_bridge
./install-macos-launchagent.sh
```

- Logok: `~/Library/Logs/drawthings-bridge.log` és `.error.log`  
- Eltávolítás: `./uninstall-macos-launchagent.sh`  
- **Port / host:** `run_bridge.sh` vagy környezeti változók: `DRAWTHINGS_BRIDGE_PORT`, `DRAWTHINGS_BRIDGE_HOST`

### 2.3 Bridge API (összefoglaló)

| Endpoint | Leírás |
|----------|--------|
| `GET /health` | Életjel |
| `GET /status` | Fut, PID, uptime, `cli.available`, `models_dir` |
| `GET /models` | Letöltött `.ckpt` modellek (parsolt lista) |
| `GET /upscalers` | Upscaler `.ckpt` jelöltek (opcionális `?all_ckpt=1`) |
| `POST /generate` | Szinkron: válaszban `image_base64` + utolsó progress meta |
| `POST /generate/stream` | SSE: `event: progress` → `event: done` (base64) vagy `event: error` |

### 2.4 CLI és a bridge környezete

- **`DRAWTHINGS_BRIDGE_NO_SCRIPT`** (alapértelmezés a kódban: **nincs** macOS `script` pseudo-TTY): **gyorsabb**, kevesebb overhead — a tiszta CLI-hez közelít.  
  **Élő SSE progress** ha nem jön soronként: `DRAWTHINGS_BRIDGE_NO_SCRIPT=0` (lásd `cli_runner.py`).

---

## 3. Open WebUI Pipe — mit csinál?

1. **Admin → Functions → Pipe** → új függvény: **`drawthings_bridge_pipe.py`** tartalma beillesztve.  
2. A chatben a **modellválasztóban** olyan modellt válassz, ami a **Pipe**-t / **Draw Things**-et jelenti (nem a sima „chat” LLM), különben a bridge **nem** hívódik.  
3. A **user üzenet** lehet: **szabad szöveg**, **JSON blokk** (varázsló kimenet), **trigger** + rövid szöveg (ha `MERGE_HISTORY_ON_SHORT_TRIGGER` be van kapcsolva).

A Pipe **fő lépései**:

1. **Bundle** kinyerése (`_parse_user_bundle_json` vagy szövegből).  
2. **Stílus** (`style_label` / `style`) + **téma** → **STYLE_PRESETS_JSON** / beágyazott lista — **preset** kiválasztása.  
3. **Prompt** összeállítása: preset **style_prefix / style_suffix** + **Valves STYLE_PREFIX/SUFFIX** + user prompt.  
4. **Negatív**: Valves **NEGATIVE_PROMPT** + preset **negative_prompt** + **NEGATIVE_BY_STYLE_JSON** / **THEME** + user `negative_prompt`.  
5. **Opcionális** angol fordítás (**ENGLISH_PROMPTS**).  
6. **`config_json`** → **csak ha van** (preset CONFIG_JSON, LoRA, **nem** üres merge) — **nem** küldünk felesleges `--config-json`-t, hogy **CLI-szerű** maradjon a hívás.  
7. **z_image** opciók: **Z_IMAGE_PIPELINE_DEFAULTS** (alap **ki**), **Z_IMAGE_REFINER_HIRES**, **UPSCALER_CKPT** — lásd lentebb.  
8. **POST** a bridge-re: `/generate` vagy `/generate/stream` (**STREAM_PROGRESS**).

### 3.1 Kép-varázsló lánc (PicGEN / szabály-alapú)

Ha **WIZARD_OLLAMA_CHAT** be van kapcsolva és megvan az **OLLAMA_BASE_URL** + **OLLAMA_MODEL**, a Pipe **nem** a varázsló LLM streamjét használja a lépésekhez (stabilabb, nem fagy „üres válasz”-ra). A sorrend:

| # | Lépés | Mit csinál a felhasználó |
|---|--------|-------------------------|
| 1 | **Indítás** | Rövid képkérés (pl. „generálj képet”) → stílus-táblázat. |
| 2 | **Stílus** | A táblázat **Kulcs**a (`style_label`), pl. `Digitalis_festmeny`. |
| 3 | **Prompt** | Leírás, mit lássunk a képen (hosszabb szöveg). |
| 4 | **Méret** | Pl. `3:4 normal`, `1024×1024`. |
| 5 | **Első összegzés + lépésszám** | Összegzés + kérdés: **alapértelmezett / preset** vagy **manuális** lépés **12–22** között (pl. `16`, `manuális 18`). |
| 6 | **CFG kérdés** | **`nem`** → preset / pipeline CFG marad, **azonnal indul** a generálás (nincs második összegzés). **`igen`** → következő üzenetben **CFG szám** (pl. `1.2`). |
| 7 | **Második összegzés** (csak ha CFG = igen) | Látható a választott **steps** és **CFG**; ha jó: **`KÉSZ MEHET`** / `mehet` / `igen` → generálás. |

Az **LLM** ebben a módban főleg **fordításra** (**ENGLISH_PROMPTS**) és **képszándék nélküli általános chatre** kell; a varázsló lépései **parsolva** mennek.

**WIZARD_SYSTEM_PROMPT:** referencia / másolható szöveg; a futó varázsló **nem** küldi automatikusan a modellnek. **WIZARD_DETERMINISTIC_FLOW:** kompatibilitási mező, **nem** vált ágat.

### 3.2 Alapértelmezések — mi történik **automatikusan** (mit nem kell egyenként állítani)

A Pipe **Valves** mezői üresen / alapértelmezett értékkel hagyva is működnek, ha a bridge elérhető és a Draw Things modell fent van. Lényeg:

| Terület | Alapból (automatikus) |
|--------|------------------------|
| **Bridge cím** | A repo alapja: **`http://10.0.0.136:8787`** (**BRIDGE_URL**) — a te hálózatodhoz igazítsd; ha az OWUI és a bridge **ugyanazon a gépen** van: `http://127.0.0.1:8787`. |
| **Kép-modell (.ckpt)** | **`z_image_turbo_1.0_q8p.ckpt`** (**DEFAULT_MODEL**), ha a modellválasztó nem ad más `.ckpt`-t. A listában az első sor: *„Draw Things + beszélgetés (varázsló → JSON → kép)”* → ugyanez az alap modell. A beágyazott **nsfw** stílus külön modellt használ: **`zimageturbonsfw_45bf16diffusion_f16.ckpt`** (CFG alapból **0,8**). |
| **Stílus-preset lista** | **STYLE_PRESETS_JSON** üres vagy `{}` → a Pipe **beágyazott** 15 stílus (Anime, Fotorealisztikus, …, nsfw). Nem kell bemásolni. |
| **Stílus választása** | **Nem kell** külön mezőt állítani: a **`style_label`** a varázsló JSON-ban / bundle-ben, **vagy** a preset **neve megjelenik** a prompt / téma / stílus szövegben (pl. „cyberpunk” → **Cyberpunk** preset). Ha **semmi nem illik**, nincs preset: a modell továbbra is **DEFAULT_MODEL**, lépés/CFG **a CLI** sajátja. |
| **Méret (width×height)** | **WIDTH** / **HEIGHT** alapból **nincs megadva** → ha a bundle/JSON sem ad méretet és a kiválasztott presetnek sincs saját mérete, a Pipe **nem küld** `--width`/`--height`-et → **a draw-things-cli / modell alap felbontása** érvényesül. |
| **Lépés (steps) / CFG** | **STEPS** / **CFG** Valves üres → **varázsló / kézi JSON** (`steps` / `cfg`) → **stílus preset** → ha mind **üres**, **CLI alap**. |
| **Seed** | **véletlen** (nincs fixálva). |
| **Extra pipeline** | **Z_IMAGE_PIPELINE_DEFAULTS** = **ki** → nincs automatikus UniPC/refiner injektálás (CLI-szerű). **UPSCALER_CKPT** üres → nincs utó-felskálázás. |
| **Negatív prompt (globális)** | **NEGATIVE_PROMPT** üres → csak a **preset** negatívja (ha van preset), plusz opcionális **NEGATIVE_BY_*_JSON** (alapból üres). |
| **Angol prompt** | **ENGLISH_PROMPTS** = **be** → ha van **OLLAMA_MODEL** + URL, **először LLM** fordít (pozitív + negatív); különben `langdetect` + `deep-translator` (pip). |
| **Indítás / trigger** | **TRIGGER_MODE** = **off** → nem kell „KÉSZ MEHET”; a teljes üzenet lehet prompt. **MERGE_HISTORY_ON_SHORT_TRIGGER** alapból **be** (rövid trigger + előzmény). |
| **Explicit képkérés** | **REQUIRE_EXPLICIT_IMAGE_REQUEST** = **igaz** → közvetlen generáláshoz kell valamilyen **képkérés** (pl. „Generálj képet…”), vagy **JSON**, vagy trigger — ez **biztonság / félreértés** ellen; ha zavar, kikapcsolható (`false`). |
| **Kép-varázsló + LLM** | **WIZARD_OLLAMA_CHAT** alapból **be** — a **lépések** (stílus → prompt → méret → lépés/CFG → összegzés) **szabály-alapúak**, LM Studio stream **nem** kell hozzájuk. **OLLAMA_BASE_URL** + **OLLAMA_MODEL** kell a varázsló **belépéséhez** és a **fordítás / általános chathez**; modellnév **üres** → nincs varázsló belépés, marad kézi prompt / JSON. |
| **Általános chat kép nélkül** | Ha van **OLLAMA_***, de nincs explicit képszándék: **WIZARD_GENERAL_CHAT_WHEN_NO_IMAGE_INTENT** = **be** → rövid beszélgetés ugyanazzal az LLM-mel. |
| **Élő progress + ETA** | **STREAM_PROGRESS** alapból **be** (SSE + gyűrű). **Ki** = szinkron `/generate` — **előtte** kiírja: *„Képgenerálás folyamatban”*, hogy lásd: fut a kérés. A **részleges kép** a CLI miatt **nem** streamelhető. |
| **Upscale** | **UPSCALER_CKPT** alapból **üres** (stabil). Ha gond van, ne kapcsold be. |
| **Bridge Mac** | `run_bridge.sh`: **`DRAWTHINGS_BRIDGE_NO_SCRIPT=0`** (alap) — élő CLI sorok → SSE progress. |
| **Beállítások összegzése chatben** | **SHOW_GENERATION_PARAMS** = **be** → „Draw Things — beállítások” blokk generálás előtt. |

**Összefoglalva:** alapból **nem** kell stíluslistát, méretet, lépésszámot beírni a Valves-ba — a **beágyazott presetek** + **szöveges találat** + **CLI** elég. **Kötelezően** csak a saját környezetedhez igazíts: **BRIDGE_URL** (ha nem lokális), és ha **interaktív kép-varázslót** akarsz (lásd **3.1**): **OLLAMA_MODEL** + **OLLAMA_BASE_URL**.

---

## 4. Valves (Pipe beállítások) — áttekintés

Az Open WebUI **Admin → Functions → Pipe → Valves** panelen. A **leírás** mezők a kódban (`Field(description=...)`) részletesek; itt csoportosítva:

### 4.1 Hálózat és modell

| Kulcs | Jelentés |
|--------|----------|
| **BRIDGE_URL** | Bridge URL **perjel nélkül**. A Pipe alapja ebben a repóban: `http://10.0.0.136:8787` — állítsd a bridge valódi címére. LXC-ből: **Mac LAN IP** vagy DDNS, **nem** a konténer `127.0.0.1`-je. |
| **DEFAULT_MODEL** | Alap `.ckpt` fájlnév, ha a választó nem ad egyértelmű modellt. |

### 4.2 Prompt és negatív

| Kulcs | Jelentés |
|--------|----------|
| **NEGATIVE_PROMPT** | Globális negatív részlet (minden generáláshoz). |
| **STYLE_PREFIX** / **STYLE_SUFFIX** | A user prompt **elé / mögé** (preset előtag/utótag mellett). |
| **NEGATIVE_BY_STYLE_JSON** | Kulcsszó → extra negatív részlet (a stílus/téma/prompt szövegben keres). |
| **NEGATIVE_BY_THEME_JSON** | Ugyanígy téma kulcsokhoz. |
| **STRIP_IMAGE_PREFIX** | Levágja a „generálj képet…” típusú elejét. |

### 4.3 Méret, lépés, CFG, seed

| Kulcs | Jelentés |
|--------|----------|
| **WIDTH** / **HEIGHT** | Globális felbontás, ha a bundle/JSON nem adja meg. |
| **STEPS** / **CFG** / **SEED** | **Globális felülírás** — ha meg vannak adva, **minden mást** felülírnak (preset + user JSON). Ha **üresek**, a sorrend: **user JSON** → **preset** (§4.3). |

**Feloldási sorrend** (lépés és CFG): **Valves STEPS/CFG** → **user JSON / bundle** (`steps`, `cfg`, `guidanceScale`) → **stílus preset**. A varázsló által adott érték így **nem** vész el a preset mögött.

### 4.4 Haladó: `config_json` és LoRA

| Kulcs | Jelentés |
|--------|----------|
| **CONFIG_JSON** | String JSON (Draw Things **JSGenerationConfiguration** részleges JSON). |
| **LORA_BY_STYLE_JSON** | Kulcsszó → részleges `config_json` (deep merge a CONFIG_JSON-szal). |

### 4.5 z_image (turbo) pipeline

| Kulcs | Alap | Jelentés |
|--------|------|----------|
| **Z_IMAGE_PIPELINE_DEFAULTS** | **hamis** | **Hamis** = **nem** injektálunk extra `config_json`-t (UniPC sampler stb.) — **ugyanaz a vonal**, mint **--config-json nélküli** CLI. **Igaz** = UniPC Trailing (`sampler`: 17) + opcionális refiner/hires. |
| **Z_IMAGE_REFINER_HIRES** | hamis | Refiner + hires fix; **lassabb / instabil** egyes gépeken („no tensors”, homály). |
| **REFINER_START** | 0.75 | Ha a refiner be van kapcsolva. |
| **Z_IMAGE_MIN_STEPS** | 12 | Ha refiner/upscaler aktív: min. lépés; **0** = clamp kikapcsolva. |
| **UPSCALER_CKPT** | üres | Post-upscaler `.ckpt` fájlnév; üres = nincs. |
| **UPSCALER_SCALE_FACTOR** | 2.0 | Upscaler skála. |

### 4.6 Mikor indul generálás (trigger / intent)

| Kulcs | Jelentés |
|--------|----------|
| **TRIGGER_MODE** | `off` / `optional` / `required` — mikor fusson a kép. |
| **TRIGGER_REGEX** | Trigger szöveg (pl. „KÉSZ MEHET”). |
| **MERGE_HISTORY_ON_SHORT_TRIGGER** | Rövid trigger esetén az előző user üzenetek is bekerülnek. |
| **REQUIRE_EXPLICIT_IMAGE_REQUEST** | Csak explicit képkérés / JSON / trigger esetén. |
| **IMAGE_REQUEST_REGEX** | Explicit képkérés felismerése. |

### 4.7 Progress és stream

| Kulcs | Alap | Jelentés |
|--------|------|----------|
| **STREAM_PROGRESS** | **igaz** | **Igaz** (alap) = `/generate/stream` + **httpx** (SSE) + ETA. **Hamis** = `/generate` POST + *„folyamatban”* üzenet a várakozás alatt. |
| **STREAM_PROGRESS_UI** | ring | `ring` vagy `bar`. |
| **STREAM_PROGRESS_MIN_DELTA** | ~0.08 | **Emitter nélküli** (yield) módban: két frissítés között min. haladás (kevesebb duplázott sor). |
| **STREAM_PROGRESS_USE_EVENT_EMITTER** | igaz | Open WebUI `replace` — ugyanabban a buborékban frissül a gyűrű. |
| **STREAM_PROGRESS_MIN_REPLACE_INTERVAL_SEC** | **0,5** | **Emitter** módban: min. idő két `replace` között. **Régi 10 s** = a „Képgenerálás” gyűrű gyakorlatilag csak a végén ugrott; állítsd **0,3–0,8**-ra élő visszajelzéshez. |
| **STREAM_PROGRESS_HEARTBEAT_SEC** | 10 | Ha az SSE egy ideig nem küld sort, ennyi után újrafrissít (emitter + throttle). |
| **STREAM_PROGRESS_SINGLE_MESSAGE** | hamis | **Hamis** = élő progress; **igaz** = csak a végén egy progress blokk + kép. |
| **SHOW_GENERATION_PARAMS** | igaz | Chatben a „Draw Things — beállítások” összegzés generálás előtt. |

### 4.8 Varázsló (Ollama / LM Studio)

| Kulcs | Jelentés |
|--------|----------|
| **WIZARD_OLLAMA_CHAT** | **Be** = kép-varázsló mód (lásd **3.1**): lépések **üzenetparsolással**, nem LM Studio streammel. |
| **WIZARD_DETERMINISTIC_FLOW** | Kompatibilitási mező; **nem** vált ágat — a varázsló mindig szabály-alapú. |
| **WIZARD_CHAT_BACKEND** | `ollama` vagy `openai` (LM Studio `/v1`) — főleg **általános chat** és **fordítás**. |
| **OLLAMA_BASE_URL** / **OLLAMA_MODEL** (és **WIZARD_API_KEY** LM Studio-hoz) | LLM kapcsolat (varázsló belépés + fordítás + kép nélküli chat). |
| **WIZARD_SYSTEM_PROMPT** | A **régi** beágyazott varázsló szöveg **referenciának** / másolásnak; a futó varázsló **nem** küldi a modellnek. Üres = beágyazott magyar + `{{STYLE_PRESET_LIST}}` / `{{WIZARD_SIZE_TABLE}}`. |
| **ENGLISH_PROMPTS** | Nem angol → fordítás (langdetect+deep-translator vagy LLM). |
| **WIZARD_GENERAL_CHAT_WHEN_NO_IMAGE_INTENT** / **GENERAL_CHAT_SYSTEM_PROMPT** | Ha nincs explicit képszándék: ugyanaz az LLM általános chatként válaszol. |

### 4.9 STYLE_PRESETS_JSON

- **Üres** vagy `{}` = a Pipe **beágyazott** listája. A **kulcsok** (a `style_label`-nak ezekkel kell egyeznie — szóköz és aláhúzás felcserélhető, pl. `Digitalis_festmeny` és `Digitalis festmeny` ugyanaz):

  `Anime` · `Fotorealisztikus` · `Vizfestek` · `Digitalis_festmeny` · `Minimal_flat` · `Cyberpunk` · `Fantasy` · `Vazlat_ceruza` · `Portrait` · `Landscape` · `Termek_foto` · `Sci-Fi` · `3D_CGI` · `Ink_comic` · `nsfw`

- Saját JSON: ugyanaz a séma — `model`, `steps`, `cfg`, `negative_prompt`, `style_prefix`, `style_suffix`, `width`, `height`, `config_json`.

---

## 5. JSON bundle (varázsló / kézi beillesztés)

A Pipe felismeri a ```json … ``` blokkot is. Tipikus mezők:

| Mező | Szerep |
|------|--------|
| `prompt` | Pozitív prompt |
| `negative_prompt` | Negatív |
| `width`, `height` | Méret |
| `steps`, `cfg` / `guidanceScale` | Lépés / CFG — **erősebb a stílus presetnél**; a **Valves** globális STEPS/CFG mindent felülírhat |
| `seed` | Fix seed (vagy `null`) |
| `style_label` / `style` | Stílus preset kulcs (pl. `Anime`, `Fotorealisztikus`, `nsfw`) |

**Példa fájl:** `openwebui_pipe/example_ready_bundle.json`

---

## 6. Anime vs fotorealisztikus — ütközés

Ha a **`style_label": "Anime"`**, de a **prompt** egyértelműen **fotót** kér (`photorealistic`, `realistic skin`, `8k`, `IMAX`, stb.), a Pipe:

- **nem** teszi az „Anime” sort a prompt **fejlécébe** feleslegesen;
- **átvált** a **Fotorealisztikus** beágyazott presetre (ha van a listában), hogy a **fotó** előtag/negatív érvényesüljön, ne az anime illusztrációs.

**Javaslat:** fotóhoz állítsd **`"style_label": "Fotorealisztikus"`** (vagy hagyd az automatikus váltást).

---

## 7. Minőség és sebesség — gyakorlati tippek

1. **CLI egyezés:** **Z_IMAGE_PIPELINE_DEFAULTS = hamis** → nincs extra `--config-json` (sampler injektálás), **hasonló** a `draw-things-cli generate` parancshoz **config nélkül**.  
2. **Ne küldjünk üres okból `config_json`-t:** a régi `zeroNegativePrompt: false` mindig bekerült — **ezt** a Pipe már csak akkor merge-eli, ha a merge-elt configban **tényleg** `zeroNegativePrompt: true` lenne.  
3. **z_image turbo:** kevés lépés + alacsony CFG **konzolból** is jó lehet; a **preset** lépés/CFG értékei a listában vannak.  
4. **Lassú** a filter: **STREAM_PROGRESS** = **hamis** (szinkron `/generate`, progress nélkül); alapból **be** van az SSE — ha nem kell gyűrű/ETA, kapcsold ki.  
5. **Homály / kevés részlet:** a **`steps`** a user JSON-ban **erősebb a presetnél** (lásd §4.3) — kevesebb lépéshez pl. **`"steps": 8`**, nehezékhez emeld (pl. **20**) vagy hagyd a presetet, ha nincs `steps` a JSON-ban.  
6. **Refiner / hires / upscaler:** csak ha **kell** — **Z_IMAGE_REFINER_HIRES** és **UPSCALER_CKPT** külön; **hiba** („no tensors”) vagy **mosott** kép esetén kapcsold ki.

---

## 8. Hibakeresés

| Jelenség | Lehetséges ok |
|----------|----------------|
| **Bridge hiba / CLI exit 1** | `draw-things-cli` kimenet a hibaüzenetben; **no tensors** → refiner/hires/upscaler vagy túl kevés lépés. |
| **Homályos kép** | **Anime** preset + **fotó** prompt (ütközés) — **Fotoreal** preset; vagy **túl kevés** lépés nagy felbontásnál. |
| **Nem indul a kép** | **Rossz modell** a választóban (nem Pipe); **TRIGGER_MODE**; **REQUIRE_EXPLICIT_IMAGE_REQUEST**; **BRIDGE_URL** nem elérhető (LXC → `127.0.0.1` helyett **Mac IP**). |
| **Lassú** | **STREAM_PROGRESS** = **hamis** (szinkron `/generate`); **SSE** kikapcsolva. Ha nincs köztes progress: **DRAWTHINGS_BRIDGE_NO_SCRIPT** (lásd `cli_runner.py`). |
| **Fordítás** | **ENGLISH_PROMPTS** + extra LLM / könyvtár késleltetés — kikapcsolható. |
| **Stílus preset nem érvényesül** | A `style_label` pontos kulcs legyen, vagy szóközzel/az aláhúzással egyező forma — a Pipe normalizálja; a README 4.9 szerinti lista a forrás. |
| **Kép nem jelenik meg a chatben** | A válasz **markdown** `![…](data:image/png;base64,…)` — nagyon nagy üzenetnél / régi OWUI-nál próbálj másik böngészőt vagy frissítést; ellenőrizd, hogy a bridge válaszában van-e `image_base64`. |
| **„Képgenerálás” gyűrű csak a végén** | Valves: **STREAM_PROGRESS_MIN_REPLACE_INTERVAL_SEC** legyen **≤ 0,8** (alap most **0,5**). Ha a Pipe régi mentett értékkel **10**-et használ, állítsd át. Mac bridge: ha az SSE egyáltalán nem küld köztes eseményt, próbáld `DRAWTHINGS_BRIDGE_NO_SCRIPT=0` (pseudo-TTY — `cli_runner.py`). |
| **Stream hiba / All connection attempts failed** | Az OWUI **szerver** nem éri el **BRIDGE_URL**-t (gyakran `127.0.0.1` = rossz, ha az OWUI nem a Macen fut). Állítsd a Mac **LAN IP** + `:8787`-et; `curl http://<IP>:8787/health` az OWUI gépről. Vagy **STREAM_PROGRESS** = hamis. |
| **Rossz modell / nem nsfw preset** | A `style_label` legyen pontosan **`nsfw`** (gyakori elírás: **`nfsw`**). A Pipe ezt **automatikusan `nsfw`-re** képezi le. |

---

## 9. LXC / hálózat

- A konténer **`127.0.0.1`-je nem a Mac** — a **BRIDGE_URL**-ben a **Mac** címe vagy **DDNS** (pl. `:8787`): `http://<Mac_IP>:8787`.  
- **Tűzfal / porttovábbítás:** 8787 elérhető legyen onnan, ahonnan az Open WebUI hívja a bridge-et.

---

## 10. Repo fájlok (fő)

| Útvonal | Szerep |
|---------|--------|
| `drawthings_bridge/main.py` | FastAPI bridge, `/generate`, `/generate/stream` |
| `drawthings_bridge/cli_runner.py` | `draw-things-cli` subprocess, `DRAWTHINGS_BRIDGE_NO_SCRIPT` |
| `drawthings_bridge/config.py` | Környezeti prefix: `DRAWTHINGS_BRIDGE_*` |
| `openwebui_pipe/drawthings_bridge_pipe.py` | Open WebUI **Pipe** (teljes logika) |
| `openwebui_pipe/example_ready_bundle.json` | Példa JSON |
| `drawthings_bridge/install-macos-launchagent.sh` | macOS autostart |

---

## 11. Következő lépések (opcionális)

- **Reverse proxy + auth** ha a bridge nyilvános DDNS-en van.  
- **Saját STYLE_PRESETS_JSON** a Valves-ban — teljes lista másolása a beágyazott példából, majd módosítás.

---

*Utolsó frissítés: a Pipe és bridge viselkedése a `drawthings_bridge_pipe.py` és `cli_runner.py` aktuális kódjához igazodik (kép-varázsló lánc: **3.1**); ha a Valves **régi** mentett értékeket tartalmaz, **ellenőrizd** a panelt (pl. **STREAM_PROGRESS**, **Z_IMAGE_PIPELINE_DEFAULTS**).*
