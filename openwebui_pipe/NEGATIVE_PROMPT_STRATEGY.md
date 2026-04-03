<!--
  Copyright (c) 2026 Miklos Lekszikov
  SPDX-License-Identifier: MIT
-->

# Negatív promptok bővítése — stratégia (Draw Things / z_image)

**Megvalósítás a Pipe-ban:** a Valves **`NEGATIVE_PROMPT`** alapértelmezése = `_DEFAULT_NEGATIVE_PROMPT_GLOBAL` a `drawthings_bridge_pipe.py` elején; a rövidebb **extra** stílusok (`_EMBEDDED_EXTRA_STYLE_PRESETS`) kiegészültek stílus-specifikus tagokkal.

A cél **nem** egy végtelen szótár, hanem **rétegezett** negatív: kevesebb ellentmondás, kevesebb „minden tiltása minden stílusnál”.

## Miért nem lehet „az összes hibát” kiszűrni?

- A modell **nem** egy szabálylista**: a negatív prompt **súlyozott jelzés**, nem programozott tiltás.
- **z_image / turbo** + alacsony **CFG** mellett a negatív **gyengébben** hat, mint a régi SD1.5/SDXL magas CFG-nél — túl hosszú lista gyakran **kevesebbet** segít, mint egy rövid, célzott.
- **Ellentmondások**: pl. egyszerre tiltani a „flat shading”-et és a „túl sok gradienst” összezavarhatja a modellt.
- **Stílus-specifikus** hibák mások: tájképnél fontos a perspektíva, portrénál az arc, pixel artnál a simítás.

## Rétegek (ajánlott sorrend a Pipe-ban)

1. **`NEGATIVE_PROMPT` (Valves)** — globális „minőség + artefakt + általános anatómia”, **minden** generálásnál hozzáadódik.  
   Ide tedd a **közös** részt (lásd alább), ne minden stílushoz másolva.
2. **Stílus preset `negative_prompt`** — csak ami **ellentétes** a kívánt megjelenéssel (pl. anime preset: „photorealistic skin”).
3. **`NEGATIVE_BY_STYLE_JSON` / `NEGATIVE_BY_THEME_JSON`** — ritka finomhangolás kulcsszó alapján.
4. **User / varázsló JSON `negative_prompt`** — konkrét kérés (pl. „no glasses”).

Így nem duplázod 25× ugyanazt a hosszú blokkot, és egy helyen frissíthető.

## Kategóriák — mit érdemes lefedni (checklist)

| Kategória | Példa kifejezések | Megjegyzés |
|-----------|-------------------|------------|
| **Anatómia (ember)** | extra fingers, fused fingers, deformed hands, bad anatomy, asymmetrical face | Portré / karakter stílusoknál fontos |
| **Arc / szem** | cross-eyed, misaligned eyes, swollen face, asymmetrical eyes | Portré, közeli kép |
| **Kompozíció** | cropped, cut off, out of frame, bad composition | Ha gyakori a levágás |
| **Minőség** | worst quality, low quality, blurry, jpeg artifacts, noise, banding | Közös alap |
| **Szöveg / logó** | watermark, signature, text, logo, username | Vigyázat: néha a kép „feliratot” kér — ne tiltsd globálisan, ha kell szöveg a jelenetben |
| **Fény / expozíció** | overexposed, underexposed, harsh lighting, blown highlights | Fotó / termék |
| **Szín** | oversaturated, muddy colors, color cast (ha zavar) | Stílusfüggő |
| **3D / CGI** | z-fighting, low poly, melted mesh | Már részben a `3D_CGI` presetben |
| **Táj / perspektíva** | warped horizon, bad perspective | `Landscape`, architektúra |
| **Biztonság (NSFW preset)** | külön lista — a `nsfw` presetben már van gyerek / minor tiltás; ezt ne másold szét minden stílusra |

## Mit ne tegyél mindenhova

- **Túl általános „tilt mindent”** (pl. „bad art”) — nem specifikus, kevés a haszna.
- **Ellentét a stílussal**: pixel artnál ne tiltsd túl agresszívan a „blur”-t, ha a retro mosás része lehet.
- **Ismétlés**: ugyanaz a szinonima 5× (extra fingers, too many fingers, six fingers…) — elég 1–2 erős forma.

## Gyakorlati „univerzális alap” (Valves `NEGATIVE_PROMPT` példa)

Ezt **egy helyen** (Valves) érdemes tartani; a presetek maradjanak rövidebbek, stílus-specifikusak:

```
worst quality, low quality, blurry, jpeg artifacts, watermark, signature, bad anatomy,
deformed hands, extra fingers, fused fingers, cropped subject, out of focus
```

Szükség szerint bővíthető: `chromatic aberration`, `banding`, `noise`, `motion blur` (ha gyakori a hiba).

## Ha egy stílus preset túl rövid (pl. `Concept_art`, `Architectural_render`)

Ne másold be a teljes 30 soros anime listát — tegyél hozzá **csak** a stílushoz illő hiányzókat:

- **Concept art**: `incoherent scale`, `floating rocks`, `muddy focal point` stb.
- **Architectural**: `lens distortion`, `warped verticals`, `inconsistent vanishing point` (ha még mindig gond van).

## Összegzés

- **„Minden hiba”** helyett: **globális rövid alap** (Valves) + **stílusonként** 5–15 célzott tag.
- **Mérj**: egy-egy bővítés után nézd a kimenetet; ha nem javul, a tagot vedd ki (zaj a promptban).
- **CFG** és **lépés** többet érhet, mint a negatív második oldala — a turbo modelleknél ez különösen igaz.

---

*Kapcsolódó: `STYLE_PRESET_NEGATIVE_PROMPTS.md` — jelenlegi beépített preset szövegek.*
