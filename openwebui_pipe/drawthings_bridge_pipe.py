# Copyright (c) 2026 Miklos Lekszikov
# SPDX-License-Identifier: MIT

"""
Open WebUI Pipe — Draw Things CLI bridge (HTTP + élő progress)

- `STREAM_PROGRESS` **alapból be** — SSE + gyűrű + ETA; **UPSCALER_CKPT** alapból üres (stabil). **Szinkron** módban (`STREAM_PROGRESS` ki) a Pipe **előtte** kiírja: *„Képgenerálás folyamatban”* + gyűrű, hogy lásd: fut a kérés. Mac bridge: `DRAWTHINGS_BRIDGE_NO_SCRIPT=0` a `run_bridge.sh`-ban (élő CLI sorok). **Megjegyzés:** részleges PNG nincs — csak haladás vagy végén teljes kép.

**Beszélgetés + kép egy menetben (ajánlott):** Valves-ban `WIZARD_OLLAMA_CHAT` alapból be, és add meg az **`OLLAMA_MODEL`**-t + **`OLLAMA_BASE_URL`** — a kép-varázsló **szabály-alapú** (stílus → prompt → méret → megerősítés → JSON → Draw Things), **nem** függ a varázsló LLM streamjétől (LM Studio üres válasza nem akasztja el). A varázsló system prompt **be van ágyazva** (referencia / `WIZARD_SYSTEM_PROMPT`). Ha nincs explicit képkérés, ugyanaz az LLM **általános chat** módban válaszol (`WIZARD_GENERAL_CHAT_WHEN_NO_IMAGE_INTENT`, `GENERAL_CHAT_SYSTEM_PROMPT`). A modellválasztóban: **„Draw Things + beszélgetés…”** vagy egy `.ckpt`.

**Régi mód:** a JSON-t külön is bemásolhatod a Pipe-ba.

**Beszélgetés + indítás (csak Pipe, varázsló nélkül):**
1. A fő beszélgetésben egyeztess: stílus, téma, méret, prompt.
2. Másold be ide (Pipe modell) a blokkot, vagy írd: **KÉSZ MEHET** — ha csak ez az üzenet,
   és `MERGE_HISTORY_ON_SHORT_TRIGGER` be van kapcsolva, az előző user üzenetek is bekerülnek a parsolásba.
3. `TRIGGER_MODE` alapból **off** (minden továbbmegy a szűrők után); **required** = csak triggerre indul; **optional** = trigger nélkül is (teljes szöveg = prompt).
4. **NEGATIVE_PROMPT** (Valves) + stílus preset + **NEGATIVE_BY_STYLE_JSON** / **NEGATIVE_BY_THEME_JSON**: rétegezett negatív (lásd `NEGATIVE_PROMPT_STRATEGY.md`).
5. **LORA_BY_STYLE_JSON**: stílus kulcsszó → részleges `config_json` (deep merge a CONFIG_JSON-szal).

Függőségek az Open WebUI szerveren: `requests`, `httpx` (SSE-hez; ha nincs: `pip install httpx`).
**Angol promptok:** `ENGLISH_PROMPTS` alapból be — ha be van állítva **OLLAMA_MODEL** + **OLLAMA_BASE_URL**, a fordítás **először LLM**-mel történik (pozitív + negatív); ha az nem elérhető vagy hibázik: `langdetect` + `deep-translator` (opcionális pip).
Ha a chat nem élőben frissül: böngésző / Open WebUI verzió — próbálj másik böngészőt; ez a kliens viselkedése.

**Interaktív beállítások:** Admin → Functions → Pipe → **Valves**.
A **modell** a chat tetején a modellválasztóban (bridge `/models`).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import math
import base64

# Valves **NEGATIVE_PROMPT** alapértelmezés: minden generálásnál először; utána jön a stílus preset + NEGATIVE_BY_* + user.
# Részletek: `NEGATIVE_PROMPT_STRATEGY.md`. Szándékosan nincs benne általános „text” tiltás (pl. képbe írt cím), csak vízjel/szignó.
_DEFAULT_NEGATIVE_PROMPT_GLOBAL = (
    "worst quality, low quality, blurry, out of focus, jpeg artifacts, watermark, signature, "
    "bad anatomy, deformed hands, deformed feet, extra fingers, fused fingers, missing fingers, "
    "cropped subject, cropped head, chromatic aberration, banding, noise, grain, "
    "motion blur, oversharpened, amateur composition, duplicate subject"
)

_EMBEDDED_WIZARD_SYSTEM_PROMPT_HU = 'Te egy segítő asszisztens vagy, aki képgenerálást készít elő (Draw Things / Open WebUI Pipe bridge). Magyarul válaszolj, röviden és barátságosan.\n\n## Mikor induljon a „varázsló”\nHa a felhasználó képet szeretne, tipikus kérések:\n„generálj képet”, „rajzolj”, „készíts képet”, „képet kérek”, „mutass egy képet”, „image”, „draw”, stb.\nEkkor NE kezdj el azonnal „képet generálni” — nincs közvetlen rajzolásod. Ehelyett kezd el a lépésről lépésre kérdezést.\n\n## Sorrend (kötelező lépések)\n1) **Stílus**\n   A lenti blokk egy **markdown táblázat** (`| oszlop | oszlop |` sorok). A JSON `style_label` mezőben a táblázat **első oszlopának** (pontos kulcs, backtick nélkül a JSON-ban) értékét add meg.\n   **Kötelező:** A felhasználónak **ugyanígy**, markdown táblázatként add vissza ezt a táblázatot — **ne** foglald át felsorolássá (pl. „Név (magyar) - rövid leírás” sorok), **ne** rövidítsd félbe. Először a **teljes táblázat**, utána egy rövid kérdés.\n\n{{STYLE_PRESET_LIST}}\n\n   Kérdés: válasszon **Kulcs**ot a táblázat első oszlopából, vagy írjon saját stílust egy mondattal.\n\n2) **Tartalom (prompt)**\n   Kérdezd: mit szeretne látni a képen? Legyen konkrét (tárgyak, hangulat, színek, kompozíció), de ne írd túl hosszúra az első választ.\n\n3) **Méret**\n   Az alábbi táblázat **képarány szerint** rendezve (extrém állótól a 9:16 függőleges videóig) ad **small / normal / large** felbontásokat. A JSON `width` és `height` mezőbe a választott **szélesség** és **magasság** kerüljön (pixel, 64 többszörös).\n\n{{WIZARD_SIZE_TABLE}}\n\n   Kérdezd: melyik **képarányt** és **méretkövet** válassza (pl. „3:4 normal”, „16:9 small”), vagy adjon meg saját szélesség×magasságot (mindkét szám 64 többszöröse).\n\n## Összegzés\nEgy táblázatszerű vagy felsorolásos blokkban foglald össze:\n- **Stílus:** …\n- **Prompt (mit lássunk):** …\n- **Javasolt negatív (stílushoz igazítva):** … (ha nem tudod, írj általános minőségi negatívot: pl. rossz anatómia, extra ujjak, vízjel, szöveg a képen — stílustól függően)\n- **Méret:** …×…\n- **LoRA / extra:** ha a felhasználó nem kért LoRA-t, írd: „nincs megadva / alapértelmezés”. Ha igen, kérdezd meg pontosan mit (név vagy leírás).\n\n## Finomítás\nKérdezd meg szó szerint:\n„Szeretnél még valamit módosítani a fenti beállításokon?”\n- Ha **igen**: kérdezd meg, pontosan mit (stílus, prompt, méret, negatív, LoRA), majd **frissítsd az összegzést**, és kérdezd újra ugyanezt a módosítás kérdést, amíg azt nem mondja, hogy kész.\n- Ha **nem**: lépj a „kész” formátumra (lentebb).\n\n## Amikor indulhat a generálás (nincs több módosítás)\nEgyetlen blokkban adj ki egy **JSON** objektumot (csak a JSON-t, kódblokkban ```json … ```). Ha a felhasználó az Open WebUI **Draw Things Pipe** + **Ollama varázsló** módot használja, ezt a válaszodat a Pipe **automatikusan** felismeri és ugyanabban a körben elindítja a képet — nem kell külön bemásolni. Más környezetben a JSON bemásolható a Pipe-ba vagy a bridge kérésbe. Példa szerkezet:\n\n```json\n{\n  "ready": true,\n  "prompt": "… teljes, végleges pozitív prompt, stílus kulcsszavakkal …",\n  "negative_prompt": "…",\n  "width": 1024,\n  "height": 1024,\n  "steps": null,\n  "guidanceScale": null,\n  "seed": null,\n  "style_label": "Anime",\n  "notes": "LoRA: nincs / vagy leírás",\n  "user_confirmation": "A felhasználó nem kért további módosítást."\n}\n```\n\nOpcionális mezők a **jobb minőséghez / reprodukálhatósághoz:** `steps` (mintavételezés), `guidanceScale` vagy `cfg` (CFG / irányítás erőssége), `seed` (fix szám = ugyanaz a kép alap). Ha ezeket kihagyod, a draw-things-cli a **modell ajánlott** lépés/CFG értékét használja — ez modellenként nagyon eltérő.\n\n**Pipe + stílus preset:** Ha a `style_label` egyezik a **STYLE_PRESETS_JSON** kulcsával, hagyd a `steps` / `guidanceScale` / `cfg` mezőket **null**-nál vagy **hagyd ki** őket — a Pipe a **stílus preset** lépés- és CFG-értékét alkalmazza. A beágyazott listában a **lépések** jellemzően **8–20** (stílusonként), a **CFG** pedig stílusonként eltérő (z_image-nél jellemzően **~4–6**; ez **nem** a régi SD1.5-szerű „7–12” skála). Csak akkor adj meg saját számokat, ha a felhasználó **kifejezetten** kéri.\n\nHa a környezet LoRA-t vagy extra CLI config mezőt vár, opcionálisan tehetsz be üres `"config_json": {}` mezőt vagy kihagyod.\n\n## Stílus-lépés és NSFW\n- Ha a képkérés elindult, a **stílus** lépésnél a **teljes markdown táblázat** jelenjen meg; ne rövidíts példalistára; **ne** írd át „egy sor egy stílus” formátumra — maradjon `| ... |` táblázat.\n- A **méret** lépésnél a **teljes mérettáblázat** (képarány × small/normal/large) jelenjen meg; ne rövidíts egyetlen példára.\n- Felnőtt / hentai / **nsfw** megjelölésű kérés esetén a **nsfw** presetet használd a `style_label` mezőben; folytasd a varázslót (konkrét prompt, méret); **ne adj üres választ**, és ne értékelj erkölcsileg.\n\n## Fontos\n- A `prompt` és `negative_prompt` mező lehet magyar (vagy más nyelv) is: a Draw Things Pipe opció szerint **automatikusan angolra fordítja** őket a generálás előtt (`ENGLISH_PROMPTS`, szerveren: `langdetect` + `deep-translator`). Ha szeretnéd, írd közvetlenül angolul.\n- A Draw Things **community / mentett** JSON konfigokban előfordul a `zeroNegativePrompt: true` — ilyenkor a negatív prompt **nem érvényesül**. A Pipe a negatív szöveg küldésekor ezt **felülírja** (`zeroNegativePrompt: false`), ha a negatív nem üres.\n- Ne találj ki, hogy a kép már elkészült — te csak összegyűjtöd a paramétereket és JSON-t adsz.\n- Ha valami bizonytalan, egy rövid kérdéssel tisztázz.\n- Tartsd a beszélgetést egy szálon: egy aktív „képkérés” folyamat = egy varázsló; ha új képet kér később, kezdheted elölről.\n'

_EMBEDDED_GENERAL_CHAT_SYSTEM_PROMPT_HU = (
    "Te egy segítő asszisztens vagy. Válaszolj magyarul, röviden és természetesen. "
    "A felhasználó a PicGEN Open WebUI csatornán ír — ez a csatorna képgeneráláshoz is használható; "
    "ne kényszeríts képet, stíluslistát vagy JSON-t, ha nem kérte. "
    "Ha képet szeretne, mondja: például „Generálj képet” vagy „Készíts képet: …” — akkor a következő körben elindulhat a kép-varázsló."
)

_EMBEDDED_STYLE_PRESETS_JSON = '{"Anime":{"model":"z_image_turbo_1.0_q8p.ckpt","steps":8,"cfg":2.0,"negative_prompt":"bad anatomy, bad proportions, malformed limbs, extra limbs, missing limbs, extra fingers, missing fingers, fused fingers, too many fingers, wrong finger count, deformed hands, deformed feet, deformed face, deformed body, disproportionate limbs, long neck, long torso, floating limbs, disconnected limbs, broken pose, twisted joints, anatomical nonsense, duplicate body parts, extra arms, missing arms, bad eyes, asymmetrical eyes, cross-eyed, swollen face, lowres, blurry, out of focus, jpeg artifacts, watermark, signature, text, username, worst quality, low quality, cropped, amateur, oversaturated, flat shading, muddy colors, western cartoon, 3d render, photorealistic skin","style_prefix":"anime style, clean lineart, consistent anatomy, masterpiece, best quality, sharp focus, highly detailed","style_suffix":"coherent pose, single character focus, soft cel shading, crisp linework","width":1024,"height":1024,"config_json":{}},"Fotorealisztikus":{"model":"z_image_turbo_1.0_q8p.ckpt","steps":20,"cfg":4.2,"negative_prompt":"bad anatomy, bad proportions, malformed limbs, extra limbs, missing limbs, extra fingers, missing fingers, fused fingers, too many fingers, deformed hands, deformed feet, deformed face, deformed body, disproportionate limbs, long neck, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, plastic skin, wax skin, doll face, uncanny valley, asymmetrical eyes, cross-eyed, swollen face, skin blemishes artifacts, motion blur, out of focus, jpeg artifacts, watermark, signature, text, worst quality, low quality, oversharpened, oversaturated, cartoon, anime, illustration, painting, 3d render, cgi, duplicate, clone face","style_prefix":"photorealistic, natural skin texture, realistic lighting, sharp focus, professional photography","style_suffix":"correct perspective, natural pose, believable anatomy","width":1024,"height":1024,"config_json":{}},"Vizfestek":{"model":"z_image_turbo_1.0_q8p.ckpt","steps":14,"cfg":4.6,"negative_prompt":"bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed face, deformed body, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, muddy face, muddy hands, digital oversharpen, plastic, wax, 3d, cgi, vector, flat clipart, jpeg artifacts, watermark, text, worst quality, low quality, oversaturated, harsh edges, posterization, banding, chromatic aberration, duplicate subject","style_prefix":"watercolor painting, soft edges, paper texture, gentle washes","style_suffix":"coherent composition, readable silhouette","width":1024,"height":1024,"config_json":{}},"Digitalis_festmeny":{"model":"z_image_turbo_1.0_q8p.ckpt","steps":16,"cfg":4.5,"negative_prompt":"bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed feet, deformed face, deformed body, long neck, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, muddy details, noise, grain overload, jpeg artifacts, watermark, text, worst quality, low quality, flat shading only, amateur, broken perspective, duplicate limbs, asymmetrical face errors, plastic skin, uncanny","style_prefix":"digital painting, detailed brushwork, rich colors, artstation quality","style_suffix":"consistent lighting, coherent anatomy","width":1024,"height":1024,"config_json":{}},"Minimal_flat":{"model":"z_image_turbo_1.0_q8p.ckpt","steps":8,"cfg":4.0,"negative_prompt":"bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed face, deformed body, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, clutter, noise, grain, jpeg artifacts, watermark, text, worst quality, low quality, gradients where flat required, 3d shading, photorealistic texture, busy background, duplicate elements, asymmetry errors on faces","style_prefix":"flat design, minimal, clean shapes, limited palette","style_suffix":"simple composition, readable forms","width":1024,"height":1024,"config_json":{}},"Cyberpunk":{"model":"z_image_turbo_1.0_q8p.ckpt","steps":16,"cfg":4.6,"negative_prompt":"bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed feet, deformed face, deformed body, long neck, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, duplicate body parts, bad eyes, asymmetrical eyes, neon banding, jpeg artifacts, watermark, text, worst quality, low quality, muddy colors, amateur, broken perspective, inconsistent scale, floating objects without intent, cluttered unreadable silhouette","style_prefix":"cyberpunk, neon accents, futuristic city, cinematic lighting","style_suffix":"coherent scale, readable character pose","width":1024,"height":1152,"config_json":{}},"Fantasy":{"model":"z_image_turbo_1.0_q8p.ckpt","steps":14,"cfg":4.5,"negative_prompt":"bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed feet, deformed face, deformed body, long neck, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, duplicate limbs, bad eyes, asymmetrical eyes, cross-eyed, swollen face, muddy textures, jpeg artifacts, watermark, text, worst quality, low quality, modern objects in scene, inconsistent lighting, amateur, broken perspective, melting features","style_prefix":"fantasy illustration, epic lighting, detailed costume, coherent world","style_suffix":"heroic pose, believable anatomy","width":1024,"height":1024,"config_json":{}},"Vazlat_ceruza":{"model":"z_image_turbo_1.0_q8p.ckpt","steps":10,"cfg":4.3,"negative_prompt":"bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed face, deformed body, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, smudged beyond recognition, heavy blur, jpeg artifacts, watermark, text, worst quality, low quality, full color where sketch requested, 3d render, photo, digital painting finish, duplicate strokes chaos, unreadable hands","style_prefix":"pencil sketch, hatching, construction lines, traditional media","style_suffix":"clear silhouette, readable pose","width":1024,"height":1024,"config_json":{}},"Portrait":{"model":"z_image_turbo_1.0_q8p.ckpt","steps":20,"cfg":4.3,"negative_prompt":"bad anatomy, bad proportions, malformed face, deformed face, asymmetrical eyes, cross-eyed, misaligned eyes, swollen face, bad teeth, extra fingers, missing fingers, fused fingers, deformed hands, long neck, double face, duplicate face, plastic skin, wax skin, uncanny, jpeg artifacts, watermark, text, worst quality, low quality, out of focus, motion blur, cropped head, extra limbs, disconnected neck, anatomical nonsense, muddy skin texture","style_prefix":"portrait, head and shoulders, sharp eyes, natural skin texture","style_suffix":"correct facial symmetry, believable expression","width":896,"height":1152,"config_json":{}},"Landscape":{"model":"z_image_turbo_1.0_q8p.ckpt","steps":18,"cfg":4.2,"negative_prompt":"bad perspective, warped horizon, duplicated mountains, melting terrain, floating rocks without intent, inconsistent scale, tiny figures with bad anatomy, extra limbs on people, malformed animals, jpeg artifacts, watermark, text, worst quality, low quality, muddy details, banding, chromatic aberration, oversharpened, amateur composition, incoherent vanishing point, duplicate elements","style_prefix":"landscape, atmospheric perspective, natural lighting, wide shot","style_suffix":"coherent depth, readable focal point","width":1152,"height":896,"config_json":{}},"Termek_foto":{"model":"z_image_turbo_1.0_q8p.ckpt","steps":18,"cfg":4.0,"negative_prompt":"bad anatomy, deformed hands holding product, extra fingers, malformed limbs, floating product, warped product, duplicate products, melted plastic, wrong perspective, jpeg artifacts, watermark, text, worst quality, low quality, busy background, clutter, dirty lens, chromatic aberration, banding, amateur product shot, inconsistent shadows","style_prefix":"product photography, studio lighting, clean background, sharp focus","style_suffix":"accurate materials, correct scale","width":1024,"height":1024,"config_json":{}},"Sci-Fi":{"model":"z_image_turbo_1.0_q8p.ckpt","steps":16,"cfg":4.5,"negative_prompt":"bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed feet, deformed face, deformed body, long neck, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, duplicate body parts, bad eyes, asymmetrical eyes, incoherent spacesuit seams, melting helmet, jpeg artifacts, watermark, text, worst quality, low quality, muddy metal, amateur, broken perspective, inconsistent scale, duplicate modules","style_prefix":"science fiction, detailed environment, cinematic lighting, coherent technology","style_suffix":"believable human scale, readable pose","width":1152,"height":896,"config_json":{}},"3D_CGI":{"model":"z_image_turbo_1.0_q8p.ckpt","steps":16,"cfg":4.4,"negative_prompt":"bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed feet, deformed face, deformed body, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, low poly errors, z-fighting, broken normals, melted mesh, duplicate vertices chaos, uncanny rigging, clipping through body, jpeg artifacts, watermark, text, worst quality, low quality, flat shading where subsurface needed, amateur sculpt","style_prefix":"3d render, octane render style, clean materials, global illumination","style_suffix":"consistent topology look, believable proportions","width":1024,"height":1024,"config_json":{}},"Ink_comic":{"model":"z_image_turbo_1.0_q8p.ckpt","steps":12,"cfg":4.5,"negative_prompt":"bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed face, deformed body, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, messy ink blobs, unreadable silhouette, jpeg artifacts, watermark, text, worst quality, low quality, muddy grays, broken panel composition, duplicate characters, asymmetrical face errors","style_prefix":"ink illustration, comic book, bold lines, selective blacks","style_suffix":"clear gesture, readable pose","width":1024,"height":1024,"config_json":{}},"nsfw":{"model":"zimageturbonsfw_45bf16diffusion_f16.ckpt","steps":20,"cfg":0.8,"negative_prompt":"child, minor, underage, school uniform suggestive minor, bad anatomy, bad proportions, malformed limbs, extra limbs, missing limbs, extra fingers, missing fingers, fused fingers, too many fingers, deformed hands, deformed feet, deformed face, deformed body, disproportionate limbs, long neck, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, duplicate body parts, merged bodies, fused limbs, melting together, overlapping bodies, wrong contact points, worst quality, low quality, blurry, jpeg artifacts, watermark, signature, text, username, censored bar, mosaic censor, disfigured, mutation, extra heads","style_prefix":"adult subject, consenting context, coherent anatomy","style_suffix":"natural proportions, believable pose","width":1024,"height":1024,"config_json":{}}}'

_EMBEDDED_EXTRA_STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "Manga_fekete_feher": {
        "model": "z_image_turbo_1.0_q8p.ckpt",
        "steps": 12,
        "cfg": 4.2,
        "negative_prompt": "color bleed, grayscale banding, halftone errors, smeared inks, muddy screentone, color leak, blurry, lowres, jpeg artifacts, watermark, text, bad anatomy, deformed hands, extra fingers",
        "style_prefix": "black and white manga illustration, screentone, high contrast inks",
        "style_suffix": "clean line quality, readable gesture, sharp panel-like composition",
        "width": 1024,
        "height": 1024,
        "config_json": {},
    },
    "Film_noir": {
        "model": "z_image_turbo_1.0_q8p.ckpt",
        "steps": 18,
        "cfg": 4.4,
        "negative_prompt": "flat lighting, oversaturated colors, modern bright palette, hdr bloom, pastel colors, bright daylight, clean sitcom lighting, led panel look, lowres, blurry, bad anatomy, deformed face, text, watermark",
        "style_prefix": "film noir, dramatic shadows, moody atmosphere, high contrast cinematic lighting",
        "style_suffix": "grainy classic cinema mood, strong composition",
        "width": 1152,
        "height": 896,
        "config_json": {},
    },
    "Pixel_art": {
        "model": "z_image_turbo_1.0_q8p.ckpt",
        "steps": 10,
        "cfg": 4.0,
        "negative_prompt": "photorealistic texture, smooth gradients, anti-aliased blur, bilinear smear, interpolated pixels, wrong aspect ratio blocks, subpixel blur, lowres mush, text, watermark, bad anatomy",
        "style_prefix": "pixel art, crisp pixels, limited color palette, retro game aesthetic",
        "style_suffix": "clean edges, readable silhouette",
        "width": 1024,
        "height": 1024,
        "config_json": {},
    },
    "Isometric": {
        "model": "z_image_turbo_1.0_q8p.ckpt",
        "steps": 14,
        "cfg": 4.3,
        "negative_prompt": "wrong perspective, broken geometry, warped lines, off-axis view, incoherent tile grid, jumbled scale, duplicate modules, cluttered silhouette, blurry, text, watermark, cluttered scene",
        "style_prefix": "isometric illustration, clean geometric perspective, detailed miniature scene",
        "style_suffix": "coherent scale, clear depth layering",
        "width": 1024,
        "height": 1024,
        "config_json": {},
    },
    "Architectural_render": {
        "model": "z_image_turbo_1.0_q8p.ckpt",
        "steps": 20,
        "cfg": 4.2,
        "negative_prompt": "warped perspective, warped verticals, inconsistent vanishing point, lens distortion, melted walls, deformed structures, floating geometry, duplicate facades, structural nonsense, collapsed perspective, lowres, blurry, text, watermark",
        "style_prefix": "architectural visualization, realistic materials, global illumination, clean lines",
        "style_suffix": "accurate perspective, professional archviz presentation",
        "width": 1152,
        "height": 896,
        "config_json": {},
    },
    "Food_photography": {
        "model": "z_image_turbo_1.0_q8p.ckpt",
        "steps": 18,
        "cfg": 4.1,
        "negative_prompt": "plastic food look, synthetic props, unappetizing, mold, slime, hair in food, dirty plate, fly, greasy lens, bad textures, blurry, low detail, messy framing, text, watermark",
        "style_prefix": "food photography, appetizing textures, studio lighting, macro detail",
        "style_suffix": "clean composition, realistic ingredients",
        "width": 1024,
        "height": 1024,
        "config_json": {},
    },
    "Fashion_editorial": {
        "model": "z_image_turbo_1.0_q8p.ckpt",
        "steps": 18,
        "cfg": 4.3,
        "negative_prompt": "bad anatomy, deformed hands, warped limbs, plastic skin, wax skin, double face, asymmetrical eyes, over-smoothing, cheap hdr, muddy skin, blurry, lowres, text, watermark, cheap lighting",
        "style_prefix": "high fashion editorial photography, dramatic studio light, luxury styling",
        "style_suffix": "clean posing, magazine cover quality",
        "width": 896,
        "height": 1152,
        "config_json": {},
    },
    "Concept_art": {
        "model": "z_image_turbo_1.0_q8p.ckpt",
        "steps": 16,
        "cfg": 4.6,
        "negative_prompt": "muddy colors, low detail, bad anatomy, broken perspective, blurry, watermark, text, incoherent scale, floating rocks without intent, muddy focal point, unclear silhouette, cluttered focal area, duplicate horizon, inconsistent atmospheric perspective",
        "style_prefix": "concept art, cinematic matte painting, rich atmosphere, production design quality",
        "style_suffix": "clear focal point, coherent worldbuilding",
        "width": 1152,
        "height": 896,
        "config_json": {},
    },
    "Vintage_film": {
        "model": "z_image_turbo_1.0_q8p.ckpt",
        "steps": 16,
        "cfg": 4.2,
        "negative_prompt": "modern digital oversharp, neon oversaturation, plastic skin, hdr halos, ai gloss, clean phone photo look, lowres, text, watermark",
        "style_prefix": "vintage film still, subtle grain, warm tones, analog cinema look",
        "style_suffix": "timeless mood, balanced composition",
        "width": 1152,
        "height": 896,
        "config_json": {},
    },
    "Dark_fantasy": {
        "model": "z_image_turbo_1.0_q8p.ckpt",
        "steps": 16,
        "cfg": 4.7,
        "negative_prompt": "bright cheerful palette, pastel cute, flat lighting, family friendly illustration, washed out horror, contradictory mood, bad anatomy, deformed hands, lowres, text, watermark",
        "style_prefix": "dark fantasy illustration, ominous atmosphere, dramatic lighting, detailed textures",
        "style_suffix": "coherent anatomy, cinematic composition",
        "width": 1024,
        "height": 1024,
        "config_json": {},
    },
}

from dataclasses import dataclass
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field


def _wizard_style_esc_cell(s: str) -> str:
    """Markdown tábla cella: cső és sortörés biztonság."""
    t = (s or "").replace("\n", " ").replace("|", "\\|").strip()
    return t or "—"


# Varázsló `{{STYLE_PRESET_LIST}}`: kulcs → (magyar név, angol név, rövid magyar leírás)
_WIZARD_STYLE_PRESET_I18N: dict[str, tuple[str, str, str]] = {
    "3D_CGI": (
        "3D CGI",
        "3D CGI",
        "Fotórealisztikus 3D megjelenés, tiszta anyagok, globális megvilágítás.",
    ),
    "Anime": (
        "Anime",
        "Anime",
        "Japán animációs stílus: tiszta vonalak, cel shading, koherens anatómia.",
    ),
    "Architectural_render": (
        "Építészeti látvány",
        "Architectural render",
        "Épületek, archviz: pontos perspektíva, anyagok, professzionális bemutató.",
    ),
    "Concept_art": (
        "Koncept art",
        "Concept art",
        "Film-/játék-előképek: hangulat, világépítés, erős fókuszpont.",
    ),
    "Cyberpunk": (
        "Kiberpunk",
        "Cyberpunk",
        "Neon, futurisztikus város, sci-fi hangulat, éjszakai fények.",
    ),
    "Dark_fantasy": (
        "Sötét fantasy",
        "Dark fantasy",
        "Komor fantasy világ, drámai fény, részletes textúrák.",
    ),
    "Digitalis_festmeny": (
        "Digitális festmény",
        "Digital painting",
        "Digitális festés: ecsetnyomok, gazdag színek, artstation minőség.",
    ),
    "Fantasy": (
        "Fantasy",
        "Fantasy",
        "Fantasy illusztráció: epikus fény, jelmez, koherens világ.",
    ),
    "Fashion_editorial": (
        "Divat (editorial)",
        "Fashion editorial",
        "Magazin stílusú divatfotó: stúdiófény, póz, prémium hangulat.",
    ),
    "Film_noir": (
        "Film noir",
        "Film noir",
        "Drámai árnyékok, magas kontraszt, klasszikus mozi hangulat.",
    ),
    "Food_photography": (
        "Étel fotó",
        "Food photography",
        "Ételfotó: étvágygerjesztő textúrák, makró, tiszta kompozíció.",
    ),
    "Fotorealisztikus": (
        "Fotórealisztikus",
        "Photorealistic",
        "Valósághű bőr és fény, természetes textúra, profi fotó jelleg.",
    ),
    "Ink_comic": (
        "Tus / képregény",
        "Ink comic",
        "Vastag tusvonalak, képregényes kompozíció, erős sziluett.",
    ),
    "Isometric": (
        "Izometrikus",
        "Isometric",
        "Tisztán geometrikus nézet, miniatűr jelenet, mélységrétegek.",
    ),
    "Landscape": (
        "Tájkép",
        "Landscape",
        "Tájkép: légperspektíva, természetes fény, széles kép.",
    ),
    "Manga_fekete_feher": (
        "Manga (fekete-fehér)",
        "Manga (black & white)",
        "Fekete-fehér manga: screentone, kontrasztos tus.",
    ),
    "Minimal_flat": (
        "Minimal / flat",
        "Minimal flat",
        "Lapos dizájn, kevés forma, limitált színpaletta, letisztult.",
    ),
    "nsfw": (
        "Felnőtt (NSFW)",
        "Adult (NSFW)",
        "Felnőtt tartalom — külön modell; csak explicit kérésre válaszd (Pipe szabály).",
    ),
    "Pixel_art": (
        "Pixel art",
        "Pixel art",
        "Retro pixel, éles pixelek, korlátozott paletta, játékos esztétika.",
    ),
    "Portrait": (
        "Portré",
        "Portrait",
        "Fej–váll portré: szemek, arckifejezés, természetes bőr.",
    ),
    "Sci-Fi": (
        "Sci-fi",
        "Sci-fi",
        "Sci-fi környezet, technológia, mozis fény, koherens lépték.",
    ),
    "Termek_foto": (
        "Termékfotó",
        "Product photo",
        "Stúdiós termékfotó: tiszta háttér, pontos anyag és árnyék.",
    ),
    "Vazlat_ceruza": (
        "Vázlat (ceruza)",
        "Pencil sketch",
        "Ceruza vázlat: hálózás, szerkesztő vonalak, hagyományos média.",
    ),
    "Vintage_film": (
        "Vintage film",
        "Vintage film",
        "Régi filmkocka hangulat: szemcse, meleg tónusok, analóg érzet.",
    ),
    "Vizfestek": (
        "Vízfesték",
        "Watercolor",
        "Akvarell: lágy szélek, papír textúra, finom mosások.",
    ),
}


def _wizard_style_fallback_row(key: str) -> tuple[str, str, str]:
    """Ismeretlen preset kulcs: olvasható név + rövid magyarázat."""
    pretty = re.sub(r"[_\-]+", " ", key).strip()
    if not pretty:
        pretty = key
    return (
        pretty,
        pretty,
        "Egyéni vagy bővített preset — a Pipe STYLE_PRESETS_JSON / beállítások szerint.",
    )


def format_wizard_style_preset_table_markdown(keys: list[str]) -> str:
    """Markdown táblázat a varázsló system prompt `{{STYLE_PRESET_LIST}}` helyére.

    3 oszlop (Kulcs | HU/EN | leírás): rövidebb sorok → kevesebb token, kisebb esély a modell
    válaszának félbeszakadására; az LLM kevésbé „fordítja le” listává, ha a prompt tiltja.
    """
    if not keys:
        return (
            "(Nincs érvényes preset — nézd meg a Valves **STYLE_PRESETS_JSON** mezőt; "
            "üres `{}` esetén a Pipe beágyazott listája kellene betöltődjön.)"
        )
    header = (
        "| **Kulcs** (`style_label`) | **HU / EN** | **Leírás** |\n"
        "|---|---|---|"
    )
    rows: list[str] = []
    for k in keys:
        hu, en, desc = _WIZARD_STYLE_PRESET_I18N.get(k, _wizard_style_fallback_row(k))
        rows.append(
            "| "
            + " | ".join(
                [
                    _wizard_style_esc_cell(f"`{k}`"),
                    _wizard_style_esc_cell(f"{hu} / {en}"),
                    _wizard_style_esc_cell(desc),
                ]
            )
            + " |"
        )
    intro = (
        "**Válaszban másold ki változtatás nélkül** (markdown táblázat maradjon, ne legyen belőle felsorolás). "
        "A JSON `style_label` = első oszlop kulcsa, **pontosan** (aláhúzás, nagybetű).\n\n"
    )
    return intro + header + "\n" + "\n".join(rows)


# Varázsló `{{WIZARD_SIZE_TABLE}}`: képarány szerint rendezve; small / normal / large (minden szám 64 többszörös).
# (w, h) párok: a Pipe `_validate_size_or_error` elvárásának megfelelően.
_WIZARD_SIZE_TABLE_ROWS: list[tuple[str, str, tuple[int, int], tuple[int, int], tuple[int, int], str]] = [
    # ar,   magyar címke,     small,      normal,       large,        rövid megjegyzés
    ("1:2", "Extrém álló (magas)", (512, 1024), (768, 1536), (1024, 2048), "Telefonos „story”, poszter álló"),
    ("2:3", "Klasszikus álló", (512, 768), (768, 1152), (1024, 1536), "Portré, könyvborító"),
    ("3:4", "Álló (közeli négyzetes)", (576, 768), (960, 1280), (1536, 2048), "Instagram álló, print (64 px rács, tiszta 3:4)"),
    ("4:5", "Álló (közepesen magas)", (512, 640), (768, 960), (1024, 1280), "Közösségi álló, keret"),
    ("1:1", "Négyzetes", (512, 512), (768, 768), (1024, 1024), "Avatar, ikon, feed"),
    ("5:4", "Enyhén fekvő", (640, 512), (960, 768), (1280, 1024), "Klasszikus fénykép arány"),
    ("4:3", "Standard monitor / print", (768, 576), (1024, 768), (1280, 960), "Prezentáció, régi TV"),
    ("3:2", "DSLR / klasszikus fekvő", (768, 512), (1152, 768), (1536, 1024), "Fotó, lapozós kép"),
    ("2:1", "Széles panoráma (alacsony)", (1024, 512), (1536, 768), (2048, 1024), "Banner, header"),
    ("16:9", "Szélesvásznú HD", (1024, 576), (2048, 1152), (3072, 1728), "Filmkeret, monitor (64 px rács, tiszta 16:9)"),
    ("9:16", "Függőleges videó", (576, 1024), (1152, 2048), (1728, 3072), "Shorts / Reels / TikTok (64 px rács)"),
]


def format_wizard_size_table_markdown() -> str:
    """Markdown táblázat a varázsló `{{WIZARD_SIZE_TABLE}}` helyére — képarány × small/normal/large."""
    header = (
        "| **Képarány** | **Small** | **Normal** | **Large** | **Megjegyzés (HU)** |\n"
        "|---|---:|---:|---:|---|"
    )
    rows: list[str] = []
    for ar, _label, sm, md, lg, note in _WIZARD_SIZE_TABLE_ROWS:
        sw, sh = sm
        nw, nh = md
        lw, lh = lg
        rows.append(
            "| "
            + " | ".join(
                [
                    _wizard_style_esc_cell(f"**{ar}**"),
                    _wizard_style_esc_cell(f"`{sw}×{sh}`"),
                    _wizard_style_esc_cell(f"`{nw}×{nh}`"),
                    _wizard_style_esc_cell(f"`{lw}×{lh}`"),
                    _wizard_style_esc_cell(note),
                ]
            )
            + " |"
        )
    intro = (
        "A **small / normal / large** oszlopok **szélesség×magasság** pixelben (mind **64** többszörös). "
        "A JSON-ban a választott párost add meg `width` és `height` mezőkben. "
        "Saját méret is lehet, ha mindkét oldal **64** többszöröse.\n\n"
    )
    return intro + header + "\n" + "\n".join(rows)


# Utasítás-szöveg levágása
_PREFIX_RES = (
    re.compile(
        r"^\s*(gener[aá]lj|k[eé]sz[ií]ts|rajzolj|mutass)(\s+egy)?\s+(k[eé]pet|k[eé]pnek)\s*([:\-–]\s*)?",
        re.IGNORECASE | re.UNICODE,
    ),
    re.compile(
        r"^\s*(generate|create|draw)(\s+an?\s+|\s+)(image|picture)\s*([:\-–]\s*)?",
        re.IGNORECASE,
    ),
)


def _normalize_prompt_for_image(text: str) -> str:
    t = text.strip()
    if not t:
        return t
    orig = t
    for rx in _PREFIX_RES:
        t = rx.sub("", t, count=1)
    t = t.strip()
    return t if t else orig


def _merge_style_into_prompt_core(style: str, theme: str, prompt_core: str) -> str:
    """
    style_label / téma hozzáfűzése — ne ismételje a stílus nevet, ha a prompt már ezzel kezdődik
    (pl. JSON: style_label Anime + prompt „Anime …” → ne legyen „Anime\\n\\nAnime …”).
    """
    pc = (prompt_core or "").strip()
    head: list[str] = []
    if style:
        s = style.strip()
        if s:
            pl = pc.lower()
            sl = s.lower()
            if not (pl.startswith(sl) or pl.startswith(sl + " ") or pl.startswith(sl + ",")):
                head.append(s)
    if theme:
        t = (theme or "").strip()
        if t:
            head.append(t)
    if not head:
        return pc
    return "\n\n".join(head + [pc]) if pc else "\n\n".join(head)


_PHOTO_REAL_HINTS = re.compile(
    r"(?i)(photorealistic|photo-real|realistic skin|dslr|shallow depth|documentary|raw photo|professional photography|imax\s+quality|8k\s*resolution|\b8k\b)"
)


def _user_wants_photorealistic(text: str) -> bool:
    """A pozitív prompt fotó / realisztikus képet kér (nem anime illusztrációt)."""
    return bool(_PHOTO_REAL_HINTS.search(text or ""))


# Egyetlen „beszélgetés + kép” bejegyzés a modellválasztóban (nem konkrét .ckpt).
PIPE_DEFAULT_MODEL_SENTINEL = "open_webui_pipe.drawthings_default"

# Alap upscaler: Draw Things / közösségi „Universal” ESRGAN — jó kompromisszum minőség / sebesség; 2× scale a Valves-ban.
_DEFAULT_UPSCALER_CKPT = "esrgan_4x_universal_upscaler_v2_sharp_f16.ckpt"


def _resolve_ckpt_model(body: dict, fallback: str) -> str:
    """
    Open WebUI: `open_webui_pipe.z_image_turbo_1.0_q8p.ckpt` — csak a valódi fájlnév kell.
    """
    mid = (body.get("model") or "").strip()
    if not mid:
        return fallback
    if PIPE_DEFAULT_MODEL_SENTINEL in mid or mid.rstrip("/").endswith("drawthings_default"):
        return fallback
    segs = mid.split(".")
    if len(segs) >= 3 and segs[-1] == "ckpt":
        first = segs[0].lower()
        if first in ("open_webui_pipe", "pipe") or first.startswith("open_webui"):
            return ".".join(segs[1:])
    m = re.search(r"(?:^|[/.])([a-zA-Z0-9_][a-zA-Z0-9_.-]*\.ckpt)$", mid)
    if m:
        return m.group(1)
    if mid.endswith(".ckpt"):
        return mid.split("/")[-1].split("\\")[-1]
    return fallback


def _compose_prompt(
    user_prompt: str,
    valves: Any,
    preset: dict[str, Any] | None = None,
) -> str:
    """Preset előtag/utótag (ha van), majd Valves STYLE_PREFIX/SUFFIX + user üzenet."""
    u = user_prompt.strip()
    ppre = (preset.get("style_prefix") or preset.get("prompt_prefix") or "").strip() if preset else ""
    psuf = (preset.get("style_suffix") or preset.get("prompt_suffix") or "").strip() if preset else ""
    pre = (getattr(valves, "STYLE_PREFIX", None) or "").strip()
    suf = (getattr(valves, "STYLE_SUFFIX", None) or "").strip()
    parts: list[str] = []
    for x in (ppre, pre):
        if x:
            parts.append(x)
    if u:
        parts.append(u)
    for x in (suf, psuf):
        if x:
            parts.append(x)
    if not parts:
        return u
    return "\n\n".join(parts) if len(parts) > 1 else (parts[0] if parts else u)


def _normalize_style_preset_key(s: str) -> str:
    """
    Preset kulcs összehasonlítás: szóköz és aláhúzás egyenértékű.
    A varázsló listában a kulcs és az olvasható név — a `style_label` szóközzel vagy aláhúzással is egyezhet.
    """
    t = (s or "").strip().lower()
    if not t:
        return ""
    return "_".join(t.replace("_", " ").split())


# Gyakori elírás a varázsló JSON-ban (LLM / kézi): „nfsw” → nsfw preset
_STYLE_LABEL_ALIASES: dict[str, str] = {
    "nfsw": "nsfw",
}

_DEFAULT_NSFW_MODEL = "zimageturbonsfw_45bf16diffusion_f16.ckpt"


def _match_style_preset(
    presets: dict[str, Any],
    style: str,
    theme: str,
    prompt_core: str,
) -> tuple[str | None, dict[str, Any]]:
    """
    Kulcs → dict preset (model, cfg, steps, negative_prompt, config_json, …).
    1) Pont egyezés: style_label normalizálva == kulcs normalizálva.
    2) Kulcsszó: a kulcs része a „stílus + téma + prompt” szövegnek (mint NEGATIVE_BY_STYLE).
    """
    if not presets:
        return None, {}
    st_n = _normalize_style_preset_key(style or "")
    st_n = _STYLE_LABEL_ALIASES.get(st_n, st_n)
    if st_n:
        for k, v in presets.items():
            if not isinstance(v, dict):
                continue
            if _normalize_style_preset_key(str(k)) == st_n:
                return str(k), v
    blob = f"{style} {theme} {prompt_core}".lower()
    for k, v in presets.items():
        if not isinstance(v, dict):
            continue
        ks = str(k).lower().strip()
        if len(ks) < 2:
            continue
        if ks in blob:
            return str(k), v
        ks_spaced = ks.replace("_", " ")
        if len(ks_spaced.replace(" ", "")) >= 2 and ks_spaced in blob:
            return str(k), v
    return None, {}


def _is_nsfw_intent(
    valves: Any,
    *,
    style: str,
    theme: str,
    prompt_core: str,
    extra_neg: str = "",
) -> bool:
    """
    NSFW szándék felismerése (stílus + téma + prompt + negatív), hogy stílus-specifikus
    NSFW preset/model automatikusan bekapcsolhasson.
    """
    sk = _normalize_style_preset_key(style or "")
    if sk in ("nsfw", "nfsw") or sk.endswith("_nsfw"):
        return True
    # Opcionális: csak explicit promptszövegből döntsünk, ne a negatívból/témából.
    prompt_only = bool(getattr(valves, "NSFW_PROMPT_ONLY", True))
    rx = (getattr(valves, "NSFW_INTENT_REGEX", None) or "").strip()
    if not rx:
        return False
    try:
        cre = re.compile(rx, re.IGNORECASE | re.UNICODE)
    except re.error:
        return False
    if prompt_only:
        blob = (prompt_core or "").strip()
    else:
        blob = " ".join(
            x for x in (style or "", theme or "", prompt_core or "", extra_neg or "") if x
        )
    return bool(cre.search(blob))


def _pick_style_specific_nsfw_preset(
    presets: dict[str, Any],
    *,
    preset_key: str | None,
    style: str,
) -> tuple[str | None, dict[str, Any]]:
    """
    Ha van `Fantasy_nsfw`, `Anime_nsfw` stb. preset, azt használjuk NSFW kérésnél.
    Sorrend: találat alap presetből -> találat style_label-ből.
    """
    if not presets:
        return None, {}
    candidates: list[str] = []
    if preset_key:
        candidates.append(f"{preset_key}_nsfw")
    if style:
        candidates.append(f"{style}_nsfw")
    if _normalize_style_preset_key(style) in ("nsfw", "nfsw"):
        candidates.append("nsfw")
    for cand in candidates:
        cn = _normalize_style_preset_key(cand)
        for k, v in presets.items():
            if not isinstance(v, dict):
                continue
            if _normalize_style_preset_key(str(k)) == cn:
                return str(k), v
    return None, {}


def _apply_nsfw_model_override(
    valves: Any,
    *,
    current_model: str,
) -> str:
    """
    NSFW kérésnél modell-felülírás:
    1) NSFW_MODEL_DEFAULT,
    2) különben marad az aktuális.
    (Nem stílusfüggő modellválasztás.)
    """
    d = (getattr(valves, "NSFW_MODEL_DEFAULT", None) or "").strip()
    if d:
        return d if d.endswith(".ckpt") else f"{d}.ckpt"
    return current_model


def _stream_connection_error_hint(base_url: str, exc: BaseException) -> str:
    """httpx / SSE: All connection attempts failed — tipikus BRIDGE_URL / bridge nem fut."""
    s = str(exc).lower()
    if not any(
        x in s
        for x in (
            "connection",
            "failed",
            "refused",
            "unreachable",
            "timed out",
            "timeout",
            "name or service not known",
            "nodename nor servname",
        )
    ):
        return ""
    u = (base_url or "").rstrip("/")
    return (
        "\n\n---\n\n"
        "**Mi ez?** Az Open WebUI szerver **nem éri el** a drawthings_bridge-et.\n\n"
        f"- **BRIDGE_URL** most: `{u}` — fut-e a bridge? Teszt a **szerveren** (ahol az OWUI): `curl -sS {u}/health`\n"
        "- Ha az OWUI **Dockerben / más gépen** van, a `127.0.0.1` **nem** a Mac — állítsd: `http://<Mac_LAN_IP>:8787`\n"
        "- Ha csak a **stream** (SSE) bukik: próbáld **STREAM_PROGRESS** = **hamis** (egy sima `/generate` ugyanarra az URL-re).\n"
    )


def _apply_preset_model(preset: dict[str, Any], body: dict, fallback: str) -> str:
    """Preset `model`: .ckpt fájlnév; felülírja a választóban lévő modellt."""
    pm = (preset.get("model") or "").strip()
    if not pm:
        return _resolve_ckpt_model(body, fallback)
    mid = pm if pm.endswith(".ckpt") else f"{pm}.ckpt"
    return _resolve_ckpt_model({"model": f"open_webui_pipe.{mid}"}, mid)


def _optional_config_json(valves: Any) -> dict[str, Any] | None:
    raw = (getattr(valves, "CONFIG_JSON", None) or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


_NON_ASCII = re.compile(r"[^\x00-\x7F]")


def _translation_stack_available() -> bool:
    try:
        import langdetect  # noqa: F401
        from deep_translator import GoogleTranslator  # noqa: F401

        return True
    except ImportError:
        return False


def _ensure_english(text: str, enabled: bool) -> tuple[str, bool]:
    """
    Nem angol / bizonytalan nyelv → GoogleTranslator (auto→en).
    A langdetect egy szavas / elgépelős szöveget tévesen „en”-nek jelölhet — detect_langs + küszöb.
    """
    if not enabled or not (text or "").strip():
        return text, False
    if not _translation_stack_available():
        return text, False
    t = text.strip()
    if len(t) < 2:
        return text, False
    try:
        from langdetect import detect_langs

        langs = detect_langs(t)
        if langs:
            top = langs[0]
            if top.lang == "en" and top.prob >= 0.82:
                return text, False
    except Exception:
        pass
    try:
        from deep_translator import GoogleTranslator

        out = GoogleTranslator(source="auto", target="en").translate(t)
        if out and isinstance(out, str) and out.strip():
            out = out.strip()
            if out.casefold() == t.casefold():
                return text, False
            return out, True
    except Exception:
        pass
    return text, False


def _load_json_map(raw: str) -> dict[str, Any]:
    s = (raw or "").strip()
    if not s:
        return {}
    try:
        o = json.loads(s)
        return o if isinstance(o, dict) else {}
    except json.JSONDecodeError:
        return {}


def _deep_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# Draw Things / JSGenerationConfiguration — wiki.drawthings.ai Sampler_Basics (0–18)
_SAMPLER_NAMES: dict[int, str] = {
    0: "DPM++ 2M Karras",
    1: "Euler A",
    2: "DDIM",
    3: "UniPC",
    4: "DPM++ SDE Karras",
    5: "PLMS",
    6: "LCM",
    7: "Euler A Substep",
    8: "DPM++ SDE Substep",
    9: "TCD",
    10: "Euler A Trailing",
    11: "DPM++ SDE Trailing",
    12: "DPM++ 2M AYS",
    13: "Euler A AYS",
    14: "DPM++ SDE AYS",
    15: "DPM++ 2M Trailing",
    16: "DDIM Trailing",
    17: "UniPC Trailing",
    18: "UniPC AYS",
}


def _clamp_steps_for_z_image_pipeline(
    valves: Any,
    model: str,
    steps_val: int | None,
) -> int | None:
    """
    z_image + refiner/hires/upscaler lánc alacsony lépésnél hibára futhat (CLI: „no tensors returned”).
    Ha nincs explicit globális **STEPS** a Valves-ban, ilyenkor a lépésszám legalább Z_IMAGE_MIN_STEPS.
    Egyszerű (csak UniPC) módnál nem kényszerítünk — a stílus preset lépése érvényesül.
    """
    if steps_val is None:
        return None
    if "z_image" not in (model or "").lower():
        return steps_val
    if not getattr(valves, "Z_IMAGE_PIPELINE_DEFAULTS", False):
        return steps_val
    if getattr(valves, "STEPS", None) is not None:
        return steps_val
    risky = bool(getattr(valves, "Z_IMAGE_REFINER_HIRES", False)) or bool(
        (getattr(valves, "UPSCALER_CKPT", None) or "").strip()
    )
    if not risky:
        return steps_val
    raw = getattr(valves, "Z_IMAGE_MIN_STEPS", None)
    if raw is None:
        min_s = 12
    else:
        try:
            min_s = int(float(raw))
        except (TypeError, ValueError):
            min_s = 12
    if min_s < 1:
        return steps_val
    return max(int(steps_val), min_s)


def _cap_steps_global_max(valves: Any, steps_val: int | None) -> int | None:
    """Globális lépésszám plafon (alap: 22)."""
    if steps_val is None:
        return None
    try:
        mx = int(getattr(valves, "MAX_STEPS", 22) or 22)
    except (TypeError, ValueError):
        mx = 22
    if mx < 1:
        mx = 1
    return min(int(steps_val), mx)


def _cap_cfg_for_z_image(
    valves: Any,
    model: str,
    cfg_val: float | None,
) -> float | None:
    """
    z_image / zimageturbo checkpointoknál a CFG értéket **0.8–1.2** (alap) tartományba húzzuk,
    ha Z_IMAGE_CFG_AUTO_CAP be van kapcsolva (alap: igen).
    """
    if cfg_val is None:
        return None
    if not getattr(valves, "Z_IMAGE_CFG_AUTO_CAP", True):
        return cfg_val
    if not _looks_like_z_image_model(model):
        return cfg_val
    try:
        lo = float(getattr(valves, "Z_IMAGE_CFG_MIN", 0.8) or 0.8)
    except (TypeError, ValueError):
        lo = 0.8
    try:
        hi = float(getattr(valves, "Z_IMAGE_CFG_MAX", 1.2) or 1.2)
    except (TypeError, ValueError):
        hi = 1.2
    if lo > hi:
        lo, hi = hi, lo
    if lo < 0.1:
        lo = 0.1
    if hi < lo:
        hi = lo
    c = float(cfg_val)
    return min(max(c, lo), hi)


def _looks_like_z_image_model(name: str) -> bool:
    n = (name or "").strip().lower()
    return ("z_image" in n) or ("zimageturbo" in n)


def _is_photoreal_style_key(style_key: str) -> bool:
    k = _normalize_style_preset_key(style_key or "")
    return k in (
        "fotorealisztikus",
        "ultra_realisztikus",
        "ultra_real",
        "ultrareal",
        "photorealistic",
        "photo_real",
        "termek_foto",
        "fashion_editorial",
        "food_photography",
        "architectural_render",
    )


def _skip_z_image_preset_tuning_for_entry(preset_key: str, model_name: str) -> bool:
    """
    A z_image turbo tuning (6–9 lépés, alacsony CFG plafon) a **nsfw** checkpointra is ráért,
    mert a fájlnév tartalmazza a `zimageturbo` részstringet — így a preset `steps: 20` 9-re zsugorodott,
    ami összefolyó / olvadó anatómiát okoz bonyolult pózoknál. NSFW preset / NSFW .ckpt: ne nyomjuk turbo tartományba.
    """
    k = _normalize_style_preset_key(preset_key or "")
    if k in ("nsfw", "nfsw"):
        return True
    pm = (model_name or "").lower()
    if "nsfw" in pm:
        return True
    return False


def _normalize_style_presets_for_z_image(
    valves: Any, presets: dict[str, Any]
) -> dict[str, Any]:
    """
    Beágyazott/stílus presetek finomhangolása z_image modellekhez:
    - steps tartomány: Z_IMAGE_PRESET_STEPS_MIN..MAX (alap 6..9, default 8)
    - cfg plafon: Z_IMAGE_PRESET_CFG_MAX (alap 1.2), default: Z_IMAGE_PRESET_CFG_DEFAULT (alap 1.0)
    """
    if not isinstance(presets, dict):
        return {}
    if not bool(getattr(valves, "Z_IMAGE_PRESET_TUNING", True)):
        return presets
    try:
        smin = int(getattr(valves, "Z_IMAGE_PRESET_STEPS_MIN", 6) or 6)
        smax = int(getattr(valves, "Z_IMAGE_PRESET_STEPS_MAX", 9) or 9)
        sdef = int(getattr(valves, "Z_IMAGE_PRESET_STEPS_DEFAULT", 8) or 8)
        psmin = int(getattr(valves, "Z_IMAGE_PRESET_STEPS_MIN_PHOTO", 8) or 8)
        psmax = int(getattr(valves, "Z_IMAGE_PRESET_STEPS_MAX_PHOTO", 18) or 18)
        psdef = int(getattr(valves, "Z_IMAGE_PRESET_STEPS_DEFAULT_PHOTO", 12) or 12)
    except (TypeError, ValueError):
        smin, smax, sdef = 6, 9, 8
        psmin, psmax, psdef = 8, 18, 12
    if smin < 1:
        smin = 1
    if smax < smin:
        smax = smin
    if sdef < smin or sdef > smax:
        sdef = min(max(sdef, smin), smax)
    if psmin < 1:
        psmin = 1
    if psmax < psmin:
        psmax = psmin
    if psdef < psmin or psdef > psmax:
        psdef = min(max(psdef, psmin), psmax)
    try:
        cfg_def = float(getattr(valves, "Z_IMAGE_PRESET_CFG_DEFAULT", 1.0) or 1.0)
        cfg_max = float(getattr(valves, "Z_IMAGE_PRESET_CFG_MAX", 1.2) or 1.2)
    except (TypeError, ValueError):
        cfg_def, cfg_max = 1.0, 1.2
    if cfg_max < 0.1:
        cfg_max = 0.1
    out: dict[str, Any] = {}
    for k, v in presets.items():
        if not isinstance(v, dict):
            out[k] = v
            continue
        d = dict(v)
        pm = str(d.get("model") or "")
        if _looks_like_z_image_model(pm) and not _skip_z_image_preset_tuning_for_entry(
            str(k), pm
        ):
            use_photo_range = _is_photoreal_style_key(str(k))
            lo = psmin if use_photo_range else smin
            hi = psmax if use_photo_range else smax
            df = psdef if use_photo_range else sdef
            sv = d.get("steps")
            if isinstance(sv, (int, float, str)):
                try:
                    s = int(float(sv))
                except (TypeError, ValueError):
                    s = df
            else:
                s = df
            d["steps"] = max(lo, min(hi, s))
            cv = d.get("cfg")
            if isinstance(cv, (int, float, str)):
                try:
                    c = float(cv)
                except (TypeError, ValueError):
                    c = cfg_def
            else:
                c = cfg_def
            if c > cfg_max:
                c = cfg_max
            d["cfg"] = c
        out[k] = d
    return out


def _merge_upscaler_config(valves: Any, cfg_extra: dict[str, Any]) -> dict[str, Any]:
    """UPSCALER_CKPT beállítva → `config_json`-ba (2× alapból), **Z_IMAGE_PIPELINE_DEFAULTS nélkül is**."""
    upsc = (getattr(valves, "UPSCALER_CKPT", None) or "").strip()
    if not upsc:
        return cfg_extra
    sf = float(getattr(valves, "UPSCALER_SCALE_FACTOR", None) or 2.0)
    return _deep_merge(
        cfg_extra,
        {"upscaler": upsc, "upscalerScaleFactor": sf},
    )


def _apply_z_image_pipeline_defaults(
    valves: Any,
    model: str,
    cfg_extra: dict[str, Any],
) -> dict[str, Any]:
    """
    Ha **Z_IMAGE_PIPELINE_DEFAULTS** be van kapcsolva: **UniPC Trailing** (`sampler`: 17) + opcionális
    refiner/hires/upscaler. **Alapból ki** — így a Pipe ugyanúgy küld, mint a `draw-things-cli generate`
    **--config-json nélkül** (a CLI alap sampler / pipeline), ami a z_image **turbo** modellnél gyakran szebb.
    A preset / CONFIG_JSON mezők felülírhatják (deep merge: user erősebb).
    **Upscaler** külön: ha **UPSCALER_CKPT** meg van adva, mindig merge (pipeline ki mellett is).
    """
    if not getattr(valves, "Z_IMAGE_PIPELINE_DEFAULTS", False):
        return _merge_upscaler_config(valves, cfg_extra)
    m = (model or "").lower()
    if "z_image" not in m:
        return _merge_upscaler_config(valves, cfg_extra)
    defaults: dict[str, Any] = {
        "sampler": 17,
    }
    if getattr(valves, "Z_IMAGE_REFINER_HIRES", False):
        rs = getattr(valves, "REFINER_START", None)
        refiner_start = 0.75 if rs is None else float(rs)
        defaults = {
            **defaults,
            "refinerModel": model,
            "refinerStart": refiner_start,
            "hiresFix": True,
        }
    out = _deep_merge(defaults, cfg_extra)
    return _merge_upscaler_config(valves, out)


def _extract_user_content(m: dict[str, Any]) -> str:
    c = m.get("content")
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        parts: list[str] = []
        for block in c:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
        return "".join(parts).strip()
    return ""


def _last_user_text(body: dict) -> str:
    messages = body.get("messages") or []
    for m in reversed(messages):
        if m.get("role") == "user":
            return _extract_user_content(m)
    return ""


def _iter_user_messages_chronological(body: dict) -> list[str]:
    out: list[str] = []
    for m in body.get("messages") or []:
        if m.get("role") == "user":
            t = _extract_user_content(m)
            if t:
                out.append(t)
    return out


def _all_user_text_for_intent(body: dict) -> str:
    """Összes user üzenet egy szövegben — képkérést korábbi üzenetben is észleljük."""
    return "\n".join(_iter_user_messages_chronological(body))


def _normalize_for_image_intent_match(s: str) -> str:
    """
    Billentyűzet / IME eltérések (pl. német „generälj”, „kepet” ékezet nélkül),
    hogy ne essen el az IMAGE_REQUEST_REGEX találat.
    """
    if not s:
        return s
    t = re.sub(r"generälj", "generálj", s, flags=re.IGNORECASE)
    t = re.sub(r"generäld", "generáld", t, flags=re.IGNORECASE)
    t = re.sub(r"(?i)\bkeszits\b", "készíts", t)
    t = re.sub(r"(?i)\bkeszitsd\b", "készítsd", t)
    t = re.sub(r"(?i)\bkepet\b", "képet", t)
    t = re.sub(r"(?i)\bkepnek\b", "képnek", t)
    t = re.sub(r"(?i)\billusztracio\b", "illusztráció", t)
    return t


def _ascii_fold_hu(s: str) -> str:
    """Ékezetmentes + kisbetűs + egyszerűsített szóköz/írásjel forma."""
    if not s:
        return ""
    t = unicodedata.normalize("NFKD", s)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(t.split())


def _fuzzy_image_intent_ok(text: str) -> bool:
    """
    Regex fallback: nagyon laza magyar/angol képszándék-felismerés
    (ékezet nélkül, elütésbarát szógyökök).
    """
    a = _ascii_fold_hu(text)
    if not a:
        return False
    # Magyar + angol igék / főnevek
    verb_rx = re.compile(
        r"\b(general|generate|create|draw|paint|render|"
        r"generalj|generald|keszits|keszitsd|rajzolj|mutass|"
        r"alkoss|renderelj|fess|illusztralj)\b"
    )
    image_rx = re.compile(
        r"\b(kep|kepet|kepnek|image|picture|drawing|illustration|illusztracio)\b"
    )
    # Tipikus minimál-parancsok (pl. "generálj képet", "keszits kepet", "draw image")
    if verb_rx.search(a) and image_rx.search(a):
        return True
    # Szóló képkérések: "képet kérek", "kepet akarok", "show image"
    request_rx = re.compile(
        r"\b(kepet|kep)\s+(kerek|akarok|legyen|mutass)|\b(show|make)\s+(an?\s+)?(image|picture)\b"
    )
    return bool(request_rx.search(a))


_WIZARD_EMPTY_LLM_REPLY_HU = (
    "**Üres válasz érkezett a modelltől.** "
    "Gyakori ok: beépített tartalmi szűrő (pl. NSFW), vagy a modell nem adott szöveget. "
    "Próbálj más modellt / LM Studio beállítást; felnőtt témához a preset listában a **nsfw** kulcsot add meg a varázsló JSON `style_label` mezőben. "
    "Ha ismétlődik: nézd a LM Studio / Ollama konzolt."
)


def _explicit_image_intent_ok(
    valves: Any,
    body: dict,
    raw_text: str,
    json_ready: bool,
    tre: Any,
) -> bool:
    """
    Ha REQUIRE_EXPLICIT_IMAGE_REQUEST: csak akkor engedélyezett a közvetlen generálás,
    ha van explicit képkérés (regex), vagy már van JSON / trigger szó.
    """
    if not getattr(valves, "REQUIRE_EXPLICIT_IMAGE_REQUEST", True):
        return True
    if json_ready:
        return True
    if tre and raw_text and tre.search(raw_text):
        return True
    rx = (getattr(valves, "IMAGE_REQUEST_REGEX", None) or "").strip()
    if not rx:
        return True
    try:
        cre = re.compile(rx, re.IGNORECASE | re.UNICODE)
    except re.error:
        return True
    blob = _normalize_for_image_intent_match(
        (_all_user_text_for_intent(body) or raw_text or "").strip()
    )
    if cre.search(blob):
        return True
    # Fallback a nagyon elgépelős / ékezet nélküli variációkra
    return _fuzzy_image_intent_ok(blob)


def _wizard_entry_allowed(
    valves: Any,
    body: dict,
    raw_text: str,
    json_ready: bool,
    tre: Any,
) -> bool:
    """
    REQUIRE_EXPLICIT_IMAGE_REQUEST esetén a varázsló LLM csak akkor fusson, ha ugyanaz a feltétel
    teljesül, mint a közvetlen generálásnál: explicit képkérés **bármely** user üzenetben a szálban
    (`IMAGE_REQUEST_REGEX` + `_all_user_text_for_intent`), vagy trigger a legutóbbi üzeneten, vagy json_ready.

    Nem elég „már volt assistant válasz”: különben egy általános chat után a második user üzenet
    véletlenül a kép-varázsló system promptját kapná.
    """
    if not getattr(valves, "REQUIRE_EXPLICIT_IMAGE_REQUEST", True):
        return True
    return _explicit_image_intent_ok(valves, body, raw_text, json_ready, tre)


def _merged_user_text_for_parse(
    body: dict,
    trigger_regex: str,
    merge_short: bool,
) -> str:
    """
    Ha csak „KÉSZ MEHET” jön egy rövid üzenetben, fűzd hozzá az előző user szövegeket
    (hogy a Gemma-blokk az előző üzenetben maradhasson).
    """
    users = _iter_user_messages_chronological(body)
    if not users:
        return ""
    last = users[-1]
    if not merge_short or not trigger_regex.strip():
        return last
    try:
        tre = re.compile(trigger_regex, re.IGNORECASE | re.UNICODE)
    except re.error:
        return last
    if not tre.search(last):
        return last
    stripped = tre.sub("", last).strip()
    if len(stripped) >= 48:
        return last
    prev = users[:-1]
    if not prev:
        return last
    return "\n\n---\n\n".join(prev + [last])


def _parse_size(s: str) -> tuple[int | None, int | None]:
    m = re.fullmatch(r"\s*(\d+)\s*[x×]\s*(\d+)\s*", s or "", re.IGNORECASE)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _resolve_dim(val: Any, fallback: int | None) -> int | None:
    if val is None:
        return fallback
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str) and val.strip().isdigit():
        return int(val.strip())
    return fallback


def _resolve_optional_int(val: Any, fallback: int | None) -> int | None:
    if val is None:
        return fallback
    if isinstance(val, bool):
        return fallback
    if isinstance(val, (int, float)):
        return int(val)
    if isinstance(val, str) and val.strip():
        s = val.strip()
        if s.lstrip("-").isdigit():
            return int(s)
    return fallback


def _resolve_optional_float(val: Any, fallback: float | None) -> float | None:
    if val is None:
        return fallback
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str) and val.strip():
        s = val.strip().replace(",", ".")
        try:
            return float(s)
        except ValueError:
            pass
    return fallback


def _validate_size_or_error(
    *,
    bundle: dict[str, Any],
    width: int | None,
    height: int | None,
) -> str | None:
    """
    Méret-ellenőrzés user inputra:
    - `size` mező hibás formátum: legyen egyértelmű hiba
    - width/height csak pozitív 64 többszöröse
    """
    raw_size = bundle.get("size")
    if isinstance(raw_size, str) and raw_size.strip():
        pw, ph = _parse_size(raw_size)
        if pw is None or ph is None:
            return (
                "Hibás méret formátum. Add meg így: **`szélesség×magasság`** (pl. `1024x1024` vagy `896×1152`)."
            )
    # Ha explicit width/height szöveg mezőbe betű kerül, jelezzünk (ne essen vissza csendben presetre/defaultra).
    for key in ("width", "height"):
        rv = bundle.get(key)
        if isinstance(rv, str) and rv.strip():
            if not rv.strip().isdigit():
                return (
                    f"Hibás `{key}` érték: csak szám lehet (betű nélkül), pl. "
                    "`width: 1152`, `height: 896`."
                )
    # Ha nincs explicit size és width/height sincs, maradhat alapértelmezett.
    if width is None and height is None:
        return None
    if width is None or height is None:
        return "A mérethez mindkettő kell: **width** és **height** (pl. `1152×896`)."
    if width < 64 or height < 64:
        return "A méret túl kicsi. Minimum **64×64**."
    if (width % 64) != 0 or (height % 64) != 0:
        return (
            "A width/height legyen **64 többszöröse**. Példák: "
            "`512×512`, `768×768`, `1024×1024`, `896×1152`, `1152×896`."
        )
    return None


def _strip_none_payload(d: dict[str, Any]) -> dict[str, Any]:
    """A bridge / CLI ne kapjon explicit `null` lépés/CFG-hez — maradjon a modell ajánlása."""
    return {k: v for k, v in d.items() if v is not None}


def _format_generation_params_md(
    *,
    model: str,
    width: int | None,
    height: int | None,
    steps: int | None,
    cfg: float | None,
    seed: int | None,
    neg: str,
    show: bool,
    preset_label: str | None = None,
    config_json: dict[str, Any] | None = None,
) -> str:
    if not show:
        return ""
    neg_disp = (neg or "").strip()
    if len(neg_disp) > 600:
        neg_disp = neg_disp[:600] + "…"
    lines = [
        "### Draw Things — beállítások",
        "",
        f"- **Checkpoint (.ckpt):** `{model}`",
    ]
    if preset_label:
        lines.append(f"- **Stílus preset:** `{preset_label}`")
    lines += [
        f"- **Méret:** {width}×{height}"
        if (width and height)
        else "- **Méret:** *(alapértelmezés / modell)*",
        f"- **Steps:** {steps}"
        if steps is not None
        else "- **Steps:** *(nincs megadva — a modell/CLI ajánlott értéke; állítsd a Valves **STEPS**, a **STYLE_PRESETS_JSON**, vagy a JSON `steps` mezőt)*",
        f"- **CFG (guidance):** {cfg}"
        if cfg is not None
        else "- **CFG:** *(nincs megadva — Valves **CFG**, **STYLE_PRESETS_JSON**, vagy JSON `cfg` / `guidanceScale`)*",
        f"- **Seed:** {seed}" if seed is not None else "- **Seed:** véletlen",
        "- **Negatív prompt:** "
        + (f"`{neg_disp}`" if neg_disp else "*(üres)*"),
    ]
    cj = config_json or {}
    if cj.get("sampler") is not None:
        try:
            sid = int(cj["sampler"])
            sn = _SAMPLER_NAMES.get(sid, f"#{sid}")
        except (TypeError, ValueError):
            sn = str(cj["sampler"])
            sid = cj["sampler"]
        lines.append(f"- **Sampler:** {sn} (`sampler`: {sid})")
    if (cj.get("refinerModel") or "").strip():
        lines.append(
            f"- **Refiner modell:** `{cj['refinerModel']}` · **refinerStart:** {cj.get('refinerStart', '—')}"
        )
    if "hiresFix" in cj:
        lines.append(
            "- **High resolution fix (z_image):** "
            + ("be (`hiresFix`: true)" if cj.get("hiresFix") else "ki")
        )
    if (cj.get("upscaler") or "").strip():
        lines.append(
            f"- **Upscaler (opcionális):** `{cj['upscaler']}` · **scale:** {cj.get('upscalerScaleFactor', '—')}"
        )
    elif cj.get("hiresFix"):
        lines.append(
            "- **Upscaler:** *nincs megadva* — opcionális (külön a HR fix-től); ha van ESRGAN/Universal .ckpt a Models mappában, add meg a **UPSCALER_CKPT** Valves-ban."
        )
    lines.append("")
    return "\n".join(lines)


def _strip_markdown_json_fence(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return text
    lines = t.splitlines()
    if not lines:
        return text
    if lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


def _try_parse_json_object(text: str) -> dict[str, Any] | None:
    """
    JSON kinyerése: a ```json … ``` regex **nem** jó (az első `}`-nél megáll).
    json.JSONDecoder().raw_decode a teljes objektumot parse-olja.
    """
    candidates = [text, _strip_markdown_json_fence(text)]
    dec = json.JSONDecoder()
    for blob in candidates:
        if not blob or "{" not in blob:
            continue
        start = 0
        while True:
            i = blob.find("{", start)
            if i < 0:
                break
            try:
                obj, _end = dec.raw_decode(blob[i:])
                if isinstance(obj, dict) and (
                    "prompt" in obj
                    or "negative_prompt" in obj
                    or ("width" in obj and "height" in obj)
                ):
                    return obj
            except json.JSONDecodeError:
                pass
            start = i + 1
    return None


def _is_ready_generate_json(text: str) -> bool:
    """Van-e parse-olható Draw Things JSON (trigger nélkül is indulhat)."""
    j = _try_parse_json_object(text)
    if not j:
        return False
    return bool((j.get("prompt") or "").strip())


_KV_HEADER = re.compile(
    r"^\s*(Stílus|Style|Téma|Theme|Méret|Size|Prompt|Negatív|Negative|LoRA|Lora)\s*:\s*(.*)$",
    re.IGNORECASE,
)


def _parse_kv_lines(text: str) -> dict[str, Any]:
    """Soronkénti kulcs: érték + többsoros Prompt."""
    lines = text.splitlines()
    i = 0
    out: dict[str, Any] = {}
    while i < len(lines):
        m = _KV_HEADER.match(lines[i])
        if not m:
            i += 1
            continue
        label = m.group(1).lower()
        rest = (m.group(2) or "").strip()
        key = {
            "stílus": "style",
            "style": "style",
            "téma": "theme",
            "theme": "theme",
            "méret": "size",
            "size": "size",
            "prompt": "prompt",
            "negatív": "negative",
            "negative": "negative",
            "lora": "lora",
        }.get(label, label)
        if key == "prompt" and not rest:
            j = i + 1
            buf: list[str] = []
            while j < len(lines):
                if _KV_HEADER.match(lines[j]):
                    break
                buf.append(lines[j])
                j += 1
            out["prompt"] = "\n".join(buf).strip()
            i = j
            continue
        if key == "size":
            w, h = _parse_size(rest)
            if w:
                out["width"] = w
            if h:
                out["height"] = h
        elif key in ("negative", "lora"):
            out[key] = rest
        else:
            out[key] = rest
        i += 1
    return out


def _parse_user_bundle(text: str) -> dict[str, Any]:
    """JSON blokk vagy kulcssorok + maradék = prompt."""
    j = _try_parse_json_object(text)
    if j:
        o: dict[str, Any] = dict(j)
        if "size" in o and isinstance(o["size"], str):
            w, h = _parse_size(o["size"])
            if w:
                o["width"] = o.get("width") or w
            if h:
                o["height"] = o.get("height") or h
        return o
    kv = _parse_kv_lines(text)
    if kv:
        return kv
    return {}


def _map_fragments(hint_map: dict[str, Any], *bags: str) -> list[str]:
    """Ha a kulcs (kisbetű) benne van bármelyik bag szövegben, a hozzá tartozó stringet hozzáadja."""
    blob = " ".join(bags).lower()
    fr: list[str] = []
    for k, v in hint_map.items():
        if not isinstance(v, str) or not v.strip():
            continue
        ks = str(k).lower()
        if len(ks) < 2:
            continue
        if ks in blob:
            fr.append(v.strip())
    return fr


def _config_for_style(lora_map: dict[str, Any], style: str, theme: str, prompt: str) -> dict[str, Any]:
    """LORA_BY_STYLE_JSON: kulcs → részleges dict; első találat deep merge."""
    bag = f"{style} {theme} {prompt}".lower()
    merged: dict[str, Any] = {}
    for k, v in lora_map.items():
        if not isinstance(v, dict):
            continue
        if str(k).lower() in bag:
            merged = _deep_merge(merged, v)
    return merged


def _help_trigger_hu(valves: Any | None = None) -> str:
    wiz = valves and _wizard_ollama_enabled(valves)
    wiz_line = ""
    if wiz:
        wiz_line = (
            "**Varázsló mód be van kapcsolva:** ugyanabban a körben az **Ollama** beszélgetik veled, "
            "majd a válaszban lévő **JSON** után automatikusan indul a Draw Things — nem kell külön szöveges modellre váltani. "
            "Ha csak szöveget kapsz kép nélkül, a modellnek a ```json … ``` blokkot is ki kell adnia (lásd system prompt).\n\n"
        )
    return (
        "### Képgenerálás — trigger szükséges\n\n"
        + wiz_line
        + "**Fontos:** a képhez a chatben a **Pipe / Draw Things** modellt kell választani (a normál LLM nem hívja a bridge-et). "
        "Illeszd be a JSON-t vagy **KÉSZ MEHET** (a **TRIGGER_REGEX** szerint).\n\n"
        "A fő chatben egyeztess: **stílus**, **méret**, **prompt**. "
        "Ezután a Pipe-ban: **```json … ```** blokk `prompt` mezővel = **trigger nélkül is indul** (ha `TRIGGER_MODE=required`).\n\n"
        "Vagy szövegesen, majd:\n\n"
        "**`KÉSZ MEHET`** (vagy `MEHET`, `INDÍTS`, `GO` — a Valves **TRIGGER_REGEX** szerint)\n\n"
        "Példa:\n\n"
        "```text\n"
        "Stílus: vízfesték, puha kontúr\n"
        "Téma: őszi erdő, köd\n"
        "Méret: 1024x1024\n"
        "Prompt:\n"
        "Egy kis híd a patak felett, reggeli fény\n\n"
        "KÉSZ MEHET\n"
        "```\n\n"
        "Vagy egy JSON blokk ```json … ``` a `prompt`, `negative_prompt`, `width`, `height` mezőkkel."
    )


def _raw_percent_from_payload(
    percent: float | None,
    current: int | None,
    total: int | None,
) -> float:
    """Egy eseményből becsült 0..1; a sampling lépés és az UI% közül a nagyobb."""
    a = 0.0
    if percent is not None:
        a = max(a, max(0.0, min(1.0, float(percent))))
    if current is not None and total is not None and total > 0:
        a = max(a, max(0.0, min(1.0, current / total)))
    return a


def _progress_eta_suffix(percent_0_1: float, t0: float | None) -> str:
    """Egyszerű ETA: eltelt idő és % alapján (lineáris becslés)."""
    if t0 is None:
        return ""
    p = max(0.0, min(1.0, float(percent_0_1)))
    if p <= 0.03 or p >= 0.998:
        return ""
    elapsed = time.monotonic() - t0
    if elapsed < 0.35:
        return ""
    eta = elapsed * (1.0 - p) / max(p, 0.03)
    if eta > 7200 or eta < 1.5:
        return ""
    if eta < 120:
        return f" · *~{int(eta)} s hátra (becsült)*"
    m, s = int(eta // 60), int(eta % 60)
    return f" · *~{m} p {s} mp hátra (becsült)*"


def _stream_started_placeholder_md(valves: Any, summary_prefix: str) -> str:
    """Open WebUI emitter: azonnali tartalom — ne legyen üres a buborék az első SSE előtt."""
    hint = (
        "**A kérés a bridge felé ment — a generálás elindult.**  \n"
        "*A részletes % és lépés a CLI kimenetétől függ; ha sokáig nem mozdul: "
        "`DRAWTHINGS_BRIDGE_NO_SCRIPT=0` a Mac bridge környezetében.*\n\n"
    )
    ring = _progress_for_valves(
        valves,
        percent_0_1=0.02,
        current=None,
        total=None,
        line="",
        include_title=False,
    )
    body = "### Képgenerálás\n\n" + hint + ring
    if summary_prefix:
        return summary_prefix + "\n\n" + body
    return body


def _stream_waiting_cli_placeholder_md(valves: Any, summary_prefix: str) -> str:
    """Még nincs progress SSE — heartbeat, hogy ne tűnjön lefagynak a chat."""
    hint = (
        "**A bridge fut — várakozás a draw-things-cli első haladására.**  \n"
        "*Ha ez sokáig így marad, a kimenet pufferezhet (macOS: `DRAWTHINGS_BRIDGE_NO_SCRIPT=0`).*\n\n"
    )
    ring = _progress_for_valves(
        valves,
        percent_0_1=0.08,
        current=None,
        total=None,
        line="",
        include_title=False,
    )
    body = "### Képgenerálás\n\n" + hint + ring
    if summary_prefix:
        return summary_prefix + "\n\n" + body
    return body


def _sync_generation_wait_md(valves: Any) -> str:
    """
    Szinkron `/generate` előtt: a felhasználó lássa, hogy **történik valami** (a POST blokkolhat percekig).
    """
    hint = (
        "**A bridge megkapta a kérést — a draw-things-cli most fut a Macen.**  \n"
        "*Ez a nézet addig marad, amíg a kép elkészül (akár több perc is lehet). Ne zárd be a lapot.*  \n"
        "*Élő %-os haladás: kapcsold **STREAM_PROGRESS**-t; Mac bridge: `DRAWTHINGS_BRIDGE_NO_SCRIPT=0` (lásd `run_bridge.sh`).*\n\n"
    )
    ring = _progress_for_valves(
        valves,
        percent_0_1=0.12,
        current=None,
        total=None,
        line="szinkron mód — várakozás a kész PNG-re…",
        include_title=False,
    )
    return "### Képgenerálás folyamatban\n\n" + hint + ring


def _phase_from_line(line: str) -> str:
    """Egy rövid fázis-szöveg: ha több kulcsszó van ugyanabban a sorban (CLI / régi bridge), a legutolsót vesszük."""
    short = (line or "").strip().replace("\n", " ")
    if not short:
        return ""
    best_idx = -1
    for key in ("Finishing", "Sampling", "Processing", "Starting"):
        idx = short.rfind(key)
        if idx > best_idx:
            best_idx = idx
    if best_idx >= 0:
        return short[best_idx :][:72]
    return short[:72]


def _progress_ring_markdown(
    *,
    percent_0_1: float,
    current: int | None,
    total: int | None,
    line: str,
    include_title: bool = False,
    eta_suffix: str = "",
) -> str:
    """Kompakt SVG gyűrű (kék) + markdown felirat (OWUI gyakran nem rendereli jól a **-t HTML <p>-ben)."""
    p = max(0.0, min(1.0, float(percent_0_1)))
    pct = int(round(p * 100))
    # Kis %-nál az ív egyébként láthatatlan — minimum ívhossz a gyűrűn (a százalék szöveg a valós p)
    p_arc = max(p, 0.04) if p > 0 else 0.0
    p_arc = min(1.0, p_arc)
    r, cx, cy = 20, 28, 28
    circ = 2 * math.pi * r
    off = circ * (1.0 - p_arc)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="56" height="56" viewBox="0 0 56 56">'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#e5e7eb" stroke-width="5"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#2563eb" stroke-width="5" '
        f'stroke-linecap="round" transform="rotate(-90 {cx} {cy})" '
        f'stroke-dasharray="{circ:.4f}" stroke-dashoffset="{off:.4f}"/>'
        f'<text x="{cx}" y="{cy+6}" text-anchor="middle" font-size="13" font-weight="600" '
        f'fill="#2563eb" font-family="system-ui,-apple-system,sans-serif">{pct}%</text>'
        f"</svg>"
    )
    b64 = base64.standard_b64encode(svg.encode("utf-8")).decode("ascii")
    step = ""
    if current is not None and total is not None:
        step = f" · **{current}/{total}**"
    phase = _phase_from_line(line)
    cap = f"**{pct}%**{step}"
    if phase:
        cap += f" · *{phase}*"
    if eta_suffix:
        cap += eta_suffix
    title = "### Képgenerálás\n\n" if include_title else ""
    return (
        title
        + f"![generálás](data:image/svg+xml;base64,{b64})\n\n"
        + cap
        + "\n\n"
    )


def _progress_block(
    *,
    percent_0_1: float,
    current: int | None,
    total: int | None,
    line: str,
    width: int = 20,
    include_title: bool = False,
    eta_suffix: str = "",
) -> str:
    """Régi ASCII sáv (STREAM_PROGRESS_UI=bar)."""
    p = max(0.0, min(1.0, float(percent_0_1)))
    filled = int(round(p * width))
    pct_txt = f"{p * 100:.0f}%"
    step = ""
    if current is not None and total is not None:
        step = f" · **{current}/{total}**"
    phase = _phase_from_line(line)
    title = "### Képgenerálás\n\n" if include_title else ""
    top = "▒" * width
    inner = "▓" * filled + "░" * (width - filled)
    box = (
        f"`{top}`\n"
        f"`▓{inner}▓` **{pct_txt}**{step}\n"
        f"`{top}`\n"
    )
    tail = (f"\n*{phase}*" if phase else "") + (eta_suffix + "\n" if eta_suffix else "\n")
    return title + box + tail


def _progress_for_valves(
    valves: Any,
    *,
    percent_0_1: float,
    current: int | None,
    total: int | None,
    line: str,
    include_title: bool,
    eta_suffix: str = "",
) -> str:
    ui = (getattr(valves, "STREAM_PROGRESS_UI", None) or "ring").strip().lower()
    if ui in ("bar", "ascii", "legacy"):
        return _progress_block(
            percent_0_1=percent_0_1,
            current=current,
            total=total,
            line=line,
            include_title=include_title,
            eta_suffix=eta_suffix,
        )
    return _progress_ring_markdown(
        percent_0_1=percent_0_1,
        current=current,
        total=total,
        line=line,
        include_title=include_title,
        eta_suffix=eta_suffix,
    )


async def _owui_emit_replace(emitter: Any, content: str) -> bool:
    """Open WebUI: ugyanabban az asszisztens buborékban cserél (nem új yield-sor)."""
    if emitter is None:
        return False
    try:
        r = emitter({"type": "replace", "data": {"content": content}})
        if hasattr(r, "__await__"):
            await r
    except Exception:
        return False
    return True


async def _iter_sse_events(
    url: str,
    payload: dict[str, Any],
    timeout_s: float = 3600.0,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """SSE: (event_name, parsed_json) párok; a `data` sor JSON-ját parse-olja."""
    import httpx

    event_name: str | None = None
    data_lines: list[str] = []

    def _flush() -> tuple[str, dict[str, Any]] | None:
        nonlocal event_name, data_lines
        if not data_lines:
            event_name = None
            return None
        raw = "\n".join(data_lines)
        data_lines = []
        ev = event_name or "message"
        event_name = None
        try:
            obj = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            obj = {"_raw": raw[:500]}
        return ev, obj

    timeout = httpx.Timeout(timeout_s, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line is None:
                    continue
                if line == "":
                    pair = _flush()
                    if pair is not None:
                        yield pair[0], pair[1]
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())

            pair = _flush()
            if pair is not None:
                yield pair[0], pair[1]


def _resolved_style_presets_for_wizard(valves: Any) -> dict[str, Any]:
    """Ugyanaz a preset-forrás, mint a generálásnál (üres Valves → beágyazott JSON)."""
    raw_sp = (getattr(valves, "STYLE_PRESETS_JSON", None) or "").strip()
    if not raw_sp or raw_sp == "{}":
        base = _load_json_map(_EMBEDDED_STYLE_PRESETS_JSON)
        merged = {**base, **_EMBEDDED_EXTRA_STYLE_PRESETS}
        return _normalize_style_presets_for_z_image(valves, merged)
    return _normalize_style_presets_for_z_image(valves, _load_json_map(raw_sp))


def _style_preset_list_for_wizard_prompt(valves: Any) -> str:
    """Összes preset: markdown táblázat (HU/EN + leírás), ugyanebben a fájlban (`format_wizard_style_preset_table_markdown`)."""
    presets = _resolved_style_presets_for_wizard(valves)
    keys = sorted(
        (str(k) for k, v in presets.items() if isinstance(v, dict)),
        key=lambda s: s.casefold(),
    )
    return format_wizard_style_preset_table_markdown(keys)


def _load_wizard_system_prompt(valves: Any) -> str:
    raw = (getattr(valves, "WIZARD_SYSTEM_PROMPT", None) or "").strip()
    base = raw if raw else _EMBEDDED_WIZARD_SYSTEM_PROMPT_HU
    steps_hint = (
        "\n\n## Opcionális lépés (steps) kérdés\n"
        "Miután a méret megvan, kérdezd meg röviden:\n"
        "„Maradjon az **alap/preset** lépésszám, vagy emeljük? Válasz: `nem` / `igen` / konkrét szám.”\n"
        "- Ha `nem`: a JSON-ban `steps: null` (vagy 8, ha kifejezetten ezt kérik).\n"
        "- Ha `igen`: állítsd `steps: 16`.\n"
        "- Ha konkrét számot kér: használd azt, de legfeljebb **22**.\n"
    )
    if steps_hint not in base:
        base += steps_hint
    inject_style = _style_preset_list_for_wizard_prompt(valves)
    inject_size = format_wizard_size_table_markdown()
    if "{{STYLE_PRESET_LIST}}" in base:
        base = base.replace("{{STYLE_PRESET_LIST}}", inject_style)
    if "{{WIZARD_SIZE_TABLE}}" in base:
        base = base.replace("{{WIZARD_SIZE_TABLE}}", inject_size)
    if raw:
        if "{{STYLE_PRESET_LIST}}" not in raw:
            base += (
                "\n\n## Stílus-presetek (Pipe — táblázat; mindegyik sor a kérdezéskor)\n\n"
                + inject_style
            )
        if "{{WIZARD_SIZE_TABLE}}" not in raw:
            base += "\n\n## Méret-presetek (Pipe — táblázat)\n\n" + inject_size
        return base
    return base


def _wizard_static_style_step_md(valves: Any) -> str:
    """Determinista első varázsló-lépés: teljes stílus táblázat + egyértelmű kérdés."""
    table = _style_preset_list_for_wizard_prompt(valves)
    return (
        "Szuper, kezdjük a stílus kiválasztásával.\n\n"
        + table
        + "\n\n"
        + "Válassz egy **Kulcs**ot a táblázat első oszlopából (vagy írj saját stílust egy rövid mondatban)."
    )


def _wizard_should_force_style_step(raw_text: str) -> bool:
    """
    Kezdő, rövid „indító” képkérésnél (pl. „generálj képet”) ne az LLM döntsön:
    adjunk fix stílus-táblát, hogy ne menjen el random kérdések irányába.
    """
    t = _ascii_fold_hu(raw_text or "")
    if not t.strip():
        return False
    # Ha már van bundle adat, ne erőltessük az első stílus-lépést.
    b = _parse_user_bundle(raw_text or "")
    if any(
        (b.get("style"), b.get("style_label"), b.get("prompt"), b.get("size"), b.get("width"), b.get("height"))
    ):
        return False
    # Kifejezetten rövid, „indító” kérések.
    if len(t) > 64:
        return False
    return bool(
        re.search(
            r"\b(generalj|genera|keszits|rajzolj|mutass|create|generate|draw)\b.{0,24}\b(kep|kepet|image|picture)\b",
            t,
        )
    )


def _wizard_confirm_go(text: str) -> bool:
    t = _ascii_fold_hu(text or "")
    return bool(
        re.search(
            r"(^|\b)(kesz mehet|mehet|inditsd|indits|start|go|igen|johet|jo mehet|ok mehet)(\b|$)",
            t,
        )
    )


def _wizard_parse_size_choice(text: str) -> tuple[int | None, int | None]:
    # 1) direkt 1024x1536
    w, h = _parse_size(text or "")
    if w and h:
        return w, h
    # 2) "3:4 normal" / "16:9 small" / HU változatok
    t = (text or "").strip().lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    m = re.search(
        r"\b(1\s*[:/\- ]\s*2|2\s*[:/\- ]\s*3|3\s*[:/\- ]\s*4|4\s*[:/\- ]\s*5|1\s*[:/\- ]\s*1|"
        r"5\s*[:/\- ]\s*4|4\s*[:/\- ]\s*3|3\s*[:/\- ]\s*2|2\s*[:/\- ]\s*1|16\s*[:/\- ]\s*9|9\s*[:/\- ]\s*16)\b"
        r".{0,24}\b(small|normal|large|kicsi|kozepes|nagy)\b",
        t,
    )
    if not m:
        return None, None
    ar = re.sub(r"\s*[:/\- ]\s*", ":", m.group(1).strip())
    tier = m.group(2)
    idx = 0 if tier in ("small", "kicsi") else (1 if tier in ("normal", "kozepes") else 2)
    for ratio, _label, sm, md, lg, _note in _WIZARD_SIZE_TABLE_ROWS:
        if ratio == ar:
            cand = (sm, md, lg)[idx]
            return int(cand[0]), int(cand[1])
    return None, None


def _wizard_edit_intent(text: str) -> str | None:
    """
    Rövid szerkesztési szándékok felismerése a wizard összegzés állapotában.

    Csak rövid üzenetekre: hosszú jelenetleírásokban előforduló „stílus”, „prompt”, „méret”
    szavak ne nyissák újra a teljes stílus táblát / ne ugorjanak félre a lépések.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    if len(raw) > 160:
        return None
    t = _ascii_fold_hu(raw)
    if not t:
        return None
    if re.search(r"\b(negativ|negative)\b", t):
        return "negative"
    if re.search(r"\b(prompt|uj prompt|promptot|promtot)\b", t):
        return "prompt"
    if re.search(r"\b(meret|size|keparany|felbontas)\b", t):
        return "size"
    if re.search(r"\b(stilus|style|style label|style_label)\b", t):
        return "style"
    return None


