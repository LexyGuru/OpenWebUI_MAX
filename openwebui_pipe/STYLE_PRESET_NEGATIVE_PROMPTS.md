# Beépített stílus presetek — negatív promptok

Forrás: `openwebui_pipe/drawthings_bridge_pipe.py` — `_EMBEDDED_STYLE_PRESETS_JSON` + `_EMBEDDED_EXTRA_STYLE_PRESETS`.
A Valves **`NEGATIVE_PROMPT`** (alapból `_DEFAULT_NEGATIVE_PROMPT_GLOBAL`) **előtte** hozzáfűződik — a teljes negatív = globális + preset + NEGATIVE_BY_* + user.
Ha a Pipe **STYLE_PRESETS_JSON** Valves mezőjében saját JSON van, ez a lista **nem** tükrözi azt.

**Összesen 25 stílus** (ábécé szerint).

---

## `3D_CGI`

bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed feet, deformed face, deformed body, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, low poly errors, z-fighting, broken normals, melted mesh, duplicate vertices chaos, uncanny rigging, clipping through body, jpeg artifacts, watermark, text, worst quality, low quality, flat shading where subsurface needed, amateur sculpt

---

## `Anime`

bad anatomy, bad proportions, malformed limbs, extra limbs, missing limbs, extra fingers, missing fingers, fused fingers, too many fingers, wrong finger count, deformed hands, deformed feet, deformed face, deformed body, disproportionate limbs, long neck, long torso, floating limbs, disconnected limbs, broken pose, twisted joints, anatomical nonsense, duplicate body parts, extra arms, missing arms, bad eyes, asymmetrical eyes, cross-eyed, swollen face, lowres, blurry, out of focus, jpeg artifacts, watermark, signature, text, username, worst quality, low quality, cropped, amateur, oversaturated, flat shading, muddy colors, western cartoon, 3d render, photorealistic skin

---

## `Architectural_render`

warped perspective, warped verticals, inconsistent vanishing point, lens distortion, melted walls, deformed structures, floating geometry, duplicate facades, structural nonsense, collapsed perspective, lowres, blurry, text, watermark

---

## `Concept_art`

muddy colors, low detail, bad anatomy, broken perspective, blurry, watermark, text, incoherent scale, floating rocks without intent, muddy focal point, unclear silhouette, cluttered focal area, duplicate horizon, inconsistent atmospheric perspective

---

## `Cyberpunk`

bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed feet, deformed face, deformed body, long neck, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, duplicate body parts, bad eyes, asymmetrical eyes, neon banding, jpeg artifacts, watermark, text, worst quality, low quality, muddy colors, amateur, broken perspective, inconsistent scale, floating objects without intent, cluttered unreadable silhouette

---

## `Dark_fantasy`

bright cheerful palette, pastel cute, flat lighting, family friendly illustration, washed out horror, contradictory mood, bad anatomy, deformed hands, lowres, text, watermark

---

## `Digitalis_festmeny`

bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed feet, deformed face, deformed body, long neck, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, muddy details, noise, grain overload, jpeg artifacts, watermark, text, worst quality, low quality, flat shading only, amateur, broken perspective, duplicate limbs, asymmetrical face errors, plastic skin, uncanny

---

## `Fantasy`

bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed feet, deformed face, deformed body, long neck, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, duplicate limbs, bad eyes, asymmetrical eyes, cross-eyed, swollen face, muddy textures, jpeg artifacts, watermark, text, worst quality, low quality, modern objects in scene, inconsistent lighting, amateur, broken perspective, melting features

---

## `Fashion_editorial`

bad anatomy, deformed hands, warped limbs, plastic skin, wax skin, double face, asymmetrical eyes, over-smoothing, cheap hdr, muddy skin, blurry, lowres, text, watermark, cheap lighting

---

## `Film_noir`

flat lighting, oversaturated colors, modern bright palette, hdr bloom, pastel colors, bright daylight, clean sitcom lighting, led panel look, lowres, blurry, bad anatomy, deformed face, text, watermark

---

## `Food_photography`

plastic food look, synthetic props, unappetizing, mold, slime, hair in food, dirty plate, fly, greasy lens, bad textures, blurry, low detail, messy framing, text, watermark

---

## `Fotorealisztikus`

