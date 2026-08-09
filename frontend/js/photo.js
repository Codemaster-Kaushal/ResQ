/* Downscale a photograph before upload.
 *
 * A 4 MB camera JPEG over a weak link keeps the radio at full transmit power
 * for minutes. Resizing to ~1280 px costs roughly 50 ms of CPU and cuts that to
 * a few seconds — on a phone, radio time is the battery cost that matters, not
 * the arithmetic.
 *
 * Two things this must not break:
 *
 * - **EXIF GPS.** Canvas re-encoding strips all metadata, and the backend's
 *   EXIF_CONSISTENT trust signal reads the photo's own coordinates. So the GPS
 *   is parsed out *before* re-encoding and sent as explicit form fields.
 * - **The perceptual hash.** pHash is computed from image structure, not
 *   pixels, so a downscaled copy still hashes close to the original — the
 *   seeded duplicate pair proves it, staying within Hamming distance 2 after a
 *   crop, rescale and re-encode. Duplicate detection survives.
 */

const MAX_EDGE = 1280;
const QUALITY = 0.8;
// Below this there is nothing to gain and re-encoding would only lose detail.
const SKIP_BELOW_BYTES = 300 * 1024;

/** Minimal EXIF GPS reader — enough to preserve the one signal that matters. */
async function readExifGps(file) {
  try {
    const buffer = await file.slice(0, 128 * 1024).arrayBuffer();
    const view = new DataView(buffer);
    if (view.getUint16(0, false) !== 0xffd8) return null; // not a JPEG

    let offset = 2;
    while (offset < view.byteLength - 4) {
      const marker = view.getUint16(offset, false);
      const size = view.getUint16(offset + 2, false);
      if (marker === 0xffe1) {
        // APP1 — "Exif\0\0" then a TIFF header
        const tiff = offset + 10;
        if (view.getUint32(offset + 4, false) !== 0x45786966) return null;
        const little = view.getUint16(tiff, false) === 0x4949;
        const ifd0 = tiff + view.getUint32(tiff + 4, little);
        const gpsPointer = findTag(view, ifd0, tiff, little, 0x8825);
        if (gpsPointer == null) return null;
        return readGpsIfd(view, tiff + gpsPointer, tiff, little);
      }
      if ((marker & 0xff00) !== 0xff00) break;
      offset += 2 + size;
    }
  } catch {
    // A camera writing malformed EXIF is not a reason to fail an emergency report.
  }
  return null;
}

function findTag(view, dirStart, tiffStart, little, wanted) {
  const count = view.getUint16(dirStart, little);
  for (let i = 0; i < count; i += 1) {
    const entry = dirStart + 2 + i * 12;
    if (view.getUint16(entry, little) === wanted) {
      return view.getUint32(entry + 8, little);
    }
  }
  return null;
}

function readRational(view, offset, little) {
  const numerator = view.getUint32(offset, little);
  const denominator = view.getUint32(offset + 4, little);
  return denominator ? numerator / denominator : 0;
}

function readGpsIfd(view, dirStart, tiffStart, little) {
  const count = view.getUint16(dirStart, little);
  const tags = {};
  for (let i = 0; i < count; i += 1) {
    const entry = dirStart + 2 + i * 12;
    tags[view.getUint16(entry, little)] = entry;
  }

  const degrees = (tagId) => {
    const entry = tags[tagId];
    if (entry == null) return null;
    const at = tiffStart + view.getUint32(entry + 8, little);
    return (
      readRational(view, at, little)
      + readRational(view, at + 8, little) / 60
      + readRational(view, at + 16, little) / 3600
    );
  };
  const ref = (tagId) => {
    const entry = tags[tagId];
    return entry == null ? '' : String.fromCharCode(view.getUint8(entry + 8));
  };

  const lat = degrees(0x0002);
  const lng = degrees(0x0004);
  if (lat == null || lng == null) return null;

  return {
    lat: /S/i.test(ref(0x0001)) ? -lat : lat,
    lng: /W/i.test(ref(0x0003)) ? -lng : lng,
  };
}

/**
 * Returns `{ file, exifGps, originalBytes, finalBytes }`.
 * Falls back to the untouched original on any failure — a photo that cannot be
 * shrunk is still worth sending.
 */
export async function preparePhoto(file) {
  const original = file.size;
  const exifGps = await readExifGps(file);

  if (file.size <= SKIP_BELOW_BYTES) {
    return { file, exifGps, originalBytes: original, finalBytes: original };
  }

  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
    if (scale === 1) {
      bitmap.close?.();
      return { file, exifGps, originalBytes: original, finalBytes: original };
    }

    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close?.();

    const blob = await new Promise((resolve) =>
      canvas.toBlob(resolve, 'image/jpeg', QUALITY));
    if (!blob || blob.size >= original) {
      return { file, exifGps, originalBytes: original, finalBytes: original };
    }

    const shrunk = new File([blob], (file.name || 'photo').replace(/\.\w+$/, '') + '.jpg', {
      type: 'image/jpeg',
      lastModified: Date.now(),
    });
    return { file: shrunk, exifGps, originalBytes: original, finalBytes: shrunk.size };
  } catch {
    return { file, exifGps, originalBytes: original, finalBytes: original };
  }
}
