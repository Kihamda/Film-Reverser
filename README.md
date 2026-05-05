# Film-Reverser

A Python/Tkinter GUI application for converting **135 film negative scans**
(colour and B&W) into positive images, with live-preview adjustments and
flexible crop tools.

---

## Features

| Feature | Detail |
|---|---|
| **RAW file support** | Reads all common camera RAW formats (CR2/CR3, NEF, ARW, RAF, ORF, DNG, …) |
| **Film-type detection** | Automatic heuristic or manual selection (Color Negative / B&W Negative) |
| **Negative inversion** | Orange-mask removal for colour negatives; luminance inversion for B&W |
| **Live adjustments** | Exposure, Contrast, Shadows, Highlights, Saturation, White Balance (Temp + Tint) |
| **Auto White Balance** | Gray-world estimation applied to the current frame |
| **Crop** | Auto-detect film borders (OpenCV or fallback); manual draw-on-canvas crop |
| **Save** | Single image (TIFF / JPEG / PNG) or batch-save entire folder |

---

## Requirements

- Python 3.9+
- `rawpy` – RAW decoding (libraw)
- `Pillow` – image processing and display
- `numpy` – array maths
- `opencv-python-headless` *(optional)* – better auto-crop / detection

Install all at once:

```bash
pip install -r requirements.txt
```

---

## Usage

```bash
python film_reverser.py
```

### Workflow

1. **Open Folder** (`Ctrl+O`) – select a directory that contains RAW files.
   All supported files are listed in the left panel.
2. **Navigate** – click a file in the list, or use the **◀ / ▶** buttons
   (or ← / → arrow keys).
3. **Film type** – the app attempts auto-detection; override manually with
   the *Color Negative* / *B&W Negative* radio buttons.
4. **Adjust** – move the sliders in the right panel for real-time updates:
   - *Exposure* (EV stops), *Contrast*, *Shadows*, *Highlights*
   - *Temperature / Tint* (white balance), *Saturation*
   - Hit **⚖ Auto WB** for an automatic gray-world balance.
5. **Crop** – click **✂ Auto Crop** to detect frame borders automatically,
   or **✏ Manual Crop** to draw a rectangle directly on the preview canvas.
   **↺ Reset Crop** removes any crop.
6. **Save** – **💾 Save Image…** saves the current frame;
   **💾 Save All…** batch-processes every file in the list using the
   current settings (choose TIFF, JPEG, or PNG output format).

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `Ctrl+O` | Open folder |
| `Ctrl+S` | Save current image |
| `←` | Previous file |
| `→` | Next file |

---

## Supported RAW Formats

Canon (CR2, CR3), Nikon (NEF, NRW), Sony (ARW, SR2, SRF),
Fujifilm (RAF), Olympus (ORF), Panasonic (RW2), Pentax (PEF, PTX),
Hasselblad (3FR), Phase One (IIQ), Minolta (MRW), Sigma (X3F),
Kodak (KDC, DCR), Epson (ERF), Mamiya (MEF), Leaf (MOS),
Adobe DNG, and generic `.raw` / `.rwl`.
