#!/usr/bin/env python3
"""
Film Reverser
=============
A GUI application for converting 135 film negative scans (color and B&W)
to positive images, with live-preview adjustments, dust/scratch removal,
and crop functionality.

Requirements: rawpy, Pillow, numpy, opencv-python-headless (optional)
Usage:
    uv run film_reverser.py          # recommended – auto-installs deps
    python film_reverser.py          # if deps are already installed
"""

import os
import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageFilter, ImageTk
import rawpy

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# ──────────────────────────── constants ──────────────────────────────────────

RAW_EXTENSIONS = frozenset({
    ".cr2", ".cr3",          # Canon
    ".nef", ".nrw",          # Nikon
    ".arw", ".srf", ".sr2",  # Sony
    ".raf",                  # Fujifilm
    ".orf",                  # Olympus
    ".rw2",                  # Panasonic
    ".pef", ".ptx",          # Pentax
    ".dng",                  # Adobe DNG / generic
    ".raw", ".rwl",
    ".3fr",                  # Hasselblad
    ".iiq",                  # Phase One
    ".mrw",                  # Minolta
    ".x3f",                  # Sigma
    ".kdc", ".dcr",          # Kodak
    ".erf",                  # Epson
    ".mef",                  # Mamiya
    ".mos",                  # Leaf
})

# ──────────────────────────── main application ───────────────────────────────