bad anatomy, bad proportions, malformed limbs, extra limbs, missing limbs, extra fingers, missing fingers, fused fingers, too many fingers, deformed hands, deformed feet, deformed face, deformed body, disproportionate limbs, long neck, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, plastic skin, wax skin, doll face, uncanny valley, asymmetrical eyes, cross-eyed, swollen face, skin blemishes artifacts, motion blur, out of focus, jpeg artifacts, watermark, signature, text, worst quality, low quality, oversharpened, oversaturated, cartoon, anime, illustration, painting, 3d render, cgi, duplicate, clone face

---

## `Ink_comic`

bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed face, deformed body, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, messy ink blobs, unreadable silhouette, jpeg artifacts, watermark, text, worst quality, low quality, muddy grays, broken panel composition, duplicate characters, asymmetrical face errors

---

## `Isometric`

wrong perspective, broken geometry, warped lines, off-axis view, incoherent tile grid, jumbled scale, duplicate modules, cluttered silhouette, blurry, text, watermark, cluttered scene

---

## `Landscape`

bad perspective, warped horizon, duplicated mountains, melting terrain, floating rocks without intent, inconsistent scale, tiny figures with bad anatomy, extra limbs on people, malformed animals, jpeg artifacts, watermark, text, worst quality, low quality, muddy details, banding, chromatic aberration, oversharpened, amateur composition, incoherent vanishing point, duplicate elements

---

## `Manga_fekete_feher`

color bleed, grayscale banding, halftone errors, smeared inks, muddy screentone, color leak, blurry, lowres, jpeg artifacts, watermark, text, bad anatomy, deformed hands, extra fingers

---

## `Minimal_flat`

bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed face, deformed body, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, clutter, noise, grain, jpeg artifacts, watermark, text, worst quality, low quality, gradients where flat required, 3d shading, photorealistic texture, busy background, duplicate elements, asymmetry errors on faces

---

## `nsfw`

child, minor, underage, school uniform suggestive minor, bad anatomy, bad proportions, malformed limbs, extra limbs, missing limbs, extra fingers, missing fingers, fused fingers, too many fingers, deformed hands, deformed feet, deformed face, deformed body, disproportionate limbs, long neck, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, duplicate body parts, worst quality, low quality, blurry, jpeg artifacts, watermark, signature, text, username, censored bar, mosaic censor, disfigured, mutation, extra heads

---

## `Pixel_art`

photorealistic texture, smooth gradients, anti-aliased blur, bilinear smear, interpolated pixels, wrong aspect ratio blocks, subpixel blur, lowres mush, text, watermark, bad anatomy

---

## `Portrait`

bad anatomy, bad proportions, malformed face, deformed face, asymmetrical eyes, cross-eyed, misaligned eyes, swollen face, bad teeth, extra fingers, missing fingers, fused fingers, deformed hands, long neck, double face, duplicate face, plastic skin, wax skin, uncanny, jpeg artifacts, watermark, text, worst quality, low quality, out of focus, motion blur, cropped head, extra limbs, disconnected neck, anatomical nonsense, muddy skin texture

---

## `Sci-Fi`

bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed feet, deformed face, deformed body, long neck, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, duplicate body parts, bad eyes, asymmetrical eyes, incoherent spacesuit seams, melting helmet, jpeg artifacts, watermark, text, worst quality, low quality, muddy metal, amateur, broken perspective, inconsistent scale, duplicate modules

---

## `Termek_foto`

bad anatomy, deformed hands holding product, extra fingers, malformed limbs, floating product, warped product, duplicate products, melted plastic, wrong perspective, jpeg artifacts, watermark, text, worst quality, low quality, busy background, clutter, dirty lens, chromatic aberration, banding, amateur product shot, inconsistent shadows

---

## `Vazlat_ceruza`

bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed face, deformed body, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, smudged beyond recognition, heavy blur, jpeg artifacts, watermark, text, worst quality, low quality, full color where sketch requested, 3d render, photo, digital painting finish, duplicate strokes chaos, unreadable hands

---

## `Vintage_film`

modern digital oversharp, neon oversaturation, plastic skin, hdr halos, ai gloss, clean phone photo look, lowres, text, watermark

---

## `Vizfestek`

bad anatomy, bad proportions, malformed limbs, extra limbs, extra fingers, missing fingers, fused fingers, deformed hands, deformed face, deformed body, floating limbs, disconnected limbs, twisted joints, anatomical nonsense, muddy face, muddy hands, digital oversharpen, plastic, wax, 3d, cgi, vector, flat clipart, jpeg artifacts, watermark, text, worst quality, low quality, oversaturated, harsh edges, posterization, banding, chromatic aberration, duplicate subject

---
