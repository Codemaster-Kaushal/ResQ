# Phase 4 — Multimodal Image Analysis + Text/Image Fusion

## Overview

Phase 4 adds image analysis capability to the RescueNet AI Engine.
When an incident report includes an image, the engine can analyze it for visual
signals (flood water, fire, people visible, structural damage, etc.) and fuse
those findings with the text-based severity score for a more accurate result.

---

## Architecture

```
IncidentAIInput
    ↓
TriagePipeline
    ├── Text analysis  (Phase 0–3)
    │       ├── Classification
    │       ├── Risk extraction
    │       └── Severity scoring
    ├── Image preprocessing  (ai_engine/vision/preprocessing.py)
    │       ├── validate_image()   — format + size check
    │       ├── decode_image()     — base64 or raw bytes
    │       ├── extract_exif()     — GPS, timestamp, device metadata
    │       └── normalize_image()  — resize to max 1024px (analysis copy)
    ├── Vision analysis  (ai_engine/providers/vision_granite.py)
    │       └── GraniteVisionProvider
    │               ├── Auto-detect vision-capable Ollama model
    │               ├── If available: real inference with image bytes
    │               └── If unavailable: VISION_UNAVAILABLE (honest)
    └── Fusion  (ai_engine/fusion/fusion_engine.py)
            ├── TEXT_ONLY         — no image provided
            ├── TEXT_AND_IMAGE    — both text + vision succeeded
            └── TEXT_ONLY_FALLBACK — image provided but vision failed
```

---

## Key Files

| File | Purpose |
|------|---------|
| `ai_engine/providers/vision_base.py` | Abstract `VisionProvider` interface |
| `ai_engine/providers/vision_granite.py` | Granite/Ollama vision implementation |
| `ai_engine/providers/embedding_base.py` | Abstract `EmbeddingProvider` (reserved) |
| `ai_engine/vision/schemas.py` | `ImageAnalysisResult`, `VisualSignals`, `VisualReasonCode`, `MultimodalMode` |
| `ai_engine/vision/preprocessing.py` | Image validation, decoding, EXIF, normalization |
| `ai_engine/vision/image_analyzer.py` | `ImageAnalyzer` — ties preprocessing + provider |
| `ai_engine/fusion/schemas.py` | `FusedResult` |
| `ai_engine/fusion/fusion_engine.py` | `FusionEngine` — weighted text+image combination |

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `VISION_MODEL` | `""` | Vision model tag. Empty = auto-detect |
| `VISION_PROVIDER` | `granite_vision` | Provider name |
| `TEXT_FUSION_WEIGHT` | `0.60` | Weight of text severity in fusion |
| `IMAGE_FUSION_WEIGHT` | `0.40` | Weight of image severity in fusion |
| `MAX_IMAGE_SIZE_MB` | `10.0` | Maximum accepted image size (MB) |

---

## Visual Signals Detected

| Signal | Description |
|--------|-------------|
| `flood_water` | Visible flood or standing water |
| `people_visible` | People seen in the image |
| `structural_damage` | Damaged building, collapsed walls |
| `fire_present` | Active fire or flames |
| `smoke_visible` | Smoke in the scene |
| `road_blocked` | Blocked road or debris |
| `vehicle_submerged` | Submerged vehicle |
| `unsafe_environment` | Generically unsafe conditions |

---

## Fusion Logic

```
TEXT_ONLY mode (no image):
    final_score = text_score  [text_weight = 1.0]

TEXT_AND_IMAGE mode (vision succeeded):
    final_score = text_score × 0.60 + image_score × 0.40

TEXT_ONLY_FALLBACK mode (vision failed/unavailable):
    final_score = text_score  [text_weight = 1.0]
    (No penalty — image failure is graceful)
```

---

## Vision Availability

The `GraniteVisionProvider` **never fakes results**.

1. It queries the Ollama `/api/tags` endpoint for available models.
2. If a vision-capable model is found (e.g., llava, moondream, minicpm-v),
   it uses that model for inference.
3. If no vision model is available, it returns:
   ```json
   {
     "vision_available": false,
     "visual_reason_codes": ["VISION_UNAVAILABLE"],
     "error_message": "No vision-capable model is available in Ollama."
   }
   ```

To enable vision analysis: `ollama pull llava`

---

## EXIF Metadata

When a JPEG/PNG image with EXIF data is submitted:
- GPS coordinates are extracted and stored in `exif_data`
- Capture timestamp is stored in `exif_data.timestamp`
- These are used by the Phase 5 Authenticity Engine

The original image bytes are **never modified** — the EXIF extraction
operates on a read-only copy.

---

## Backward Compatibility

All Phase 4 fields on `IncidentAIOutput` are `Optional` with `None` defaults:
- `image_analysis: Optional[ImageAnalysisResult] = None`
- `multimodal_mode: Optional[MultimodalMode] = None`

Existing `/ai/triage` behavior is unchanged. The new `/ai/analyze` endpoint
provides the full multimodal output.