class FilmReverserApp:
    """Film Reverser – convert 135 film negative scans to positive images."""

    # ── init ─────────────────────────────────────────────────────────────────

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Film Reverser")
        self.root.geometry("1280x820")
        self.root.minsize(900, 640)

        # data
        self.raw_files: list = []
        self.current_index: int = -1
        self.original_array = None   # float32 [0,1] H×W×3
        self.processed_pil = None    # PIL Image after full processing
        self.display_pil = None      # resized version shown on canvas
        self.photo_image = None      # ImageTk reference (kept to prevent GC)

        # crop state (coordinates in original-image space)
        self.crop_rect = None        # (x1, y1, x2, y2) or None
        self._crop_drag_start = None
        self._crop_rect_canvas_id = None
        self._manual_crop_active = False

        # adjustment parameters
        self.params = {
            "film_type":    tk.StringVar(value="color"),
            "exposure":     tk.DoubleVar(value=0.0),
            "contrast":     tk.DoubleVar(value=1.0),
            "shadows":      tk.DoubleVar(value=0.0),
            "highlights":   tk.DoubleVar(value=0.0),
            "saturation":   tk.DoubleVar(value=1.0),
            "wb_temp":      tk.DoubleVar(value=0.0),
            "wb_tint":      tk.DoubleVar(value=0.0),
            "dust_strength": tk.DoubleVar(value=0.0),
        }
        self._debounce_id = None

        # trace numeric params for live update
        for key, var in self.params.items():
            if isinstance(var, (tk.DoubleVar, tk.IntVar)):
                var.trace_add("write", self._schedule_reprocess)

        self._build_ui()
        self._set_status("Ready – open a folder (Ctrl+O) to load RAW files.")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ---- menu bar ----
        menu = tk.Menu(self.root)
        self.root.config(menu=menu)

        fm = tk.Menu(menu, tearoff=False)
        fm.add_command(label="Open Folder…",  command=self.open_folder,
                       accelerator="Ctrl+O")
        fm.add_command(label="Save Image…",   command=self.save_image,
                       accelerator="Ctrl+S")
        fm.add_separator()
        fm.add_command(label="Exit", command=self.root.quit)
        menu.add_cascade(label="File", menu=fm)

        self.root.bind("<Control-o>", lambda _: self.open_folder())
        self.root.bind("<Control-s>", lambda _: self.save_image())
        self.root.bind("<Left>",      lambda _: self.prev_file())
        self.root.bind("<Right>",     lambda _: self.next_file())

        # ---- three-column layout ----
        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self._build_file_panel(main)
        self._build_preview_panel(main)
        self._build_adjust_panel(main)

        # ---- status bar ----
        self._status_var = tk.StringVar()
        ttk.Label(self.root, textvariable=self._status_var,
                  relief=tk.SUNKEN, anchor=tk.W).pack(
            side=tk.BOTTOM, fill=tk.X, padx=6, pady=(0, 6))

    # -- left: file list -------------------------------------------------------

    def _build_file_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Files", width=180)
        frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        frame.pack_propagate(False)

        nav = ttk.Frame(frame)
        nav.pack(fill=tk.X, pady=2)
        ttk.Button(nav, text="◀", command=self.prev_file,
                   width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(nav, text="▶", command=self.next_file,
                   width=3).pack(side=tk.LEFT, padx=2)
        ttk.Button(nav, text="Open Folder", command=self.open_folder).pack(
            side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        container = ttk.Frame(frame)
        container.pack(fill=tk.BOTH, expand=True)

        sb = ttk.Scrollbar(container, orient=tk.VERTICAL)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._file_listbox = tk.Listbox(
            container, selectmode=tk.SINGLE,
            exportselection=False, yscrollcommand=sb.set)
        self._file_listbox.pack(fill=tk.BOTH, expand=True)
        sb.config(command=self._file_listbox.yview)
        self._file_listbox.bind("<<ListboxSelect>>", self._on_listbox_select)

    # -- center: canvas --------------------------------------------------------

    def _build_preview_panel(self, parent):
        frame = ttk.LabelFrame(parent, text="Preview")
        frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        self._canvas = tk.Canvas(frame, bg="#1e1e1e", cursor="crosshair")
        hbar = ttk.Scrollbar(frame, orient=tk.HORIZONTAL,
                              command=self._canvas.xview)
        vbar = ttk.Scrollbar(frame, orient=tk.VERTICAL,
                              command=self._canvas.yview)
        self._canvas.configure(
            xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        hbar.pack(side=tk.BOTTOM, fill=tk.X)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(fill=tk.BOTH, expand=True)

        self._canvas.bind("<Configure>",       self._on_canvas_configure)
        self._canvas.bind("<ButtonPress-1>",   self._on_canvas_press)
        self._canvas.bind("<B1-Motion>",       self._on_canvas_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_canvas_release)

    # -- right: adjustments ----------------------------------------------------

    def _build_adjust_panel(self, parent):
        outer = ttk.LabelFrame(parent, text="Adjustments", width=230)
        outer.pack(side=tk.LEFT, fill=tk.Y)
        outer.pack_propagate(False)

        # scrollable inner frame
        canvas = tk.Canvas(outer, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(fill=tk.BOTH, expand=True)

        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(
                       scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw", width=220)

        p = dict(padx=8, pady=4)

        # Film type
        ff = ttk.LabelFrame(inner, text="Film Type")
        ff.pack(fill=tk.X, **p)
        ttk.Radiobutton(ff, text="Color Negative",
                        variable=self.params["film_type"],
                        value="color",
                        command=self._reprocess).pack(
            anchor=tk.W, padx=6, pady=1)
        ttk.Radiobutton(ff, text="B&W Negative",
                        variable=self.params["film_type"],
                        value="bw",
                        command=self._reprocess).pack(
            anchor=tk.W, padx=6, pady=1)
        ttk.Button(ff, text="🔍 Auto Detect",
                   command=self.auto_detect_film).pack(
            fill=tk.X, padx=6, pady=4)

        # Tone
        tf = ttk.LabelFrame(inner, text="Tone")
        tf.pack(fill=tk.X, **p)
        self._add_slider(tf, "Exposure",   "exposure",   -3.0,  3.0,  0.01)
        self._add_slider(tf, "Contrast",   "contrast",    0.5,  2.5,  0.05)
        self._add_slider(tf, "Shadows",    "shadows",   -100., 100.,  1.0)
        self._add_slider(tf, "Highlights", "highlights", -100., 100.,  1.0)

        # White Balance
        wf = ttk.LabelFrame(inner, text="White Balance")
        wf.pack(fill=tk.X, **p)
        self._add_slider(wf, "Temperature", "wb_temp", -100., 100., 1.0)
        self._add_slider(wf, "Tint",        "wb_tint", -100., 100., 1.0)
        ttk.Button(wf, text="⚖ Auto WB",
                   command=self.auto_white_balance).pack(
            fill=tk.X, padx=6, pady=4)

        # Color
        cf = ttk.LabelFrame(inner, text="Color")
        cf.pack(fill=tk.X, **p)
        self._add_slider(cf, "Saturation", "saturation", 0.0, 2.0, 0.05)

        # Dust & Scratches
        df = ttk.LabelFrame(inner, text="Dust & Scratches")
        df.pack(fill=tk.X, **p)
        ttk.Label(df, text="Strength  0 = off", foreground="gray",
                  font=("TkDefaultFont", 8)).pack(anchor=tk.W, padx=6)
        self._add_slider(df, "Strength", "dust_strength", 0.0, 100.0, 1.0)

        # Crop
        kf = ttk.LabelFrame(inner, text="Crop")
        kf.pack(fill=tk.X, **p)
        ttk.Button(kf, text="✂ Auto Crop",
                   command=self.auto_crop).pack(
            fill=tk.X, padx=6, pady=2)
        self._crop_btn = ttk.Button(kf, text="✏ Manual Crop (draw)",
                                    command=self.toggle_manual_crop)
        self._crop_btn.pack(fill=tk.X, padx=6, pady=2)
        ttk.Button(kf, text="↺ Reset Crop",
                   command=self.reset_crop).pack(
            fill=tk.X, padx=6, pady=2)

        # Actions
        ttk.Separator(inner, orient=tk.HORIZONTAL).pack(
            fill=tk.X, padx=8, pady=6)
        ttk.Button(inner, text="↺ Reset All",
                   command=self.reset_all).pack(fill=tk.X, **p)
        ttk.Button(inner, text="💾 Save Image…",
                   command=self.save_image).pack(fill=tk.X, **p)
        ttk.Button(inner, text="💾 Save All in Folder…",
                   command=self.save_all).pack(fill=tk.X, **p)

    def _add_slider(self, parent, label: str, key: str,
                    from_: float, to: float, resolution: float):
        """Add a labeled slider widget to *parent*."""
        container = ttk.Frame(parent)
        container.pack(fill=tk.X, padx=5, pady=2)

        lbl_row = ttk.Frame(container)
        lbl_row.pack(fill=tk.X)
        ttk.Label(lbl_row, text=label, anchor=tk.W).pack(side=tk.LEFT)
        val_lbl = ttk.Label(lbl_row, text="", width=7, anchor=tk.E)
        val_lbl.pack(side=tk.RIGHT)

        ttk.Scale(container, from_=from_, to=to,
                  variable=self.params[key],
                  orient=tk.HORIZONTAL).pack(fill=tk.X)

        def _fmt(*_):
            v = self.params[key].get()
            val_lbl.config(text=f"{v:+.2f}" if from_ < 0 else f"{v:.2f}")

        self.params[key].trace_add("write", _fmt)
        _fmt()

    # ── file operations ───────────────────────────────────────────────────────

    def open_folder(self):
        """Open a folder and populate the file list with RAW files."""
        folder = filedialog.askdirectory(
            title="Select Folder Containing RAW Files")
        if not folder:
            return

        files = sorted(
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if os.path.splitext(f)[1].lower() in RAW_EXTENSIONS
        )
        self.raw_files = files
        self._file_listbox.delete(0, tk.END)
        for f in files:
            self._file_listbox.insert(tk.END, os.path.basename(f))

        if files:
            self._set_status(
                f"Found {len(files)} RAW file(s) in {folder}")
            self.load_file(0)
        else:
            self._set_status(f"No RAW files found in: {folder}")

    def load_file(self, index: int):
        """Load RAW file at *index* and run the processing pipeline."""
        if not (0 <= index < len(self.raw_files)):
            return

        self.current_index = index
        self._file_listbox.selection_clear(0, tk.END)
        self._file_listbox.selection_set(index)
        self._file_listbox.see(index)

        path = self.raw_files[index]
        self._set_status(f"Loading {os.path.basename(path)}…")
        self.root.update()

        try:
            self.original_array = _load_raw(path)
            self.crop_rect = None
            self._reprocess()
            h, w = self.original_array.shape[:2]
            self._set_status(
                f"[{index + 1}/{len(self.raw_files)}]  "
                f"{os.path.basename(path)}  –  {w}×{h} px")
        except Exception as exc:
            messagebox.showerror(
                "Load Error", f"Could not load file:\n{exc}")
            self._set_status("Error loading file.")

    def prev_file(self):
        if self.current_index > 0:
            self.load_file(self.current_index - 1)

    def next_file(self):
        if self.current_index < len(self.raw_files) - 1:
            self.load_file(self.current_index + 1)

    def _on_listbox_select(self, _event):
        sel = self._file_listbox.curselection()
        if sel:
            self.load_file(sel[0])

    # ── processing ────────────────────────────────────────────────────────────

    def _schedule_reprocess(self, *_):
        """Debounce rapid slider movements (60 ms)."""
        if self._debounce_id:
            self.root.after_cancel(self._debounce_id)
        self._debounce_id = self.root.after(60, self._reprocess)

    def _reprocess(self):
        """Full processing pipeline → update canvas."""
        if self.original_array is None:
            return
        self.processed_pil = self._process_array(
            self.original_array, apply_crop=True)
        self._update_canvas()

    def _process_array(self, arr: np.ndarray,
                       apply_crop: bool = True) -> Image.Image:
        """Apply the full pipeline to *arr*; return 8-bit RGB PIL image."""
        img = arr.copy()
        ft = self.params["film_type"].get()

        img = _invert_negative(img, ft)
        dust = self.params["dust_strength"].get() / 100.0
        if dust > 0.0:
            img = _remove_dust(img, dust)
        img = _apply_wb(img,
                        self.params["wb_temp"].get() / 100.0,
                        self.params["wb_tint"].get() / 100.0)
        img = _apply_exposure(img, self.params["exposure"].get())
        img = _apply_tone(img,
                          self.params["contrast"].get(),
                          self.params["shadows"].get() / 100.0,
                          self.params["highlights"].get() / 100.0)
        img = _apply_saturation(img, self.params["saturation"].get(), ft)
        img = np.clip(img, 0.0, 1.0)

        if apply_crop and self.crop_rect:
            x1, y1, x2, y2 = self.crop_rect
            img = img[y1:y2, x1:x2]

        return Image.fromarray((img * 255).astype(np.uint8), "RGB")

    # ── auto helpers ──────────────────────────────────────────────────────────

    def auto_detect_film(self):
        """Heuristic detection of Color vs B&W negative."""
        if self.original_array is None:
            messagebox.showinfo("Auto Detect",
                                "Please load a file first.")
            return

        img = self.original_array
        h, w = img.shape[:2]
        sample = img[h // 4:3 * h // 4, w // 4:3 * w // 4]
        means = sample.reshape(-1, 3).mean(axis=0)
        spread = float(max(
            abs(means[0] - means[1]),
            abs(means[1] - means[2]),
            abs(means[0] - means[2]),
        ))
        detected = "color" if spread > 0.04 else "bw"
        self.params["film_type"].set(detected)
        label = "Color Negative" if detected == "color" else "B&W Negative"
        self._set_status(
            f"Auto detected: {label}  "
            f"(channel spread = {spread:.3f})")
        self._reprocess()

    def auto_white_balance(self):
        """Gray-world white balance on the current processed image."""
        if self.processed_pil is None:
            return
        img = np.array(self.processed_pil).astype(np.float32) / 255.0
        h, w = img.shape[:2]
        centre = img[h // 4:3 * h // 4, w // 4:3 * w // 4]
        r = centre[:, :, 0].mean()
        g = centre[:, :, 1].mean()
        b = centre[:, :, 2].mean()
        if r < 0.01 or b < 0.01:
            return
        # Warm/cool balance + magenta/green tint
        temp_corr = -(r - b) * 100.0
        tint_corr = (r + b - 2 * g) * 50.0
        new_temp = float(
            np.clip(self.params["wb_temp"].get() + temp_corr, -100, 100))
        new_tint = float(
            np.clip(self.params["wb_tint"].get() + tint_corr, -100, 100))
        self.params["wb_temp"].set(round(new_temp, 1))
        self.params["wb_tint"].set(round(new_tint, 1))
        self._set_status("Auto white balance applied.")

    def auto_crop(self):
        """Detect film-frame borders and apply crop."""
        if self.original_array is None:
            return
        rect = _detect_crop(self.original_array)
        if rect:
            self.crop_rect = rect
            self._reprocess()
            self._set_status(f"Auto crop applied: {rect}")
        else:
            self._set_status("Auto crop: could not detect film borders.")

    def reset_crop(self):
        self.crop_rect = None
        self._reprocess()
        self._set_status("Crop reset.")

    def toggle_manual_crop(self):
        """Toggle interactive crop-drawing mode."""
        self._manual_crop_active = not self._manual_crop_active
        if self._manual_crop_active:
            self._crop_btn.config(text="✏ Drawing… (drag on image)")
            self._canvas.config(cursor="crosshair")
            self._set_status(
                "Manual crop – click and drag on the image to set the crop area.")
        else:
            self._crop_btn.config(text="✏ Manual Crop (draw)")
            self._canvas.config(cursor="crosshair")

    # ── canvas event handlers ─────────────────────────────────────────────────

    def _on_canvas_configure(self, _event):
        if self.processed_pil is not None:
            self._update_canvas()

    def _on_canvas_press(self, event):
        if not self._manual_crop_active:
            return
        self._crop_drag_start = (event.x, event.y)
        if self._crop_rect_canvas_id:
            self._canvas.delete(self._crop_rect_canvas_id)
            self._crop_rect_canvas_id = None

    def _on_canvas_drag(self, event):
        if not self._manual_crop_active or self._crop_drag_start is None:
            return
        if self._crop_rect_canvas_id:
            self._canvas.delete(self._crop_rect_canvas_id)
        x0, y0 = self._crop_drag_start
        self._crop_rect_canvas_id = self._canvas.create_rectangle(
            x0, y0, event.x, event.y,
            outline="yellow", width=2, dash=(6, 3))

    def _on_canvas_release(self, event):
        if not self._manual_crop_active or self._crop_drag_start is None:
            return
        x0, y0 = self._crop_drag_start
        rect = self._canvas_to_orig(x0, y0, event.x, event.y)

        self._crop_drag_start = None
        self._manual_crop_active = False
        self._crop_btn.config(text="✏ Manual Crop (draw)")
        self._canvas.config(cursor="crosshair")

        if self._crop_rect_canvas_id:
            self._canvas.delete(self._crop_rect_canvas_id)
            self._crop_rect_canvas_id = None

        if rect:
            self.crop_rect = rect
            self._reprocess()
            self._set_status(f"Manual crop set: {rect}")
        else:
            self._set_status("Crop area too small – ignored.")

    def _canvas_to_orig(self, cx0, cy0, cx1, cy1):
        """Map canvas pixel coords → original-image pixel coords."""
        if self.display_pil is None or self.original_array is None:
            return None

        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        dw, dh = self.display_pil.size
        ox = (cw - dw) // 2
        oy = (ch - dh) // 2

        # canvas → display-image coords
        dx0, dy0 = cx0 - ox, cy0 - oy
        dx1, dy1 = cx1 - ox, cy1 - oy

        # display-image → processed-image coords (undo LANCZOS scaling)
        pw, ph = self.processed_pil.size
        sx, sy = pw / dw, ph / dh
        px0, py0 = dx0 * sx, dy0 * sy
        px1, py1 = dx1 * sx, dy1 * sy

        # processed-image → original-image (account for current crop offset)
        off_x = self.crop_rect[0] if self.crop_rect else 0
        off_y = self.crop_rect[1] if self.crop_rect else 0
        oh, ow = self.original_array.shape[:2]

        ix0, iy0 = int(px0) + off_x, int(py0) + off_y
        ix1, iy1 = int(px1) + off_x, int(py1) + off_y

        x1, x2 = sorted([ix0, ix1])
        y1, y2 = sorted([iy0, iy1])
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(ow, x2), min(oh, y2)

        if x2 - x1 < 10 or y2 - y1 < 10:
            return None
        return (x1, y1, x2, y2)

    # ── canvas drawing ────────────────────────────────────────────────────────

    def _update_canvas(self):
        if self.processed_pil is None:
            return
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            return

        iw, ih = self.processed_pil.size
        scale = min(cw / iw, ch / ih)   # fit inside canvas
        dw = max(1, int(iw * scale))
        dh = max(1, int(ih * scale))

        self.display_pil = self.processed_pil.resize(
            (dw, dh), Image.Resampling.LANCZOS)
        self.photo_image = ImageTk.PhotoImage(self.display_pil)

        self._canvas.delete("all")
        self._canvas.create_image(
            cw // 2, ch // 2, anchor=tk.CENTER, image=self.photo_image)
        self._canvas.configure(
            scrollregion=(0, 0, max(dw, cw), max(dh, ch)))

    # ── save ─────────────────────────────────────────────────────────────────

    def save_image(self):
        """Save the currently displayed processed image."""
        if self.processed_pil is None:
            messagebox.showinfo("Save", "No image loaded.")
            return

        default = "processed.tif"
        if 0 <= self.current_index < len(self.raw_files):
            stem = os.path.splitext(
                os.path.basename(self.raw_files[self.current_index]))[0]
            default = stem + "_positive.tif"

        path = filedialog.asksaveasfilename(
            title="Save Image",
            defaultextension=".tif",
            initialfile=default,
            filetypes=[
                ("TIFF", "*.tif *.tiff"),
                ("JPEG", "*.jpg *.jpeg"),
                ("PNG",  "*.png"),
                ("All",  "*.*"),
            ])
        if not path:
            return
        try:
            _save_pil(self.processed_pil, path)
            self._set_status(f"Saved: {path}")
        except Exception as exc:
            messagebox.showerror("Save Error",
                                 f"Could not save image:\n{exc}")

    def save_all(self):
        """Batch-process and save all loaded files with current settings."""
        if not self.raw_files:
            messagebox.showinfo("Save All", "No files loaded.")
            return

        out_dir = filedialog.askdirectory(title="Select Output Folder")
        if not out_dir:
            return

        # format chooser dialog
        dlg = tk.Toplevel(self.root)
        dlg.title("Output Format")
        dlg.geometry("200x130")
        dlg.transient(self.root)
        dlg.grab_set()

        ttk.Label(dlg, text="Choose output format:").pack(pady=6)
        fmt = tk.StringVar(value=".tif")
        for ext, lbl in [(".tif", "TIFF"), (".jpg", "JPEG"), (".png", "PNG")]:
            ttk.Radiobutton(dlg, text=lbl, variable=fmt,
                            value=ext).pack(anchor=tk.W, padx=20)
        confirmed = tk.BooleanVar(value=False)

        def _confirm_and_close():
            confirmed.set(True)
            dlg.destroy()

        ttk.Button(dlg, text="OK", command=_confirm_and_close).pack(pady=6)
        self.root.wait_window(dlg)
        if not confirmed.get():
            return

        out_ext = fmt.get()
        saved_idx = self.current_index

        prog = tk.Toplevel(self.root)
        prog.title("Batch Saving…")
        prog.geometry("360x90")
        prog.transient(self.root)
        prog_lbl = ttk.Label(prog, text="")
        prog_lbl.pack(pady=6)
        prog_bar = ttk.Progressbar(
            prog, maximum=len(self.raw_files), mode="determinate")
        prog_bar.pack(fill=tk.X, padx=20)

        errors = []
        for i, fp in enumerate(self.raw_files):
            prog_lbl.config(
                text=f"{i + 1}/{len(self.raw_files)}: {os.path.basename(fp)}")
            prog_bar["value"] = i
            prog.update()
            try:
                arr = _load_raw(fp)
                # batch: apply current settings but no per-image crop
                pil = self._process_array(arr, apply_crop=False)
                stem = os.path.splitext(os.path.basename(fp))[0]
                _save_pil(pil,
                          os.path.join(out_dir, stem + "_positive" + out_ext))
            except Exception as exc:
                errors.append(f"{os.path.basename(fp)}: {exc}")

        prog.destroy()
        self.load_file(saved_idx)   # restore view

        n_ok = len(self.raw_files) - len(errors)
        if errors:
            messagebox.showwarning(
                "Save All",
                f"{len(errors)} error(s):\n" + "\n".join(errors[:5]))
        else:
            messagebox.showinfo(
                "Save All",
                f"Saved {n_ok} file(s) to:\n{out_dir}")
        self._set_status(f"Batch complete – {n_ok} file(s) saved.")

    def reset_all(self):
        """Reset all sliders and crop to defaults."""
        self.params["exposure"].set(0.0)
        self.params["contrast"].set(1.0)
        self.params["shadows"].set(0.0)
        self.params["highlights"].set(0.0)
        self.params["saturation"].set(1.0)
        self.params["wb_temp"].set(0.0)
        self.params["wb_tint"].set(0.0)
        self.params["dust_strength"].set(0.0)
        self.crop_rect = None
        self._reprocess()
        self._set_status("All adjustments reset.")

    # ── utility ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self._status_var.set(msg)
        self.root.update_idletasks()


# ──────────────────────── pure processing functions ──────────────────────────

def _load_raw(path: str) -> np.ndarray:
    """Read a RAW file and return a float32 [0, 1] H×W×3 array."""
    with rawpy.imread(path) as raw:
        rgb = raw.postprocess(
            use_camera_wb=False,
            use_auto_wb=False,
            no_auto_bright=True,
            output_bps=16,          # 16-bit for precision
        )
    return rgb.astype(np.float32) / 65535.0


def _invert_negative(img: np.ndarray, film_type: str) -> np.ndarray:
    """Invert a negative scan to produce a positive image.

    Color negatives: estimate the orange film-base from the borders, then
    normalise per-channel so the base maps to white.
    B&W negatives: convert to luminance and invert.
    """
    if film_type == "color":
        bs = max(1, min(60, img.shape[0] // 20, img.shape[1] // 20))
        border = np.concatenate([
            img[:bs, :].reshape(-1, 3),
            img[-bs:, :].reshape(-1, 3),
            img[:, :bs].reshape(-1, 3),
            img[:, -bs:].reshape(-1, 3),
        ])
        # 95th-percentile of border pixels = unexposed film base
        base = np.quantile(border, 0.95, axis=0)
        base = np.maximum(base, 0.01)

        inv = (base - img) / base
        inv = np.clip(inv, 0.0, 1.0)

        # stretch each channel to fill [0, 1] using a single quantile call
        lows, highs = np.quantile(
            inv.reshape(-1, 3), (0.02, 0.98), axis=0)
        for c in range(3):
            lo, hi = lows[c], highs[c]
            if hi > lo:
                inv[:, :, c] = (inv[:, :, c] - lo) / (hi - lo)
        return np.clip(inv, 0.0, 1.0)

    else:  # B&W
        gray = (0.299 * img[:, :, 0]
                + 0.587 * img[:, :, 1]
                + 0.114 * img[:, :, 2])
        inv = 1.0 - gray
        lo, hi = np.quantile(inv, (0.02, 0.98))
        if hi > lo:
            inv = (inv - lo) / (hi - lo)
        inv = np.clip(inv, 0.0, 1.0)
        return np.stack([inv, inv, inv], axis=-1)


def _apply_wb(img: np.ndarray, temp: float, tint: float) -> np.ndarray:
    """White-balance shift.  *temp* and *tint* each in [−1, 1]."""
    if abs(temp) < 1e-3 and abs(tint) < 1e-3:
        return img
    r = img[:, :, 0] * (1.0 + temp * 0.5)
    g = img[:, :, 1] * (1.0 + tint * 0.3)
    b = img[:, :, 2] * (1.0 - temp * 0.5)
    return np.stack([r, g, b], axis=-1)


def _apply_exposure(img: np.ndarray, ev: float) -> np.ndarray:
    """Multiply luminosity by 2^ev."""
    if abs(ev) < 1e-3:
        return img
    return img * (2.0 ** ev)


def _apply_tone(img: np.ndarray,
                contrast: float,
                shadows: float,
                highlights: float) -> np.ndarray:
    """Contrast around mid-grey, plus targeted shadows/highlights lift."""
    result = img.copy()
    if abs(contrast - 1.0) > 1e-3:
        result = 0.5 + (result - 0.5) * contrast
    if abs(shadows) > 1e-3:
        # shadows mask: strong near 0, zero near 0.5
        mask = np.clip(1.0 - result * 2.0, 0.0, 1.0)
        result = result + shadows * mask * 0.5
    if abs(highlights) > 1e-3:
        # highlights mask: strong near 1, zero near 0.5
        mask = np.clip((result - 0.5) * 2.0, 0.0, 1.0)
        result = result + highlights * mask * 0.5
    return result


def _apply_saturation(img: np.ndarray,
                      sat: float,
                      film_type: str) -> np.ndarray:
    """Blend between greyscale and full colour (sat = 1 → no change)."""
    if film_type == "bw" or abs(sat - 1.0) < 1e-3:
        return img
    gray = (0.299 * img[:, :, 0:1]
            + 0.587 * img[:, :, 1:2]
            + 0.114 * img[:, :, 2:3])
    gray = np.repeat(gray, 3, axis=2)
    return gray + sat * (img - gray)


def _remove_dust(img: np.ndarray, strength: float) -> np.ndarray:
    """Remove dust spots, scratches and stray-light artefacts.

    Works on the *positive* image (after inversion): dust on film appears as
    bright or dark outlier spots relative to the local neighbourhood.

    strength: 0.0 = off, 1.0 = maximum (maps to slider 0-100 / 100).

    Strategy
    --------
    1. Compute a local median image (robust background estimate).
    2. Build a mask of pixels whose deviation from the median exceeds a
       threshold that shrinks with *strength* (more aggressive detection at
       higher strength).
    3. Fill masked pixels via inpainting (OpenCV) or median replacement
       (PIL fallback).
    """
    if strength < 0.01:
        return img

    img8 = (np.clip(img, 0.0, 1.0) * 255).astype(np.uint8)

    # Deviation threshold (in 0-255 units) below which a pixel is considered
    # clean.  At minimum strength (~0.01) the threshold is ~60 (only obvious
    # bright/dark outliers flagged); at full strength (1.0) it drops to ~8
    # (catches fine grain and faint artefacts too).
    # Formula: threshold = max(MIN_THR, MAX_THR * (1 - strength * SCALE))
    _DUST_MIN_THR   = 6    # absolute floor so we never over-detect on noise
    _DUST_MAX_THR   = 60   # threshold at the lowest usable strength
    _DUST_SCALE     = 0.87 # how fast the threshold decreases with strength
    threshold = int(max(_DUST_MIN_THR,
                        _DUST_MAX_THR * (1.0 - strength * _DUST_SCALE)))

    if HAS_CV2:
        gray = cv2.cvtColor(img8, cv2.COLOR_RGB2GRAY)

        # Local median background (large kernel handles coarse dust)
        median = cv2.medianBlur(gray, 7)
        diff = gray.astype(np.int16) - median.astype(np.int16)

        # Mask: bright dust spots AND dark scratches
        mask = (np.abs(diff) > threshold).astype(np.uint8) * 255

        # Dilate slightly so the inpainting has context around each spot
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.dilate(mask, kernel, iterations=1)

        # Inpainting radius scales with strength
        inpaint_r = max(2, int(2 + strength * 4))
        result = cv2.inpaint(img8, mask, inpaintRadius=inpaint_r,
                             flags=cv2.INPAINT_TELEA)
        return result.astype(np.float32) / 255.0

    else:
        # PIL fallback: per-channel median replacement for outlier pixels
        pil = Image.fromarray(img8, "RGB")
        # ImageFilter.MedianFilter(size=k) uses an odd k×k kernel; here
        # k = 2*radius+1 so the kernel grows from 3×3 to 7×7 with strength.
        radius = max(1, int(1 + strength * 2))
        pil_median = pil.filter(ImageFilter.MedianFilter(size=radius * 2 + 1))

        orig_arr = np.array(pil, dtype=np.int16)
        med_arr  = np.array(pil_median, dtype=np.int16)
        diff     = np.abs(orig_arr - med_arr)

        # Replace outlier pixels with the median value
        dust_mask = diff.max(axis=2) > threshold
        result = orig_arr.copy()
        result[dust_mask] = med_arr[dust_mask]
        return result.astype(np.float32) / 255.0


def _detect_crop(img: np.ndarray):
    """Return (x1, y1, x2, y2) crop rect for the film frame, or None."""
    if HAS_CV2:
        gray = (0.299 * img[:, :, 0]
                + 0.587 * img[:, :, 1]
                + 0.114 * img[:, :, 2])
        g8 = (gray * 255).astype(np.uint8)
        blur = cv2.GaussianBlur(g8, (5, 5), 0)
        edges = cv2.Canny(blur, 20, 80)
        pts = cv2.findNonZero(edges)
        if pts is not None:
            x, y, w, h = cv2.boundingRect(pts)
            pad = 15
            H, W = img.shape[:2]
            return (max(0, x - pad), max(0, y - pad),
                    min(W, x + w + pad), min(H, y + h + pad))
    else:
        gray = (0.299 * img[:, :, 0]
                + 0.587 * img[:, :, 1]
                + 0.114 * img[:, :, 2])
        thr = np.quantile(gray, 0.50)
        mask = gray > thr
        rows = np.any(mask, axis=1)
        cols = np.any(mask, axis=0)
        if rows.any():
            r0, r1 = np.where(rows)[0][[0, -1]]
            c0, c1 = np.where(cols)[0][[0, -1]]
            H, W = img.shape[:2]
            my = max(2, (r1 - r0) // 50)
            mx = max(2, (c1 - c0) // 50)
            return (max(0, c0 - mx), max(0, r0 - my),
                    min(W, c1 + mx), min(H, r1 + my))
    return None


def _save_pil(img: Image.Image, path: str):
    """Save a PIL image; choose compression based on extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        img.save(path, quality=95, subsampling=0)
    elif ext in (".tif", ".tiff"):
        img.save(path, compression="lzw")
    else:
        img.save(path)


# ──────────────────────────────── entry point ────────────────────────────────

def main():
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.5)   # nicer on HiDPI displays
    except Exception:
        pass
    FilmReverserApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