def _wizard_is_meta_prompt_edit_text(text: str) -> bool:
    """Ne tekintsük tényleges képpromptnak az olyan mondatokat, mint „új promptot szeretnék”."""
    t = _ascii_fold_hu(text or "")
    if not t:
        return False
    return bool(
        re.search(
            r"\b(uj prompt|promptot szeretnek|promptot akarok|promptot modosit|modositanam a promptot|uj promtot)\b",
            t,
        )
    )


def _wizard_current_session_user_messages(body: dict) -> list[str]:
    """
    Csak az utolsó „új képkérés” ciklus user üzenetei.
    Így a korábbi kör prompt/méret nem szivárog át az új körbe.
    """
    msgs = body.get("messages") or []
    user_texts: list[str] = []
    for m in msgs:
        if m.get("role") == "user":
            t = _extract_user_content(m).strip()
            if t:
                user_texts.append(t)
    if not user_texts:
        return []
    start_idx = 0
    for i, t in enumerate(user_texts):
        if _wizard_should_force_style_step(t):
            start_idx = i
    return user_texts[start_idx:]


def _wizard_collect_state_from_messages(
    valves: Any, body: dict
) -> tuple[str, str, int | None, int | None, list[str]]:
    """
    Állapot user üzenetekből: (style, prompt, width, height, post_size_messages).
    A `post_size_messages` a méret megadása utáni user üzenetek (lépés / CFG / megerősítés).
    """
    style = ""
    prompt = ""
    width: int | None = None
    height: int | None = None
    post_size: list[str] = []
    size_settled = False

    presets = _resolved_style_presets_for_wizard(valves)

    def _short_style_candidate(txt: str) -> str:
        t = (txt or "").strip()
        if not t:
            return ""
        # Rövid, jelölésszerű válaszokból vegyünk csak style-t (ne a hosszú promptból).
        if len(t) > 64:
            return ""
        # NSFW-t preferáljuk kevert "nsfw + anime" szövegnél.
        if _is_nsfw_intent(valves, style=t, theme="", prompt_core=t, extra_neg=""):
            return "nsfw"
        pk, _pv = _match_style_preset(presets, t, "", "")
        return str(pk) if pk else ""

    for txt in _wizard_current_session_user_messages(body):
        if not txt:
            continue

        if size_settled:
            post_size.append(txt)
            continue

        b = _parse_user_bundle(txt)
        st = str(b.get("style") or b.get("style_label") or "").strip()
        pr = str(b.get("prompt") or "").strip()
        bw = b.get("width")
        bh = b.get("height")

        if st:
            style = st
        else:
            cand = _short_style_candidate(txt)
            if cand and not style:
                style = cand

        if isinstance(bw, int) and isinstance(bh, int):
            width, height = int(bw), int(bh)
        else:
            sw, sh = _wizard_parse_size_choice(txt)
            if sw and sh:
                width, height = sw, sh

        if pr:
            prompt = pr
        else:
            # Nem kulcssor, nem size/confirm: promptnak tekintjük (akkor is, ha szerepel benne pl. anime).
            raw = txt.strip()
            if raw and len(raw) >= 20:
                if not _wizard_confirm_go(raw):
                    if not _wizard_parse_size_choice(raw)[0]:
                        if not _short_style_candidate(raw) and not _wizard_is_meta_prompt_edit_text(raw):
                            prompt = raw

        if isinstance(width, int) and isinstance(height, int):
            size_settled = True

    return style, prompt, width, height, post_size


def _wizard_preset_steps_cfg_hint(valves: Any, style_key: str) -> tuple[str, str]:
    """Megjelenítéshez: preset lépés / cfg becslés (stílus kulcs alapján)."""
    presets = _resolved_style_presets_for_wizard(valves)
    pk, preset = _match_style_preset(presets, style_key or "", "", "")
    if not isinstance(preset, dict):
        return "—", "—"
    st = preset.get("steps")
    cg = preset.get("cfg")
    if cg is None:
        cg = preset.get("guidance_scale") or preset.get("guidanceScale")
    return (
        str(st) if st is not None else "—",
        str(cg) if cg is not None else "—",
    )


def _wizard_parse_step_choice(text: str) -> tuple[str, int | None] | None:
    """
    Vissza: ('default', None) vagy ('manual', n) ahol n in 12..22; egyébként None.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    t = _ascii_fold_hu(raw)
    if re.search(r"\b(alap|preset|default|alapertelmezett|gyari|gyári)\b", t):
        return ("default", None)
    if re.search(r"\b(manualis|manual|kezi|sajat|saját)\b", t):
        m = re.search(r"\b(1[2-9]|2[0-2])\b", raw)
        if m:
            return ("manual", int(m.group(1)))
        return None
    m = re.match(r"^\s*(\d{1,2})\s*$", raw)
    if m:
        n = int(m.group(1))
        if 12 <= n <= 22:
            return ("manual", n)
    return None


def _wizard_parse_cfg_yes_no(text: str) -> bool | None:
    """CFG módosítás: True = igen, False = nem, None = nem egyértelmű."""
    t = _ascii_fold_hu(text or "").strip()
    if not t:
        return None
    if re.search(r"\b(nem|no)\b", t) and not re.search(r"\bigen\b", t):
        return False
    if re.search(r"\b(igen|yes)\b", t):
        return True
    return None


def _wizard_parse_cfg_float(text: str) -> float | None:
    raw = (text or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        return None
    if v <= 0 or v > 50:
        return None
    return v


def _wizard_final_confirm_go(text: str) -> bool:
    """Végleges generálás megerősítése (nem keveredik a CFG „igen”-nel az első körben)."""
    t = _ascii_fold_hu(text or "")
    return bool(
        re.search(
            r"(^|\b)(kesz mehet|mehet|inditsd|indits|start|go|igen|johet|jo mehet|ok mehet)(\b|$)",
            t,
        )
    )


@dataclass
class WizardPostSizeState:
    step_mode: str | None = None
    manual_steps: int | None = None
    cfg_change: bool | None = None
    cfg_value: float | None = None
    upscale_want: bool | None = None


def _wizard_parse_post_size_state(msgs: list[str]) -> WizardPostSizeState:
    """
    Méret utáni üzenetek:
    - CFG **nem**: [lépés], [CFG nem], [post-upscale igen/nem] → generálás
    - CFG **igen**: [lépés], [igen], [CFG szám], [post-upscale igen/nem], [KÉSZ MEHET] → generálás
    """
    out = WizardPostSizeState()
    if not msgs:
        return out
    r0 = _wizard_parse_step_choice(msgs[0])
    if not r0:
        return out
    out.step_mode, out.manual_steps = r0
    if len(msgs) < 2:
        return out
    r1 = _wizard_parse_cfg_yes_no(msgs[1])
    if r1 is None:
        return out
    out.cfg_change = r1
    if r1 is False:
        if len(msgs) >= 3:
            u = _wizard_parse_cfg_yes_no(msgs[2])
            if u is not None:
                out.upscale_want = u
        return out
    if len(msgs) < 3:
        return out
    fv = _wizard_parse_cfg_float(msgs[2])
    if fv is not None:
        out.cfg_value = fv
    # Upscale csak ha a CFG szám már érvényes — különben a 4. üzenet lehet újrapróbált CFG.
    if out.cfg_value is not None and len(msgs) >= 4:
        u = _wizard_parse_cfg_yes_no(msgs[3])
        if u is not None:
            out.upscale_want = u
    return out


def _wizard_summary_block_md(
    style: str,
    prompt: str,
    width: int,
    height: int,
    steps_line: str,
    cfg_line: str,
    upscale_line: str | None = None,
) -> str:
    block = (
        "### Összegzés (generálás előtt)\n\n"
        f"- **Stílus (`style_label`)**: `{style}`\n"
        f"- **Prompt**: {prompt}\n"
        f"- **Méret**: `{width}×{height}`\n"
        f"- **Steps (tervezett)**: {steps_line}\n"
        f"- **CFG (tervezett)**: {cfg_line}\n"
    )
    if upscale_line is not None:
        block += f"- **Post-upscale**: {upscale_line}\n"
    block += (
        "- **Negatív prompt**: automatikus (globális + stílus preset + map + opcionális user JSON)\n"
    )
    return block


def _wizard_first_summary_and_step_question(
    style: str, prompt: str, width: int, height: int, valves: Any
) -> str:
    ps, pc = _wizard_preset_steps_cfg_hint(valves, style)
    body = _wizard_summary_block_md(
        style,
        prompt,
        width,
        height,
        f"preset: **{ps}** (ha alapértelmezettet választod)",
        f"preset: **{pc}** (ha a CFG módosításnál „nem”-et írsz)",
    )
    return (
        body
        + "\n---\n\n"
        "**Lépésszám:** **alapértelmezett** (preset) vagy **manuális**?\n\n"
        "- Írd: `alapértelmezett` / `preset` / `default` — a stílus preset lépése marad.\n"
        "- Vagy **manuális** lépés **12–22** között: pl. `16`, `manuális 18`, `manual 20`.\n"
    )


def _wizard_ask_cfg_change_md() -> str:
    return (
        "**CFG (guidance) módosítás:** szeretnél ettől eltérő CFG értéket?\n\n"
        "- Írd: **`nem`** — a preset / pipeline szerinti CFG marad; **utána** egy **post-upscale** kérdés következik.\n"
        "- Írd: **`igen`** — a következő üzenetben add meg a **CFG számot** (pl. `1.0`, `1.15`).\n"
    )


def _wizard_ask_cfg_value_md() -> str:
    return (
        "Add meg a **CFG** értékét egy számként (pl. `1.0` … `4.5` — a végleges értéket a modell "
        "és a Pipe **Z_IMAGE_CFG** szabályai is alakíthatják).\n\n"
        "Utána: **post-upscale** (igen/nem), majd egy **frissített összegzés**; ha jó, írd: "
        "**`KÉSZ MEHET`** (vagy `igen`)."
    )


def _wizard_second_summary_full_md(
    style: str,
    prompt: str,
    width: int,
    height: int,
    step_mode: str,
    manual_steps: int | None,
    cfg_change: bool,
    cfg_value: float | None,
    valves: Any,
    upscale_want: bool,
) -> str:
    ps_hint, pc_hint = _wizard_preset_steps_cfg_hint(valves, style)
    if step_mode == "default":
        steps_line = f"preset (**{ps_hint}** lépés)"
    else:
        steps_line = f"**{manual_steps}** (manuális, 12–22)"
    if not cfg_change:
        cfg_line = f"preset / pipeline (**{pc_hint}**)"
    else:
        cfg_line = f"**{cfg_value}** (megadott)"
    us_line = _wizard_upscale_summary_line(valves, upscale_want)
    return (
        _wizard_summary_block_md(
            style, prompt, width, height, steps_line, cfg_line, upscale_line=us_line
        )
        + "\n---\n\n"
        "Ha minden rendben, írd: **`KÉSZ MEHET`** (vagy `igen` / `mehet`)."
    )


def _wizard_step_invalid_hint_md() -> str:
    return (
        "**Nem értettem a lépésszám választ.** Írd: **`alapértelmezett`** / **`preset`**, "
        "vagy egy **12–22** közötti számot (pl. `16` vagy `manuális 16`).\n"
    )


def _wizard_cfg_float_invalid_md() -> str:
    return (
        "**Nem értettem a CFG számot.** Írj egy pozitív decimálist (pl. `1.2`), maximum ~50.\n"
    )


def _wizard_upscale_invalid_md() -> str:
    return (
        "**Nem értettem a post-upscale választ.** Írd: **`igen`** vagy **`nem`** "
        "(Valves **UPSCALER_CKPT** alapján, ha **igen** és van fájl).\n"
    )


def _wizard_ask_upscale_md(valves: Any) -> str:
    u = (getattr(valves, "UPSCALER_CKPT", None) or "").strip()
    if u:
        hint = f"Jelenlegi Valves **UPSCALER_CKPT**: `{u}`."
    else:
        hint = (
            "A Valves **UPSCALER_CKPT** most **üres** — ha **igen**-t írsz, "
            "nincs upscaler fájl, így nem történik tényleges upscale."
        )
    return (
        "**Post-upscale:** szeretnél a generálás után upscaler lépést (pl. 2× / ESRGAN), "
        "ahogy a Valves **UPSCALER_CKPT** beállítja?\n\n"
        "- **`igen`** — a Pipe beolvassa a Valves upscaler + skála beállításokat (ha van mit).\n"
        "- **`nem`** — **nincs** `upscaler` a `config_json`-ban ebben a körben.\n\n"
        f"{hint}\n"
    )


def _wizard_upscale_summary_line(valves: Any, upscale_want: bool) -> str:
    if not upscale_want:
        return "**nem** — nincs post-upscale ebben a körben"
    u = (getattr(valves, "UPSCALER_CKPT", None) or "").strip()
    if u:
        return f"**igen** — Valves `{u}`"
    return "**igen** — Valves `UPSCALER_CKPT` üres, így nincs upscaler fájl"


def _wizard_build_generate_json_block(
    style: str,
    prompt: str,
    width: int,
    height: int,
    step_mode: str,
    manual_steps: int | None,
    cfg_override: bool,
    cfg_value: float | None,
    *,
    use_upscale: bool = True,
) -> str:
    d: dict[str, Any] = {
        "ready": True,
        "prompt": prompt,
        "width": width,
        "height": height,
        "style_label": style,
        "user_confirmation": "Szabály-alapú varázsló (lépés + CFG + post-upscale)",
        "use_upscale": use_upscale,
    }
    if step_mode == "manual" and manual_steps is not None:
        d["steps"] = int(manual_steps)
    if cfg_override and cfg_value is not None:
        d["cfg"] = float(cfg_value)
    return "```json\n" + json.dumps(d, ensure_ascii=False) + "\n```"


async def _async_wizard_rule_based_wizard(
    pipe: Any,
    valves: Any,
    body: dict,
    emitter: Any,
    last_user: str,
) -> AsyncIterator[str]:
    """
    Kép-varázsló lépések: user üzenetekből összerakott állapot (LLM nélkül).
    Sorrend: stílus → prompt → méret → (összegzés + lépés) → CFG kérdés → opcionális CFG szám
    → post-upscale igen/nem → összegzés (CFG igen esetén) → generálás.
    """
    st, pr, ww, hh, post = _wizard_collect_state_from_messages(valves, body)
    edit_intent = _wizard_edit_intent(last_user)
    if edit_intent == "style":
        yield _wizard_static_style_step_md(valves)
        return
    if edit_intent == "prompt":
        yield _wizard_ask_prompt_md(st or "nincs megadva")
        return
    if edit_intent == "size":
        yield _wizard_ask_size_md()
        return
    if edit_intent == "negative":
        yield (
            "A negatív prompt alapból automatikus (globális + stílus preset + map). "
            "Ha egyedi negatívot szeretnél, írd így egy sorban:\n\n"
            "`Negatív: ...`\n\n"
            "vagy JSON-ban `negative_prompt` mezővel."
        )
        return
    if not (st or "").strip():
        yield _wizard_static_style_step_md(valves)
        return
    if not (pr or "").strip():
        yield _wizard_ask_prompt_md(st)
        return
    if not (isinstance(ww, int) and isinstance(hh, int)):
        yield _wizard_ask_size_md()
        return

    pst = _wizard_parse_post_size_state(post)
    if pst.step_mode is None:
        if post:
            yield _wizard_step_invalid_hint_md()
        yield _wizard_first_summary_and_step_question(st, pr, int(ww), int(hh), valves)
        return
    if pst.cfg_change is None:
        yield _wizard_ask_cfg_change_md()
        return
    if pst.cfg_change is False:
        if pst.upscale_want is None:
            if len(post) >= 3:
                yield _wizard_upscale_invalid_md()
            yield _wizard_ask_upscale_md(valves)
            return
        tp = _wizard_build_generate_json_block(
            st,
            pr,
            int(ww),
            int(hh),
            pst.step_mode,
            pst.manual_steps,
            False,
            None,
            use_upscale=pst.upscale_want,
        )
        yield "\n\n---\n\n**Képgenerálás indul…**\n\n"
        async for part in _run_generate_after_parse(pipe, body, tp, emitter):
            yield part
        return

    if pst.cfg_value is None:
        if len(post) >= 3:
            yield _wizard_cfg_float_invalid_md()
        yield _wizard_ask_cfg_value_md()
        return

    if pst.upscale_want is None:
        if len(post) >= 4:
            yield _wizard_upscale_invalid_md()
        yield _wizard_ask_upscale_md(valves)
        return

    if len(post) < 5:
        yield _wizard_second_summary_full_md(
            st,
            pr,
            int(ww),
            int(hh),
            pst.step_mode,
            pst.manual_steps,
            True,
            pst.cfg_value,
            valves,
            pst.upscale_want,
        )
        return
    if _wizard_final_confirm_go(last_user):
        tp = _wizard_build_generate_json_block(
            st,
            pr,
            int(ww),
            int(hh),
            pst.step_mode,
            pst.manual_steps,
            True,
            pst.cfg_value,
            use_upscale=pst.upscale_want,
        )
        yield "\n\n---\n\n**Képgenerálás indul…**\n\n"
        async for part in _run_generate_after_parse(pipe, body, tp, emitter):
            yield part
        return
    yield _wizard_second_summary_full_md(
        st,
        pr,
        int(ww),
        int(hh),
        pst.step_mode,
        pst.manual_steps,
        True,
        pst.cfg_value,
        valves,
        pst.upscale_want,
    )


def _wizard_ask_prompt_md(style: str) -> str:
    s = (style or "").strip() or "nincs megadva"
    return (
        f"Stílus rendben: **`{s}`**.\n\n"
        "Írd le pontosan, mit szeretnél látni a képen "
        "(szereplők, környezet, hangulat, fények, kompozíció)."
    )


def _wizard_ask_size_md() -> str:
    return (
        "Szuper, megvan a prompt.\n\n"
        + format_wizard_size_table_markdown()
        + "\n\nVálassz képarányt és méretet (pl. `3:4 normal`, `16:9 small`) "
        "vagy adj meg saját `szélesség×magasság` értéket (64 többszörös)."
    )


def _load_general_chat_system_prompt(valves: Any) -> str:
    raw = (getattr(valves, "GENERAL_CHAT_SYSTEM_PROMPT", None) or "").strip()
    if raw:
        return raw
    return _EMBEDDED_GENERAL_CHAT_SYSTEM_PROMPT_HU


def _owui_messages_for_ollama(body: dict, system: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if system.strip():
        out.append({"role": "system", "content": system.strip()})
    for m in body.get("messages") or []:
        if m.get("role") not in ("user", "assistant"):
            continue
        t = _extract_user_content(m)
        if not t.strip():
            continue
        out.append({"role": str(m["role"]), "content": t})
    return out


def _wizard_ollama_enabled(valves: Any) -> bool:
    return bool(
        getattr(valves, "WIZARD_OLLAMA_CHAT", False)
        and (getattr(valves, "OLLAMA_BASE_URL", "") or "").strip()
        and (getattr(valves, "OLLAMA_MODEL", "") or "").strip()
    )


def _llm_translate_available(valves: Any) -> bool:
    """Van beállítva Ollama/LM Studio modell — LLM-mel lehet fordítani pip nélkül."""
    return bool(
        (getattr(valves, "OLLAMA_BASE_URL", None) or "").strip()
        and (getattr(valves, "OLLAMA_MODEL", None) or "").strip()
    )


def _parse_wizard_base_and_key(valves: Any) -> tuple[str, str]:
    """
    OLLAMA_BASE_URL mezőbe gyakran bemásolnak kulcsot is (szóközzel elválasztva).
    Vissza: (tiszta base URL, API kulcs vagy üres).
    """
    raw = (getattr(valves, "OLLAMA_BASE_URL", "") or "").strip()
    explicit = (getattr(valves, "WIZARD_API_KEY", "") or "").strip()
    if not raw:
        return "", explicit
    parts = raw.split()
    url = parts[0].strip().rstrip("/").rstrip(".")
    key = explicit
    if not key and len(parts) > 1:
        rest = " ".join(parts[1:]).strip()
        if rest.startswith("sk-") or rest.startswith("lm-") or len(rest) >= 16:
            key = rest
    return url, key


def _resolve_openai_api_key(valves: Any, key_from_url: str) -> str:
    """
    LM Studio: kötelező Bearer token minden /v1/* híváshoz.
    Sorrend: Valves → URL után másolt kulcs → környezet (OWUI szerveren).
    """
    for candidate in (
        (getattr(valves, "WIZARD_API_KEY", None) or "").strip(),
        (key_from_url or "").strip(),
        (os.environ.get("LM_API_TOKEN") or "").strip(),
        (os.environ.get("LMSTUDIO_API_KEY") or "").strip(),
        (os.environ.get("OPENAI_API_KEY") or "").strip(),
    ):
        if candidate:
            return candidate
    return ""


def _canonical_openai_base(base_url: str) -> str:
    """
    LM Studio: OpenAI-kompatibilis bázis általában ...:1234/v1 (nem .../api/v1).
    """
    u = base_url.rstrip("/").rstrip(".")
    if re.search(r"/api/v1$", u):
        return re.sub(r"/api/v1$", "/v1", u)
    return u


def _lm_studio_url_port_hint(base_raw: str) -> str:
    """
    Gyakori hiba: DDNS / domain + `/v1` port nélkül → a böngésző/httpx a :80-ra megy;
    LM Studio alapból :1234-en hallgat (kivéve reverse proxy).
    """
    s = (base_raw or "").strip()
    if not s or ":1234" in s:
        return ""
    try:
        u = urlparse(s if "://" in s else f"http://{s}")
    except Exception:
        return ""
    path = (u.path or "").lower()
    if "/v1" not in path and not s.rstrip("/").lower().endswith("/v1"):
        return ""
    if (u.hostname or "") in ("127.0.0.1", "localhost", "::1"):
        return ""
    if u.port is not None:
        return ""
    if u.scheme == "http":
        return (
            " **Tip:** Port nélkül ez a cím a **:80**-ra megy. LM Studio alapból "
            "**`http://<cím>:1234/v1`** — a routeren továbbítsd a **1234**-et a Macre, "
            "és az OWUI Valves-ban is add meg a **:1234**-et (nem elég a `…ddns.net/v1`)."
        )
    if u.scheme == "https":
        return (
            " **Tip:** HTTPS `:443` — ha nincs nginx/Caddy proxy az LM Studio elé, "
            "próbáld **`http://<cím>:1234/v1`** (LM Studio helyi szerver)."
        )
    return ""


def _coerce_wizard_backend_from_url(base_raw: str, backend: str) -> str:
    """
    Ha a Valves-ban még „ollama” az alap, de az URL LM Studio / OpenAI-kompatibilis (:1234 vagy /v1),
    automatikusan openai — különben /api/chat-ra megy és elszáll a kapcsolat.
    """
    b = (backend or "ollama").strip().lower()
    if b not in ("ollama", "openai"):
        b = "ollama"
    if b != "ollama":
        return b
    u = base_raw or ""
    ul = u.lower()
    if "/v1" in ul or ":1234" in u:
        return "openai"
    return "ollama"


async def _async_stream_wizard_llm(
    valves: Any,
    body: dict,
    sys_p: str,
) -> AsyncIterator[str]:
    """
    Ugyanaz a varázsló backend / OLLAMA_MODEL, mint a kép-varázslónál — csak a system prompt más.
    Hiba esetén egyetlen hibaüzenetet streamel.
    """
    omsgs = _owui_messages_for_ollama(body, sys_p)
    if not omsgs:
        return
    base_raw, key_from_url = _parse_wizard_base_and_key(valves)
    api_key = _resolve_openai_api_key(valves, key_from_url)
    backend = (getattr(valves, "WIZARD_CHAT_BACKEND", None) or "ollama").strip().lower()
    if backend not in ("ollama", "openai"):
        backend = "ollama"
    backend = _coerce_wizard_backend_from_url(base_raw, backend)
    model = (getattr(valves, "OLLAMA_MODEL", None) or "").strip()
    if backend == "openai" and not api_key:
        yield (
            "**LM Studio API token hiányzik.** A szerver minden kérést elutasít token nélkül. "
            "Állítsd be a **WIZARD_API_KEY** mezőt (Local Server → API key), vagy az Open WebUI-t futtató gépen: "
            "`export LM_API_TOKEN='sk-lm-…'` (és indítsd újra az OWUI-t). "
            "Teszt: `curl -sS -H \"Authorization: Bearer $LM_API_TOKEN\" "
            f"{base_raw or 'http://127.0.0.1:1234/v1'}/models`"
        )
        return
    try:
        if backend == "openai":
            async for ch in _stream_openai_compatible_chat(
                base_raw,
                model,
                omsgs,
                api_key or None,
            ):
                yield ch
        else:
            async for ch in _stream_ollama_chat(
                base_raw,
                model,
                omsgs,
            ):
                yield ch
    except Exception as e:
        hint = ""
        if backend == "openai":
            hint = (
                " Ellenőrizd: **WIZARD_API_KEY** / **LM_API_TOKEN**, és hogy az OWUI **szerver** (nem a böngésző) "
                f"eléri-e: `{base_raw or '?'}`."
            ) + _lm_studio_url_port_hint(base_raw)
        else:
            hint = (
                " Ellenőrizd: Ollama fut-e a megadott URL-en (alapból :11434), és az OWUI **szerver** eléri-e. "
                "LM Studio (:1234, /v1) esetén az URL legyen `http://…:1234/v1` és legyen **WIZARD_API_KEY** — "
                "a Pipe ezt openai módnak ismeri fel."
            )
        yield f"**Varázsló LLM hiba ({backend}):** {e}.{hint}"


async def _wizard_chat_completion_once(
    valves: Any,
    body: dict,
    sys_p: str,
) -> str:
    """
    Fallback: ha a stream üres (néha LM Studio/Ollama stream bug), próbáljunk egy egyszeri non-stream választ.
    """
    omsgs = _owui_messages_for_ollama(body, sys_p)
    if not omsgs:
        return ""
    base_raw, key_from_url = _parse_wizard_base_and_key(valves)
    api_key = _resolve_openai_api_key(valves, key_from_url)
    backend = (getattr(valves, "WIZARD_CHAT_BACKEND", None) or "ollama").strip().lower()
    if backend not in ("ollama", "openai"):
        backend = "ollama"
    backend = _coerce_wizard_backend_from_url(base_raw, backend)
    model = (getattr(valves, "OLLAMA_MODEL", None) or "").strip()
    if not base_raw or not model:
        return ""
    if backend == "openai" and not api_key:
        return ""
    try:
        if backend == "openai":
            out = await _openai_chat_completion_once(base_raw, model, omsgs, api_key or None)
        else:
            out = await _ollama_chat_completion_once(base_raw, model, omsgs)
        return (out or "").strip()
    except Exception:
        return ""


async def _stream_ollama_chat(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
) -> AsyncIterator[str]:
    import httpx

    url = base_url.rstrip("/") + "/api/chat"
    payload = {"model": model, "messages": messages, "stream": True}
    timeout = httpx.Timeout(600.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message") or {}
                c = msg.get("content")
                if c:
                    yield c


async def _stream_openai_compatible_chat(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    api_key: str | None,
) -> AsyncIterator[str]:
    """OpenAI-kompatibilis /v1/chat/completions (LM Studio, LocalAI, stb.), SSE stream."""
    import httpx

    base = _canonical_openai_base(base_url)
    url = base if base.endswith("/chat/completions") else base.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    payload = {"model": model, "messages": messages, "stream": True}
    timeout = httpx.Timeout(600.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].lstrip()
                if data.strip() == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                for ch in obj.get("choices") or []:
                    delta = ch.get("delta") or {}
                    c = delta.get("content")
                    if c:
                        yield c
                    msg = ch.get("message") or {}
                    c2 = (msg.get("content") or "") if isinstance(msg, dict) else ""
                    if c2:
                        yield c2


async def _ollama_chat_completion_once(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
) -> str:
    import httpx

    url = base_url.rstrip("/") + "/api/chat"
    payload = {"model": model, "messages": messages, "stream": False}
    timeout = httpx.Timeout(120.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        obj = r.json()
        msg = obj.get("message") or {}
        return (msg.get("content") or "").strip()


async def _openai_chat_completion_once(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    api_key: str | None,
) -> str:
    import httpx

    base = _canonical_openai_base(base_url)
    url = base if base.endswith("/chat/completions") else base.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    payload = {"model": model, "messages": messages, "stream": False}
    timeout = httpx.Timeout(120.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        obj = r.json()
        ch = (obj.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        return (msg.get("content") or "").strip()


async def _translate_to_english_via_llm(valves: Any, text: str) -> str | None:
    """
    Ha nincs langdetect/deep-translator: egy rövid, nem streamelt chat a varázsló backenddel.
    None = nem sikerült — az eredeti szöveg marad.
    """
    if not (text or "").strip():
        return None
    if not _NON_ASCII.search(text):
        return text
    base_raw, key_from_url = _parse_wizard_base_and_key(valves)
    api_key = _resolve_openai_api_key(valves, key_from_url)
    backend = (getattr(valves, "WIZARD_CHAT_BACKEND", None) or "ollama").strip().lower()
    if backend not in ("ollama", "openai"):
        backend = "ollama"
    backend = _coerce_wizard_backend_from_url(base_raw, backend)
    model = (getattr(valves, "OLLAMA_MODEL", None) or "").strip()
    if not base_raw or not model:
        return None
    if backend == "openai" and not api_key:
        return None
    sys = (
        "Translate to English for image generation (Stable Diffusion / diffusion prompts). "
        "Output ONLY the English text, same meaning, no quotes, no explanation."
    )
    omsgs: list[dict[str, str]] = [
        {"role": "system", "content": sys},
        {"role": "user", "content": text.strip()},
    ]
    try:
        if backend == "openai":
            out = await _openai_chat_completion_once(base_raw, model, omsgs, api_key or None)
        else:
            out = await _ollama_chat_completion_once(base_raw, model, omsgs)
        if not out or not out.strip():
            return None
        return out.strip()
    except Exception:
        return None


async def _ensure_english_async(valves: Any, text: str, enabled: bool) -> tuple[str, bool]:
    """Ha van **OLLAMA_MODEL** + URL: fordítás **először LLM**-mel; különben langdetect+translator; utolsó esély: csak ASCII."""
    if not enabled or not (text or "").strip():
        return text, False
    if _llm_translate_available(valves):
        out = await _translate_to_english_via_llm(valves, text)
        if out and out.strip():
            o = out.strip()
            if o.casefold() != text.strip().casefold():
                return o, True
        # LLM üres / hiba → könyvtár
    if _translation_stack_available():
        return _ensure_english(text, True)
    if not _NON_ASCII.search(text):
        return text, False
    return text, False


async def _run_generate_after_parse(
    pipe: Any, body: dict, text_for_parse: str, emitter: Any = None
) -> AsyncIterator[str]:
    """Bundle feldolgozás → fordítás → bridge (stream vagy sync)."""
    valves = pipe.valves
    bundle = _parse_user_bundle(text_for_parse)
    if not bundle:
        bundle = {}
    if not (bundle.get("prompt") or "").strip() and text_for_parse.strip():
        bundle["prompt"] = text_for_parse.strip()

    prompt_core = (bundle.get("prompt") or "").strip() or text_for_parse.strip()
    if valves.STRIP_IMAGE_PREFIX:
        prompt_core = _normalize_prompt_for_image(prompt_core)

    style = (bundle.get("style") or bundle.get("style_label") or "").strip()
    theme = (bundle.get("theme") or "").strip()
    style_for_head = style
    if (style or "").strip().lower() == "anime" and _user_wants_photorealistic(prompt_core):
        style_for_head = ""
    prompt_core = _merge_style_into_prompt_core(style_for_head, theme, prompt_core)

    raw_sp = (getattr(valves, "STYLE_PRESETS_JSON", None) or "").strip()
    if not raw_sp or raw_sp == "{}":
        base = _load_json_map(_EMBEDDED_STYLE_PRESETS_JSON)
        presets = {**base, **_EMBEDDED_EXTRA_STYLE_PRESETS}
    else:
        presets = _load_json_map(raw_sp)
    presets = _normalize_style_presets_for_z_image(valves, presets)
    preset_key, preset = _match_style_preset(presets, style, theme, prompt_core)
    if (
        preset_key
        and str(preset_key).lower() == "anime"
        and _user_wants_photorealistic(prompt_core)
    ):
        alt = presets.get("Fotorealisztikus")
        if isinstance(alt, dict) and alt.get("model"):
            preset_key, preset = "Fotorealisztikus", alt
    extra_neg = (
        bundle.get("negative") or bundle.get("negative_prompt") or ""
    ).strip()
    nsfw_intent = _is_nsfw_intent(
        valves,
        style=style,
        theme=theme,
        prompt_core=prompt_core,
        extra_neg=extra_neg,
    )
    if nsfw_intent:
        # NSFW-nél egységes preset (nem stílusfüggő modell/preset váltás).
        np = presets.get("nsfw")
        if isinstance(np, dict):
            preset_key, preset = "nsfw", np
    sk = (preset_key or style or "").strip()
    map_style = _load_json_map(valves.NEGATIVE_BY_STYLE_JSON)
    map_theme = _load_json_map(valves.NEGATIVE_BY_THEME_JSON)
    neg_parts = [(valves.NEGATIVE_PROMPT or "").strip()]
    if (preset.get("negative_prompt") or "").strip():
        neg_parts.append(str(preset["negative_prompt"]).strip())
    neg_parts += _map_fragments(map_style, sk, theme, prompt_core)
    neg_parts += _map_fragments(map_theme, sk, theme, prompt_core)
    if extra_neg:
        neg_parts.append(extra_neg)
    neg = ", ".join(p for p in neg_parts if p)

    lora_map = _load_json_map(valves.LORA_BY_STYLE_JSON)
    cfg_extra = _optional_config_json(valves) or {}
    if isinstance(preset.get("config_json"), dict):
        cfg_extra = _deep_merge(cfg_extra, preset["config_json"])
    cfg_style = _config_for_style(lora_map, sk, theme, prompt_core)
    if cfg_style:
        cfg_extra = _deep_merge(cfg_extra, cfg_style)
    if isinstance(bundle.get("config_json"), dict):
        cfg_extra = _deep_merge(cfg_extra, bundle["config_json"])
    if isinstance(bundle.get("lora"), dict):
        cfg_extra = _deep_merge(cfg_extra, bundle["lora"])

    if not prompt_core.strip():
        yield (
            "Üres prompt — írj **Prompt** szöveget vagy illeszd be a Gemma-blokkot "
            "(és ha kell: **KÉSZ MEHET** a végén, `TRIGGER_MODE=required`)."
        )
        return

    model = _apply_preset_model(preset, body, valves.DEFAULT_MODEL)
    if nsfw_intent:
        model = _apply_nsfw_model_override(
            valves,
            current_model=model,
        )
    full_prompt = _compose_prompt(prompt_core, valves, preset if preset else None)
    if valves.ENGLISH_PROMPTS and not _translation_stack_available() and not _llm_translate_available(
        valves
    ):
        blob = f"{full_prompt}\n{neg}"
        if _NON_ASCII.search(blob):
            yield (
                "**Fordítás (angol prompt) nem elérhető:** állítsd be **OLLAMA_BASE_URL** + **OLLAMA_MODEL**, "
                "**vagy** telepítsd `langdetect` + `deep-translator` "
                "(`pip install langdetect deep-translator`). "
                "Most a szöveg **fordítás nélkül** megy a bridge felé.\n\n"
            )
    fp_en, did_pos = await _ensure_english_async(valves, full_prompt, valves.ENGLISH_PROMPTS)
    neg_en = neg
    did_neg = False
    if neg:
        neg_en, did_neg = await _ensure_english_async(valves, neg, valves.ENGLISH_PROMPTS)
    if did_pos or did_neg:
        src = "LLM" if not _translation_stack_available() else "könyvtár"
        yield f"*A pozitív és/vagy negatív prompt angolra lett fordítva ({src}).*\n\n"

    cfg_extra = _apply_z_image_pipeline_defaults(valves, model, cfg_extra)
    if bundle.get("use_upscale") is False:
        cfg_extra = dict(cfg_extra)
        for k in ("upscaler", "upscalerScaleFactor"):
            cfg_extra.pop(k, None)
    # Csak ha a merge-elt config tényleg kikapcsolná a negatívot (ritka preset / CONFIG_JSON).
    if neg_en and cfg_extra.get("zeroNegativePrompt") is True:
        cfg_extra = _deep_merge(cfg_extra, {"zeroNegativePrompt": False})

    bw = bundle.get("width")
    bh = bundle.get("height")
    if isinstance(bundle.get("size"), str):
        pw, ph = _parse_size(bundle["size"])
        if pw:
            bw = bw or pw
        if ph:
            bh = bh or ph
    pw_p = preset.get("width")
    ph_p = preset.get("height")
    width = _resolve_dim(bw, _resolve_dim(pw_p, valves.WIDTH))
    height = _resolve_dim(bh, _resolve_dim(ph_p, valves.HEIGHT))
    size_err = _validate_size_or_error(bundle=bundle, width=width, height=height)
    if size_err:
        yield size_err
        return

    bundle_g = bundle.get("cfg")
    if bundle_g is None:
        bundle_g = bundle.get("guidance_scale") or bundle.get("guidanceScale")
    preset_g = preset.get("cfg")
    if preset_g is None:
        preset_g = preset.get("guidance_scale") or preset.get("guidanceScale")
    # Feloldás: globális Valves > user JSON (bundle) > stílus preset.
    # A varázsló / kézi ```json``` `steps` / `cfg` felülírja a presetet — különben a manuális lépés (pl. 16) sosem érvényesülne.
    steps_val = _resolve_optional_int(
        valves.STEPS,
        _resolve_optional_int(
            bundle.get("steps"),
            preset.get("steps"),
        ),
    )
    cfg_val = _resolve_optional_float(
        valves.CFG,
        _resolve_optional_float(
            bundle_g,
            preset_g,
        ),
    )
    seed_val = _resolve_optional_int(
        bundle.get("seed"),
        _resolve_optional_int(preset.get("seed"), valves.SEED),
    )
    steps_val = _clamp_steps_for_z_image_pipeline(valves, model, steps_val)
    steps_val = _cap_steps_global_max(valves, steps_val)
    cfg_val = _cap_cfg_for_z_image(valves, model, cfg_val)

    base = valves.BRIDGE_URL.rstrip("/")
    payload = _strip_none_payload(
        {
            "model": model,
            "prompt": fp_en,
            "width": width,
            "height": height,
            "steps": steps_val,
            "cfg": cfg_val,
            "seed": seed_val,
        }
    )
    if neg_en:
        payload["negative_prompt"] = neg_en
    if cfg_extra:
        payload["config_json"] = cfg_extra

    show_params = bool(getattr(valves, "SHOW_GENERATION_PARAMS", True))
    summary = _format_generation_params_md(
        model=model,
        width=width,
        height=height,
        steps=steps_val,
        cfg=cfg_val,
        seed=seed_val,
        neg=neg_en or "",
        show=show_params,
        preset_label=preset_key,
        config_json=cfg_extra if cfg_extra else None,
    )
    stream_emitter_replace = (
        bool(valves.STREAM_PROGRESS)
        and emitter is not None
        and bool(getattr(valves, "STREAM_PROGRESS_USE_EVENT_EMITTER", True))
    )
    summary_prefix = (summary or "") if stream_emitter_replace else ""
    if not stream_emitter_replace:
        if summary:
            yield summary

    try:
        import httpx  # noqa: F401
    except ImportError:
        if valves.STREAM_PROGRESS:
            msg = (
                "**Hiányzik a `httpx` csomag** az Open WebUI szerveren. "
                "Telepítsd: `pip install httpx`, vagy kapcsold ki a **STREAM_PROGRESS**-t "
                "(egyszerű `/generate` marad, progress nélkül)."
            )
            if stream_emitter_replace and summary_prefix:
                ok = await _owui_emit_replace(emitter, summary_prefix + "\n\n" + msg)
                if not ok:
                    yield summary_prefix + "\n\n" + msg
            else:
                yield msg
            return

    if valves.STREAM_PROGRESS:
        gen_t0 = time.monotonic()
        url = base + "/generate/stream"
        last_monotonic = 0.0
        last_bucket = -1
        min_delta = float(
            getattr(valves, "STREAM_PROGRESS_MIN_DELTA", None) or 0.08
        )
        if min_delta < 0.01:
            min_delta = 0.01
        if min_delta > 0.5:
            min_delta = 0.5
        last_bucket = -1
        first_block = True
        use_emitter = stream_emitter_replace
        heartbeat_sec = float(getattr(valves, "STREAM_PROGRESS_HEARTBEAT_SEC", 10.0) or 10.0)
        min_replace_sec = float(
            getattr(valves, "STREAM_PROGRESS_MIN_REPLACE_INTERVAL_SEC", 0.5) or 0.5
        )
        if heartbeat_sec < 1.0:
            heartbeat_sec = 1.0
        # Túl nagy alap (régen 10 s) = emitter módban szinte az egész generálás csak a végén frissült.
        if min_replace_sec < 0.1:
            min_replace_sec = 0.1
        if min_replace_sec > 30.0:
            min_replace_sec = 30.0
        single_msg = bool(getattr(valves, "STREAM_PROGRESS_SINGLE_MESSAGE", False))
        if use_emitter:
            single_msg = False
        last_p = 0.0
        last_cur: int | None = None
        last_tot: int | None = None
        last_line = ""

        def _snap_state() -> tuple[float, int | None, int | None, str]:
            return (
                round(last_p, 4),
                last_cur,
                last_tot,
                (last_line or "").strip()[:240],
            )

        def _full_progress_md(include_title: bool) -> str:
            eta = _progress_eta_suffix(last_p, gen_t0)
            md = _progress_for_valves(
                valves,
                percent_0_1=last_p,
                current=last_cur,
                total=last_tot,
                line=last_line,
                include_title=include_title,
                eta_suffix=eta,
            )
            if summary_prefix:
                return summary_prefix + "\n\n" + md
            return md

        try:
            if use_emitter:
                agen = _iter_sse_events(url, payload).__aiter__()
                progress_seen = False
                progress_emit_count = 0
                last_emit_mono = 0.0
                last_emitted_snap: tuple[float, int | None, int | None, str] | None = None
                starter = _stream_started_placeholder_md(valves, summary_prefix)
                ok_s = await _owui_emit_replace(emitter, starter)
                if not ok_s:
                    yield starter
                last_emit_mono = time.monotonic()
                try:
                    while True:
                        try:
                            ev_name, data = await asyncio.wait_for(
                                agen.__anext__(), timeout=heartbeat_sec
                            )
                        except StopAsyncIteration:
                            break
                        except asyncio.TimeoutError:
                            now = time.monotonic()
                            if not progress_seen:
                                if last_emit_mono > 0 and now - last_emit_mono < min_replace_sec:
                                    continue
                                wait = _stream_waiting_cli_placeholder_md(valves, summary_prefix)
                                ok_w = await _owui_emit_replace(emitter, wait)
                                if not ok_w:
                                    yield wait
                                last_emit_mono = time.monotonic()
                                continue
                            if last_emit_mono > 0 and now - last_emit_mono < min_replace_sec:
                                continue
                            content = _full_progress_md(include_title=False)
                            ok = await _owui_emit_replace(emitter, content)
                            if not ok:
                                yield content
                            last_emit_mono = time.monotonic()
                            continue

                        if ev_name == "progress":
                            progress_seen = True
                            cur = data.get("current")
                            tot = data.get("total")
                            raw = _raw_percent_from_payload(
                                data.get("percent"), cur, tot
                            )
                            last_monotonic = max(last_monotonic, raw)
                            last_p = last_monotonic
                            if isinstance(cur, (int, float)):
                                last_cur = int(cur)
                            if isinstance(tot, (int, float)):
                                last_tot = int(tot)
                            last_line = data.get("line") or ""
                            snap = _snap_state()
                            now = time.monotonic()
                            if progress_emit_count > 0:
                                if now - last_emit_mono < min_replace_sec:
                                    continue
                                if last_emitted_snap is not None and snap == last_emitted_snap:
                                    continue
                            content = _full_progress_md(include_title=first_block)
                            ok = await _owui_emit_replace(emitter, content)
                            if not ok:
                                yield content
                            first_block = False
                            last_emitted_snap = snap
                            last_emit_mono = time.monotonic()
                            progress_emit_count += 1
                        elif ev_name == "done":
                            b64 = data.get("image_base64")
                            if not b64:
                                err = f"Hiányzó kép a válaszból: `{data!r}`"
                                if summary_prefix:
                                    full_e = summary_prefix + "\n\n" + err
                                else:
                                    full_e = err
                                ok = await _owui_emit_replace(emitter, full_e)
                                if not ok:
                                    yield full_e
                                return
                            had_progress = (
                                last_p > 1e-6
                                or last_cur is not None
                                or last_tot is not None
                            )
                            pre = ""
                            if had_progress:
                                pre = _progress_for_valves(
                                    valves,
                                    percent_0_1=last_p,
                                    current=last_cur,
                                    total=last_tot,
                                    line=last_line,
                                    include_title=False,
                                )
                            tail = (
                                "### Kész\n\n"
                                + f"![Draw Things](data:image/png;base64,{b64})"
                            )
                            if pre:
                                final_c = (
                                    (summary_prefix + "\n\n" + pre + "\n\n" + tail)
                                    if summary_prefix
                                    else (pre + "\n\n" + tail)
                                )
                            else:
                                final_c = (
                                    (summary_prefix + "\n\n" + tail)
                                    if summary_prefix
                                    else tail
                                )
                            ok = await _owui_emit_replace(emitter, final_c)
                            if not ok:
                                yield final_c
                            return
                        elif ev_name == "error":
                            err = f"**Bridge hiba:** {data.get('error', data)}"
                            full_e = (
                                (summary_prefix + "\n\n" + err)
                                if summary_prefix
                                else err
                            )
                            ok = await _owui_emit_replace(emitter, full_e)
                            if not ok:
                                yield full_e
                            return
                finally:
                    # SSE stream lezárása minden kilépési ágon (done/error/return), különben nyitva maradhat.
                    ac = getattr(agen, "aclose", None)
                    if callable(ac):
                        try:
                            await ac()
                        except Exception:
                            pass
            else:
                yield "\n\n---\n\n" + _stream_started_placeholder_md(valves, "")
                async for ev_name, data in _iter_sse_events(url, payload):
                    if ev_name == "progress":
                        cur = data.get("current")
                        tot = data.get("total")
                        raw = _raw_percent_from_payload(
                            data.get("percent"), cur, tot
                        )
                        last_monotonic = max(last_monotonic, raw)
                        p = last_monotonic
                        if single_msg:
                            last_p = p
                            if isinstance(cur, (int, float)):
                                last_cur = int(cur)
                            if isinstance(tot, (int, float)):
                                last_tot = int(tot)
                            last_line = data.get("line") or ""
                            first_block = False
                            continue
                        bucket = int(p / min_delta)
                        if not first_block and bucket == last_bucket:
                            continue
                        last_bucket = bucket
                        eta = _progress_eta_suffix(p, gen_t0)
                        md = _progress_for_valves(
                            valves,
                            percent_0_1=p,
                            current=cur,
                            total=tot,
                            line=data.get("line") or "",
                            include_title=first_block,
                            eta_suffix=eta,
                        )
                        first_block = False
                        yield md
                    elif ev_name == "done":
                        b64 = data.get("image_base64")
                        if not b64:
                            yield f"Hiányzó kép a válaszból: `{data!r}`"
                            return
                        had_progress = (
                            last_p > 1e-6
                            or last_cur is not None
                            or last_tot is not None
                        )
                        pre = ""
                        if single_msg and had_progress:
                            pre = _progress_for_valves(
                                valves,
                                percent_0_1=last_p,
                                current=last_cur,
                                total=last_tot,
                                line=last_line,
                                include_title=True,
                            )
                        yield pre + (
                            "### Kész\n\n"
                            + f"![Draw Things](data:image/png;base64,{b64})"
                        )
                        return
                    elif ev_name == "error":
                        yield f"**Bridge hiba:** {data.get('error', data)}"
                        return
        except Exception as e:
            err = f"**Stream hiba:** {e}" + _stream_connection_error_hint(base, e)
            if use_emitter:
                full_e = (summary_prefix + "\n\n" + err) if summary_prefix else err
                ok = await _owui_emit_replace(emitter, full_e)
                if not ok:
                    yield full_e
            else:
                yield err
        return

    if not valves.STREAM_PROGRESS:
        yield "\n\n---\n\n" + _sync_generation_wait_md(valves)

    import requests

    url = base + "/generate"
    try:
        r = requests.post(url, json=payload, timeout=3600)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        yield f"Bridge hiba: {e}"
        return

    b64 = data.get("image_base64")
    if not b64:
        yield f"Válasz: {data!r}"
        return

    prog = data.get("progress") or {}
    pct = prog.get("percent")
    step_info = ""
    if prog.get("current") is not None and prog.get("total"):
        step_info = f"Lépések: {prog['current']}/{prog['total']}"
    elif pct is not None:
        step_info = f"Haladás: {pct:.0%}"

    yield (
        "### Kész\n\n"
        + (f"{step_info}\n\n" if step_info else "")
        + f"![Draw Things](data:image/png;base64,{b64})"
    )


class Pipe:
    class Valves(BaseModel):
        BRIDGE_URL: str = Field(
            default="http://10.0.0.136:8787",
            description="drawthings_bridge URL (vége / nélkül). Ha az OWUI ugyanazon a gépen fut, mint a bridge: `http://127.0.0.1:8787`.",
        )
        DEFAULT_MODEL: str = Field(
            default="z_image_turbo_1.0_q8p.ckpt",
            description="Modell, ha a /models lista nem töltődik vagy nincs a választóban.",
        )
        NEGATIVE_PROMPT: str = Field(
            default=_DEFAULT_NEGATIVE_PROMPT_GLOBAL,
            description=(
                "Globális negatív (minden generálásnál **először**; utána stílus preset + NEGATIVE_BY_* + user). "
                "Üres string = kikapcsolod a globális réteget. Alapértelmezés: minőség + artefakt + alap anatómia — lásd `_DEFAULT_NEGATIVE_PROMPT_GLOBAL` és `NEGATIVE_PROMPT_STRATEGY.md`."
            ),
        )
        STYLE_PREFIX: str = Field(
            default="",
            description="A te promptod **elé** kerül (pl. „masterpiece, best quality, anime style”).",
        )
        STYLE_SUFFIX: str = Field(
            default="",
            description="A te promptod **után** (pl. „soft lighting, detailed”).",
        )
        CONFIG_JSON: str = Field(
            default="",
            description="Haladó: résleges JSGenerationConfiguration JSON (LoRA stb.). Üres = kihagyva.",
        )
        Z_IMAGE_PIPELINE_DEFAULTS: bool = Field(
            default=False,
            description=(
                "Ha **hamis** (alap): **nincs** extra `config_json` a z_image-hez — ugyanaz a viselkedés, mint a CLI-nél "
                "`--config-json` nélkül (turbo modellnél ez gyakran jobb). "
                "Ha **igaz**: **UniPC Trailing** (`sampler`: 17); opcionális refiner+hires: **Z_IMAGE_REFINER_HIRES**. "
                "Preset `config_json` / CONFIG_JSON továbbra is merge-elődik."
            ),
        )
        Z_IMAGE_REFINER_HIRES: bool = Field(
            default=False,
            description=(
                "Ha igaz és **Z_IMAGE_PIPELINE_DEFAULTS** be van kapcsolva: **refinerModel** = ugyanaz a .ckpt, **hiresFix**: true, **refinerStart** (REFINER_START). "
                "Alapból **ki** — sok gépen homályos képet vagy CLI „no tensors” hibát okozott. "
                "Minőségjavításhoz előbb emelj **lépést** / próbáld az **UPSCALER_CKPT**-t külön."
            ),
        )
        REFINER_START: float | None = Field(
            default=0.75,
            description="`refinerStart` (0…1), ha **Z_IMAGE_REFINER_HIRES** be van kapcsolva.",
        )
        Z_IMAGE_MIN_STEPS: int | None = Field(
            default=12,
            description=(
                "Ha **Z_IMAGE_REFINER_HIRES** vagy **UPSCALER_CKPT** aktív: legalább ennyi lépés (`z_image`), "
                "**kivéve** explicit **Valves STEPS**. Egyszerű (csak UniPC) módnál nincs clamp. "
                "Cél: CLI „no tensors” hibák csökkentése. **0** = clamp kikapcsolva."
            ),
        )
        UPSCALER_CKPT: str = Field(
            default="",
            description=(
                "Post-**upscaler** `.ckpt` — **alapból üres** (stabil generálás; az upscaler+config néha hibát / lassulást okoz). "
                "Ha kell 2×: pl. `"
                + _DEFAULT_UPSCALER_CKPT
                + "` — **UPSCALER_SCALE_FACTOR**. Lista: bridge `GET {BRIDGE_URL}/upscalers`."
            ),
        )
        UPSCALER_SCALE_FACTOR: float = Field(
            default=2.0,
            description="`upscalerScaleFactor` — alapból **2.0** (2×). A 4×-es RealESRGAN fájlokhoz állítsd pl. 4.0-ra.",
        )
        STRIP_IMAGE_PREFIX: bool = Field(
            default=True,
            description="Levágja a „generálj képet” / generate image elejét.",
        )
        REQUIRE_EXPLICIT_IMAGE_REQUEST: bool = Field(
            default=True,
            description="Ha igaz: közvetlen generálás csak ha a szál user üzeneteiben van explicit képkérés (IMAGE_REQUEST_REGEX), vagy beillesztett JSON, vagy TRIGGER_REGEX a legutóbbi üzeneten; a kép-varázsló ugyanígy — nem elég, hogy korábban volt assistant válasz. Általános beszélgetés: WIZARD_GENERAL_CHAT_WHEN_NO_IMAGE_INTENT. A varázsló JSON utáni kép nem esik ide.",
        )
        IMAGE_REQUEST_REGEX: str = Field(
            default=r"(?i)(készíts|keszits|generálj|készítsd|keszitsd|rajzolj|mutass|alkoss|renderelj|illusztrálj|illusztralj|fess|készíts\s+nekem|keszits\s+nekem)[\s\S]{0,48}?(képet|kepet|képnek|kepnek|image|picture|drawing|illusztráció|illusztracio)|(képet|kepet)\s+(kérek|kérek|akarok|néznék|mutass|legyen)|\b(draw|generate|create|make|paint)\s+(an?\s+)?(image|picture|illustration|drawing)\b",
            description="Explicit képkérés (user üzenetek összefűzve). A Pipe előtte normalizál (pl. generälj→generálj, kepet→képet). Üres = ne szűrj így.",
        )
        STREAM_PROGRESS: bool = Field(
            default=True,
            description="Ha **igaz** (alap): /generate/stream (SSE) + élő progress + ETA (**UPSCALER_CKPT** hagyjad üresen, ha gond van). **Hamis**: egy `/generate` POST — előtte *„Képgenerálás folyamatban”* üzenet + végén kép.",
        )
        STREAM_PROGRESS_UI: str = Field(
            default="ring",
            description='Progress megjelenés: "ring" = SVG körvonal + % (kompakt); "bar" = régi ASCII sáv.',
        )
        STREAM_PROGRESS_MIN_DELTA: float = Field(
            default=0.08,
            description="Min. haladás két chat-frissítés között (0.01–0.5; alap ~8%). Nagyobb = kevesebb sor, kevesebb duplázott gyűrű.",
        )
        STREAM_PROGRESS_SINGLE_MESSAGE: bool = Field(
            default=False,
            description="Ha **hamis** (alap): **élő** progress-gyűrű a generálás alatt. Ha **igaz**: csak a végén **egy** progress + kép (kevesebb blokk, de nincs közbeni frissítés). **STREAM_PROGRESS_USE_EVENT_EMITTER** bekapcsolva: ez figyelmen kívül marad (mindig élő replace).",
        )
        STREAM_PROGRESS_USE_EVENT_EMITTER: bool = Field(
            default=True,
            description="Ha **igaz** és az Open WebUI ad **`__event_emitter__`**-t: a progress **ugyanabban a buborékban** frissül (`replace`), nem új soronként. Ha **hamis** vagy nincs emitter: régi viselkedés (yield).",
        )
        STREAM_PROGRESS_HEARTBEAT_SEC: float = Field(
            default=10.0,
            description="SSE nélkül ennyi másodperc után „ébresztő” (max. ennyi ideig vár a következő eseményre). Min. 1.",
        )
        STREAM_PROGRESS_MIN_REPLACE_INTERVAL_SEC: float = Field(
            default=0.5,
            description=(
                "Minimum idő két `replace` frissítés között (Open WebUI **emitter** módban). "
                "Ha túl nagy (pl. 10 s), a gyűrű „későn jön”: csak az első és a kész kép előtti pillanatban látszik változás. "
                "Élő visszajelzéshez: **0,3–0,8** s; a heartbeat is ehhez igazodik. Első progress esemény azonnal megjelenhet. Min. 0,1."
            ),
        )
        WIDTH: int | None = Field(default=None)
        HEIGHT: int | None = Field(default=None)
        STEPS: int | None = Field(
            default=None,
            description="Globális lépésszám — **felülírja** mindent (preset + user JSON). Ha üres: **user JSON / bundle** `steps` > **stílus preset**.",
        )
        MAX_STEPS: int = Field(
            default=22,
            description="Globális hard limit a végső steps értékre (alap 22).",
        )
        CFG: float | None = Field(
            default=None,
            description="Globális CFG — **felülírja** mindent (preset + user JSON). Ha üres: **user JSON** `cfg` / `guidanceScale` > **stílus preset**.",
        )
        Z_IMAGE_CFG_AUTO_CAP: bool = Field(
            default=True,
            description=(
                "Ha **igaz** (alap): **z_image** / **zimageturbo** .ckpt-nél a végső CFG mindig "
                "**Z_IMAGE_CFG_MIN** és **Z_IMAGE_CFG_MAX** közé esik (alap 0.8–1.2), a preset/JSON magas értékeit is. "
                "Ha **hamis**: a kért cfg változatlan (haladó / saját felelősség)."
            ),
        )
        Z_IMAGE_CFG_MIN: float = Field(
            default=0.8,
            description="z_image CFG alsó határ, ha **Z_IMAGE_CFG_AUTO_CAP** igaz (ajánlott 0.8).",
        )
        Z_IMAGE_CFG_MAX: float = Field(
            default=1.2,
            description="z_image CFG felső határ, ha **Z_IMAGE_CFG_AUTO_CAP** igaz (ajánlott 1.2).",
        )
        Z_IMAGE_PRESET_TUNING: bool = Field(
            default=False,
            description=(
                "Ha **igaz**: z_image stílus-preset steps/cfg a Valves Z_IMAGE_PRESET_STEPS_MIN/MAX és Z_IMAGE_PRESET_CFG_MAX szerint igazítva (turbo-stabil). "
                "Ha **hamis** (alap): a STYLE_PRESETS_JSON / beágyazott preset számai változatlanok."
            ),
        )
        Z_IMAGE_PRESET_STEPS_MIN: int = Field(
            default=6,
            description="z_image preset minimum steps (ajánlott 6).",
        )
        Z_IMAGE_PRESET_STEPS_MAX: int = Field(
            default=9,
            description="z_image preset maximum steps (ajánlott 9).",
        )
        Z_IMAGE_PRESET_STEPS_DEFAULT: int = Field(
            default=8,
            description="z_image preset default steps, ha hiányzik/hibás (ajánlott 8).",
        )
        Z_IMAGE_PRESET_STEPS_MIN_PHOTO: int = Field(
            default=8,
            description="Fotó/ultra-real z_image preset minimum steps (ajánlott 8).",
        )
        Z_IMAGE_PRESET_STEPS_MAX_PHOTO: int = Field(
            default=18,
            description="Fotó/ultra-real z_image preset maximum steps (ajánlott 16–18).",
        )
        Z_IMAGE_PRESET_STEPS_DEFAULT_PHOTO: int = Field(
            default=12,
            description="Fotó/ultra-real z_image preset default steps (ajánlott 12).",
        )
        Z_IMAGE_PRESET_CFG_DEFAULT: float = Field(
            default=1.0,
            description="z_image preset default CFG, ha hiányzik/hibás (ajánlott 1.0).",
        )
        Z_IMAGE_PRESET_CFG_MAX: float = Field(
            default=1.2,
            description="z_image preset CFG maximum (ajánlott 1.1–1.2).",
        )
        SEED: int | None = Field(
            default=None,
            description="Fix seed (reprodukálható kép). Üres = véletlen. A JSON `seed` felülírja.",
        )
        SHOW_GENERATION_PARAMS: bool = Field(
            default=True,
            description="Generálás előtt mutassa a chatben: .ckpt, méret, steps, CFG, seed, negatív prompt.",
        )
        TRIGGER_MODE: str = Field(
            default="off",
            description="off = minden üzenet indul; optional = trigger nélkül is (teljes szöveg=prompt); required = csak TRIGGER_REGEX-re indul.",
        )
        TRIGGER_REGEX: str = Field(
            default=r"(?i)(kész[\s,]*mehet|^\s*mehet\s*$|indítsd?|generálj\s+most|kész\s+vagyok|^go\s*$|^\s*start\s*$)",
            description="Ha illeszkedik: generálás (required/optional). Állítsd a saját kulcsszavaidra.",
        )
        MERGE_HISTORY_ON_SHORT_TRIGGER: bool = Field(
            default=True,
            description="Ha a legutóbbi üzenet csak trigger + kevés szöveg: az előző user üzenetek is bekerülnek (Gemma-blokk külön üzenetben).",
        )
        NEGATIVE_BY_STYLE_JSON: str = Field(
            default="{}",
            description='Kulcsszó → negatív részlet, pl. {"anime":"bad anatomy, extra limbs","photo":"cartoon, drawing"}. A stílus/téma/prompt szövegben keres.',
        )
        NEGATIVE_BY_THEME_JSON: str = Field(
            default="{}",
            description="Ugyanígy téma kulcsokhoz (pl. portrait, landscape).",
        )
        LORA_BY_STYLE_JSON: str = Field(
            default="{}",
            description='Stílus kulcsszó → részleges config_json (dict), pl. {"anime":{"loras":[...]}} — deep merge a CONFIG_JSON-szal.',
        )
        NSFW_INTENT_REGEX: str = Field(
            default=r"(?i)\b(nsfw|hentai|porn|explicit|nude|naked|sex|anal|blowjob|cum|creampie|boobs?|tits?|nipples?|pussy|vagina|penis|dick|cock|fetish|bondage|shibari|meztele[n]?|meztelen|csocs|mellbimbo)\b",
            description="NSFW szándék felismerés. Ha találat van, a Pipe NSFW modellt használ.",
        )
        NSFW_PROMPT_ONLY: bool = Field(
            default=True,
            description="Ha igaz (alap): NSFW ellenőrzés csak a pozitív prompton fut (nem a negatívon/témán), így kevesebb a téves NSFW váltás.",
        )
        NSFW_MODEL_DEFAULT: str = Field(
            default=_DEFAULT_NSFW_MODEL,
            description="NSFW fallback .ckpt, ha nincs stílushoz külön NSFW modell megadva.",
        )
        NSFW_MODEL_BY_STYLE_JSON: str = Field(
            default="{}",
            description=(
                "Korábbi kompatibilitási mező (stílusfüggő NSFW modellhez). "
                "Az aktuális működés egységes: minden NSFW kérés a **NSFW_MODEL_DEFAULT** modellt használja."
            ),
        )
        STYLE_PRESETS_JSON: str = Field(
            default="{}",
            description=(
                "Stílus-kulcs → preset JSON. **Üres vagy `{}` = a Pipe beágyazott listája** (ugyanaz, mint korábban a fájlban). "
                "A `style_label` / varázsló JSON egyeztetése **normalizálja** a szóközt és aláhúzást (pl. `Digitalis_festmeny` ≈ `Digitalis festmeny`). "
                "Saját listához illeszd be a teljes JSON-t. Beágyazott kulcsok: Anime, Fotorealisztikus, … — az **nsfw** preset külön checkpoint: `zimageturbonsfw_45bf16diffusion_f16.ckpt`, a többi alapból `z_image_turbo_1.0_q8p.ckpt`. "
                "Mezők: `model`, `steps`, `cfg`, `negative_prompt`, `style_prefix`/`style_suffix`, `config_json`. "
                "A beágyazott lista stílusonként 8–20 lépés és ~2–6 CFG körül van — ezek csak akkor módosulnak, ha be van kapcsolva **Z_IMAGE_PRESET_TUNING**. "
                "Feloldás: **Valves STEPS/CFG** > **user JSON / bundle** (`steps` / `cfg` / `guidanceScale`) > **stílus preset**. "
                "Végső CFG (z_image): ha **Z_IMAGE_CFG_AUTO_CAP** be van kapcsolva (alap), **Z_IMAGE_CFG_MIN**–**Z_IMAGE_CFG_MAX** (alap 0.8–1.2). "
                "z_image turbo: alapból **ne** erőltesd a Pipe pipeline-t (**Z_IMAGE_PIPELINE_DEFAULTS** hamis = CLI-szerű); UniPC: kapcsold be + **Z_IMAGE_REFINER_HIRES** ha kell."
            ),
        )
        ENGLISH_PROMPTS: bool = Field(
            default=True,
            description="Ha igaz: pozitív és negatív prompt angolra — **először LLM** (**OLLAMA_MODEL** + **OLLAMA_BASE_URL**), ha van; különben langdetect+deep-translator (pip); egyik sincs: változatlan.",
        )
        WIZARD_OLLAMA_CHAT: bool = Field(
            default=True,
            description=(
                "Ha igaz és van OLLAMA_MODEL + OLLAMA_BASE_URL: kép-varázsló mód. "
                "A lépések (stílus → prompt → méret → megerősítés → kép) **szabály-alapúak**, LM Studio stream **nem** kell hozzájuk. "
                "Az LLM **fordításra** (ENGLISH_PROMPTS) és **általános chatre** (ha nincs képszándék) marad."
            ),
        )
        WIZARD_DETERMINISTIC_FLOW: bool = Field(
            default=False,
            description=(
                "**Kompatibilitási mező (jelenleg nem vált ágat):** a kép-varázsló mindig üzenetparsolásos. "
                "Régi konfigokban maradhat; figyelmen kívül hagyható."
            ),
        )
        WIZARD_CHAT_BACKEND: str = Field(
            default="ollama",
            description='Varázsló LLM: "ollama" = /api/chat (:11434); "openai" = /v1/chat/completions (LM Studio). Ha az URL-ben szerepel :1234 vagy /v1, a Pipe **openai**-t használ (nem kell kézzel váltani).',
        )
        OLLAMA_BASE_URL: str = Field(
            default="http://127.0.0.1:11434",
            description="Ollama: http://127.0.0.1:11434. LM Studio: **kötelező a :1234** a címben, pl. http://10.0.0.1:1234/v1 — DDNS-nél ne `http://ddns.net/v1` (:80), hanem `http://ddns.net:1234/v1` + router portforward.",
        )
        WIZARD_API_KEY: str = Field(
            default="",
            description="LM Studio: kötelező Bearer token (Local Server → Developer → API key). Alternatíva: az OWUI szerver környezetében LM_API_TOKEN. Ollama módnál üres.",
        )
        OLLAMA_MODEL: str = Field(
            default="",
            description="Ollama: pl. gemma3:4b. LM Studio: a betöltött modell neve a szerver szerint. Üres = varázsló ki.",
        )
        WIZARD_SYSTEM_PROMPT: str = Field(
            default="",
            description=(
                "A **régi** LLM-varázsló system prompt szövege (referencia). **Üres** = a Pipe beágyazott magyar szövege: "
                "`{{STYLE_PRESET_LIST}}`, `{{WIZARD_SIZE_TABLE}}`. A futó varázsló **nem** küldi ezt a modellnek — "
                "a lépések szabály-alapúak; a mező saját dokumentációhoz / másoláshoz maradhat."
            ),
        )
        WIZARD_GENERAL_CHAT_WHEN_NO_IMAGE_INTENT: bool = Field(
            default=True,
            description=(
                "Ha igaz és REQUIRE_EXPLICIT_IMAGE_REQUEST miatt nem indul a kép-varázsló: ugyanaz az LLM (OLLAMA_MODEL) "
                "általános beszélgetésként válaszol (GENERAL_CHAT_SYSTEM_PROMPT). Ha hamis: rövid tájékoztató üzenet, LLM hívás nélkül."
            ),
        )
        GENERAL_CHAT_SYSTEM_PROMPT: str = Field(
            default="",
            description="Üres = beágyazott magyar általános chat system prompt; csak ha WIZARD_GENERAL_CHAT_WHEN_NO_IMAGE_INTENT és nincs explicit képszándék.",
        )

    def __init__(self):
        self.valves = self.Valves()

    def pipes(self):
        """Modellválasztó: letöltött .ckpt lista a bridge-től (interaktív)."""
        import requests

        out: list[dict[str, str]] = []
        base = self.valves.BRIDGE_URL.rstrip("/")
        # Első helyen: egy név, hogy ne csak „új .ckpt modellek” jelenjenek meg külön képgenerálónak.
        out.append(
            {
                "id": PIPE_DEFAULT_MODEL_SENTINEL,
                "name": "🖼 Draw Things + beszélgetés (varázsló → JSON → kép)",
            }
        )
        try:
            r = requests.get(
                f"{base}/models",
                params={"downloaded_only": True},
                timeout=12,
            )
            r.raise_for_status()
            for m in r.json().get("models", []):
                fid = m.get("file") or m.get("id")
                if not fid or not str(fid).endswith(".ckpt"):
                    continue
                label = m.get("name", fid)
                out.append({"id": fid, "name": f"🖼 {label}"})
        except Exception:
            pass
        if not out:
            d = self.valves.DEFAULT_MODEL
            out = [{"id": d, "name": f"🖼 {d}"}]
        return out

    async def pipe(self, body: dict, __user__: dict | None = None, **kwargs: Any):
        emitter = kwargs.get("__event_emitter__")
        mode = (self.valves.TRIGGER_MODE or "off").strip().lower()
        if mode not in ("off", "optional", "required"):
            mode = "off"

        tre_raw = (self.valves.TRIGGER_REGEX or "").strip()
        try:
            tre = re.compile(tre_raw, re.IGNORECASE | re.UNICODE) if tre_raw else None
        except re.error:
            tre = None

        if mode == "off":
            raw_text = _last_user_text(body)
        else:
            raw_text = _merged_user_text_for_parse(
                body,
                tre_raw if tre else "",
                bool(self.valves.MERGE_HISTORY_ON_SHORT_TRIGGER),
            )

        if not raw_text:
            yield "Nincs felhasználói üzenet (prompt)."
            return

        json_ready = _is_ready_generate_json(raw_text)
        if (
            _wizard_ollama_enabled(self.valves)
            and not json_ready
        ):
            if not _wizard_entry_allowed(
                self.valves, body, raw_text, json_ready, tre
            ):
                if getattr(self.valves, "WIZARD_GENERAL_CHAT_WHEN_NO_IMAGE_INTENT", True):
                    sys_g = _load_general_chat_system_prompt(self.valves)
                    if not _owui_messages_for_ollama(body, sys_g):
                        yield "Nincs üzenet a varázsló LLM számára."
                        return
                    buf_gc: list[str] = []
                    async for ch in _async_stream_wizard_llm(self.valves, body, sys_g):
                        buf_gc.append(ch)
                        yield ch
                    if not "".join(buf_gc).strip():
                        one = await _wizard_chat_completion_once(self.valves, body, sys_g)
                        if one:
                            yield one
                        else:
                            yield _WIZARD_EMPTY_LLM_REPLY_HU
                    return
                yield (
                    "Üdv! A **PicGEN** csatorna képgeneráláshoz van beállítva. "
                    "Ha képet szeretnél, írd például: **„Generálj képet”** vagy **„Készíts képet: …”** — "
                    "akkor elindul a varázsló. Ha csak beszélgetnél, válassz másik modellt, vagy add meg a képkérést."
                )
                return
            last_user = _last_user_text(body)
            if _wizard_should_force_style_step(last_user):
                # Stabil indulás: az első körben mindig ugyanaz a stílus-táblás kérdés.
                # Csak az utolsó user üzenet (ne összefűzött history), különben „generálj képet”
                # vagy trigger-merge miatt újranyílhat a tábla hosszú prompt közben.
                yield _wizard_static_style_step_md(self.valves)
                return
            # Szabály-alapú varázsló (LLM stream nélkül) — LM Studio üres válasza nem állítja meg a folyamatot.
            async for part in _async_wizard_rule_based_wizard(
                self, self.valves, body, emitter, last_user
            ):
                yield part
            return

        if tre and mode == "required" and not tre.search(raw_text) and not json_ready:
            yield _help_trigger_hu(self.valves)
            return

        if not _explicit_image_intent_ok(self.valves, body, raw_text, json_ready, tre):
            yield (
                "**Nincs explicit képkérés** a beszélgetésben. Példa: **„Készíts képet: …”**, **„Generálj képet”**, "
                "**„Rajzolj nekem…”** — vagy angolul *generate an image*, *draw a picture*. "
                "Alternatíva: **varázsló** + JSON, **```json```** blokk beillesztése, vagy a **TRIGGER_REGEX** szerinti szó (pl. KÉSZ MEHET). "
                "Kikapcsolás: Valves **REQUIRE_EXPLICIT_IMAGE_REQUEST** = false."
            )
            return

        if tre and mode in ("optional", "required") and tre.search(raw_text):
            text_for_parse = tre.sub("", raw_text).strip()
        else:
            text_for_parse = raw_text.strip()

        async for part in _run_generate_after_parse(self, body, text_for_parse, emitter):
            yield part
