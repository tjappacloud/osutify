"""Main UI class for osu! MP3 Browser."""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import time
import json
import random
from pathlib import Path

from .config import get_default_osu_songs_dir, SUPPORTED_AUDIO_EXTS, MIN_DURATION_SECONDS, CACHE_FILENAME
from .utils import strip_leading_numbers, parse_artist_from_folder, format_duration, os_walk
from .metadata import get_mp3_metadata, get_osu_background, ensure_duration
from . import audio

# try to import Pillow for image thumbnails
try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except Exception:
    Image = None
    ImageTk = None
    HAS_PIL = False


class OsuMP3Browser(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("osu! MP3 Browser")
        width = self.winfo_screenwidth()
        height = self.winfo_screenheight()
        # Start maximized (zoomed) on Windows; fallback to fullscreen-sized window
        try:
            self.state('zoomed')
        except Exception:
            self.geometry("%dx%d" % (width, height))

        # Allow toggling zoom with Escape
        self.bind('<Escape>', lambda e: self.toggle_fullscreen())

        # Initialize pygame mixer
        if not audio.init_audio():
            messagebox.showwarning("Audio init failed", "pygame.mixer.init() failed")
        
        # default volume (0.0 - 1.0)
        self.volume_var = tk.DoubleVar(value=0.8)
        if audio.is_audio_initialized():
            audio.set_volume(self.volume_var.get())

        # minimum duration (seconds) configurable via UI
        self.min_duration_var = tk.IntVar(value=MIN_DURATION_SECONDS)
        # string var for entry widget so we can accept free text and validate on submit
        self.min_duration_strvar = tk.StringVar(value=str(self.min_duration_var.get()))
        # dark mode toggle
        self.dark_mode_var = tk.BooleanVar(value=False)

        self.songs_dir = get_default_osu_songs_dir()
        # diagnostic: print songs_dir info
        try:
            print(f"Osu songs dir: {self.songs_dir} (exists={self.songs_dir.exists()})")
            if self.songs_dir.exists():
                try:
                    count = sum(1 for _ in self.songs_dir.iterdir())
                    print(f"  Contains {count} items")
                except Exception:
                    pass
        except Exception:
            pass
        
        # store tuples of (Path, folder_title) where folder_title is the parent folder name
        self.all_mp3_paths = []
        self.mp3_paths = []  # list of (Path, display_title)
        # quick membership set of known paths to avoid duplicates during incremental scans
        self._seen_paths = set()

        # UI
        top = ttk.Frame(self)
        top.pack(fill=tk.X, padx=8, pady=6)

        self.dir_label = ttk.Label(top, text=f"Songs dir: {self.songs_dir}")
        self.dir_label.pack(side=tk.LEFT, expand=True)

        browse_btn = ttk.Button(top, text="Browse...", command=self.browse_folder)
        browse_btn.pack(side=tk.RIGHT)
        # Manual scan button for debugging/refresh
        scan_btn = ttk.Button(top, text="Scan Now", command=lambda: threading.Thread(target=self.scan_and_populate, daemon=True).start())
        scan_btn.pack(side=tk.RIGHT, padx=(6, 0))
        # Dark mode toggle
        try:
            self.dark_check = ttk.Checkbutton(top, text="Dark Mode", variable=self.dark_mode_var, command=self._on_theme_changed)
            self.dark_check.pack(side=tk.RIGHT, padx=(6, 0))
        except Exception:
            try:
                self.dark_check = tk.Checkbutton(top, text="Dark Mode", variable=self.dark_mode_var, command=self._on_theme_changed)
                self.dark_check.pack(side=tk.RIGHT, padx=(6, 0))
            except Exception:
                pass
        
        # Search entry
        search_frame = ttk.Frame(self)
        search_frame.pack(fill=tk.X, padx=8)
        ttk.Label(search_frame, text="Search:").pack(side=tk.LEFT, padx=(0, 6))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.search_entry.bind('<KeyRelease>', lambda e: self.refresh_list())
        clear_btn = ttk.Button(search_frame, text="Clear", command=self._clear_search)
        clear_btn.pack(side=tk.LEFT, padx=6)

        mid = ttk.Frame(self)
        mid.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        left = ttk.Frame(mid)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Create a container so we can place vertical and horizontal scrollbars correctly
        list_container = ttk.Frame(left)
        list_container.pack(fill=tk.BOTH, expand=True)

        # listbox inside container using grid so hscroll sits under list and vscroll to right
        self.listbox = tk.Listbox(list_container, activestyle='none')
        self.listbox.grid(row=0, column=0, sticky='nsew')
        self.listbox.bind('<Double-1>', self.on_double_click)
        self.listbox.bind('<<ListboxSelect>>', self.on_select)

        # vertical scrollbar
        try:
            scrollbar = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.listbox.yview)
        except Exception:
            scrollbar = tk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.listbox.config(yscrollcommand=scrollbar.set)

        # horizontal scrollbar beneath
        try:
            self.hscroll = ttk.Scrollbar(list_container, orient=tk.HORIZONTAL, command=self.listbox.xview)
        except Exception:
            self.hscroll = tk.Scrollbar(list_container, orient=tk.HORIZONTAL, command=self.listbox.xview)
        self.hscroll.grid(row=1, column=0, columnspan=2, sticky='ew')
        self.listbox.config(xscrollcommand=self.hscroll.set)

        # Make grid expand
        try:
            list_container.rowconfigure(0, weight=1)
            list_container.columnconfigure(0, weight=1)
        except Exception:
            pass

        # Lightweight tooltip for showing full title on hover with delay
        self._title_tooltip = None
        self._tooltip_after_id = None
        self._last_tooltip_index = None
        self._tooltip_delay_ms = 400
        self.listbox.bind('<Motion>', self._on_listbox_motion)
        self.listbox.bind('<Leave>', self._hide_title_tooltip)

        right = ttk.Frame(mid, width=240)
        right.pack(side=tk.RIGHT, fill=tk.Y)
        # Background thumbnail (will be filled on selection)
        self.meta_image_label = ttk.Label(right)
        self.meta_image_label.pack(anchor=tk.CENTER, padx=6, pady=6)

        # Metadata labels
        self.meta_title = ttk.Label(right, text="Title: ")
        self.meta_title.pack(anchor=tk.W, padx=6, pady=4)
        self.meta_artist = ttk.Label(right, text="Artist: ")
        self.meta_artist.pack(anchor=tk.W, padx=6, pady=4)
        self.meta_album = ttk.Label(right, text="Album: ")
        self.meta_album.pack(anchor=tk.W, padx=6, pady=4)
        self.meta_duration = ttk.Label(right, text="Duration: ")
        self.meta_duration.pack(anchor=tk.W, padx=6, pady=4)
        self.meta_path = ttk.Label(right, text="Path: ", wraplength=220)
        self.meta_path.pack(anchor=tk.W, padx=6, pady=4)

        bottom = ttk.Frame(self)
        bottom.pack(fill=tk.X, padx=8, pady=6)

        # Now playing area (shows thumbnail and song title) - placed just above controls
        now_frame = ttk.Frame(self)
        now_frame.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.now_image_label = ttk.Label(now_frame)
        self.now_image_label.pack(side=tk.LEFT, padx=(0, 8))
        now_right = ttk.Frame(now_frame)
        now_right.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.now_title_label = ttk.Label(now_right, text="Now: Not playing")
        self.now_title_label.pack(anchor=tk.W)
        # progress bar and time label
        # use a finer-grained internal scale (0-1000) for smoother progress updates
        self.progress = ttk.Progressbar(now_right, orient=tk.HORIZONTAL, mode='determinate', length=400, maximum=1000)
        self.progress.pack(fill=tk.X, pady=(4, 0))
        self.time_label = ttk.Label(now_right, text="0:00 / 0:00")
        self.time_label.pack(anchor=tk.W)
        # playback tracking
        self._playing_path = None
        self._progress_after_id = None
        # manual timing for smoother progress and seeking
        self._start_time = None
        self._pause_time = None
        self._paused_offset = 0.0
        # bind progress seeking events
        try:
            self.progress.bind('<Button-1>', self.on_progress_click)
            self.progress.bind('<B1-Motion>', self.on_progress_click)
        except Exception:
            pass

        self.play_btn = ttk.Button(bottom, text="Play", command=self.play_selected)
        self.play_btn.pack(side=tk.LEFT)

        self.pause_btn = ttk.Button(bottom, text="Pause", command=self.toggle_pause)
        self.pause_btn.pack(side=tk.LEFT, padx=6)

        self.stop_btn = ttk.Button(bottom, text="Stop", command=self.stop)
        self.stop_btn.pack(side=tk.LEFT)

        # play mode button: 'sequential', 'loop' (repeat current), 'shuffle' (random next)
        self.play_mode = 'sequential'  # default: advance to next
        try:
            self.mode_btn = ttk.Button(bottom, text="Mode: Sequential", command=self.cycle_play_mode)
            self.mode_btn.pack(side=tk.LEFT, padx=(6, 0))
        except Exception:
            try:
                self.mode_btn = tk.Button(bottom, text="Mode: Sequential", command=self.cycle_play_mode)
                self.mode_btn.pack(side=tk.LEFT, padx=(6, 0))
            except Exception:
                self.mode_btn = None

        # Volume control
        self.volume_label = ttk.Label(bottom, text=f"Vol: {int(self.volume_var.get()*100)}%")
        self.volume_label.pack(side=tk.LEFT, padx=(8, 4))
        # Use a ttk.Scale for volume (0.0 - 1.0)
        self.volume_scale = ttk.Scale(bottom, from_=0.0, to=1.0, orient=tk.HORIZONTAL,
                          length=120, variable=self.volume_var,
                          command=self.on_volume_change)
        self.volume_scale.pack(side=tk.LEFT)

        # Minimum duration entry
        try:
            self.min_label = ttk.Label(bottom, text="Min length (s):")
            self.min_label.pack(side=tk.LEFT, padx=(8, 4))
            self.min_entry = ttk.Entry(bottom, textvariable=self.min_duration_strvar, width=8)
            self.min_entry.pack(side=tk.LEFT)
            # on Enter or focus-out, validate and trigger a background rescan
            self.min_entry.bind('<Return>', lambda e: threading.Thread(target=self._on_min_duration_changed, daemon=True).start())
            self.min_entry.bind('<FocusOut>', lambda e: threading.Thread(target=self._on_min_duration_changed, daemon=True).start())
        except Exception:
            # fallback to simple tk.Entry
            try:
                self.min_label = ttk.Label(bottom, text="Min length (s):")
                self.min_label.pack(side=tk.LEFT, padx=(8, 4))
                self.min_entry = tk.Entry(bottom, textvariable=self.min_duration_strvar, width=8)
                self.min_entry.pack(side=tk.LEFT)
                self.min_entry.bind('<Return>', lambda e: threading.Thread(target=self._on_min_duration_changed, daemon=True).start())
                self.min_entry.bind('<FocusOut>', lambda e: threading.Thread(target=self._on_min_duration_changed, daemon=True).start())
            except Exception:
                pass

        self.current_label = ttk.Label(bottom, text="Not playing")
        self.current_label.pack(side=tk.RIGHT)

        # scan on start (in background)
        self.after(100, lambda: threading.Thread(target=self.scan_and_populate, daemon=True).start())

        self.paused = False
        # metadata cache: path -> dict
        self._metadata = {}
        # counter for excluded short files during scanning (updated on main thread)
        self._excluded_short = 0
        # persistent cache file path
        try:
            self.cache_path = Path.home() / CACHE_FILENAME
        except Exception:
            self.cache_path = Path(CACHE_FILENAME)

        # try to load existing cache so UI can populate faster (also loads theme)
        try:
            self._load_cache()
            # apply theme from cache before showing UI
            try:
                self.apply_theme()
            except Exception:
                pass
            # apply cached entries to UI immediately
            try:
                self.after(0, self._apply_cache_to_ui)
            except Exception:
                pass
        except Exception:
            pass

    def _begin_scan_ui(self):
        # Clear current visible lists and show scanning state (must run on main thread)
        try:
            self.listbox.delete(0, tk.END)
        except Exception:
            pass
        try:
            self.mp3_paths.clear()
        except Exception:
            pass
        try:
            self.all_mp3_paths.clear()
        except Exception:
            pass
        self._excluded_short = 0
        try:
            self.current_label.config(text="Scanning...")
        except Exception:
            pass

    def _apply_cache_to_ui(self):
        """Populate the visible list from the loaded cache quickly (main thread)."""
        try:
            # Clear current visible lists
            try:
                self.listbox.delete(0, tk.END)
            except Exception:
                pass
            self.mp3_paths.clear()
            for path, folder_title in self.all_mp3_paths:
                # populate seen set from cache so future scans don't duplicate
                try:
                    self._seen_paths.add(str(path))
                except Exception:
                    pass
                # apply current search filter
                q = (self.search_var.get() or '').strip().lower()
                if q:
                    meta = self._metadata.get(str(path), {})
                    searchable = [folder_title.lower(), str(meta.get('title', '')).lower(), str(meta.get('artist', '')).lower()]
                    if not any(q in s for s in searchable):
                        continue
                self.mp3_paths.append((path, folder_title))
                try:
                    self.listbox.insert(tk.END, folder_title)
                except Exception:
                    pass
            try:
                self.current_label.config(text=f"Found {len(self.all_mp3_paths)} audio files (cached)")
            except Exception:
                pass
            # update play mode button label to reflect loaded mode
            try:
                if self.mode_btn and self.play_mode:
                    mode_text = self.play_mode.capitalize()
                    self.mode_btn.config(text=f"Mode: {mode_text}")
            except Exception:
                pass
        except Exception:
            pass

    def apply_theme(self):
        """Apply the chosen theme (dark/light) to the UI widgets and ttk styles."""
        try:
            dark = bool(self.dark_mode_var.get())
            style = ttk.Style()
            # prefer 'clam' theme for better style control where available
            try:
                style.theme_use('clam')
            except Exception:
                try:
                    style.theme_use('default')
                except Exception:
                    pass

            if dark:
                # explicit dark palette
                bg = '#2e2e2e'
                fg = '#eaeaea'
                entry_bg = '#3a3a3a'
                list_bg = '#1e1e1e'
                select_bg = '#555555'
                button_bg = '#3a3a3a'
            else:
                # explicit light palette (avoid None to prevent type issues)
                bg = '#f0f0f0'
                fg = '#000000'
                entry_bg = '#ffffff'
                list_bg = '#ffffff'
                select_bg = '#3399ff'
                button_bg = '#e0e0e0'

            # configure ttk styles
            try:
                style.configure('TFrame', background=bg)
                style.configure('TLabel', background=bg, foreground=fg)
                style.configure('TButton', background=button_bg, foreground=fg)
                style.configure('TEntry', fieldbackground=entry_bg, foreground=fg)
                style.configure('Horizontal.TScale', background=bg)
                style.configure('TScrollbar', background=bg)
                # progressbar styling (may vary by platform)
                try:
                    style.configure('Horizontal.TProgressbar', background='#4CAF50')
                except Exception:
                    pass
            except Exception:
                pass

            # apply to some direct tk widgets
            try:
                self.configure(bg=bg)
            except Exception:
                pass
            try:
                self.listbox.config(bg=list_bg, fg=fg, selectbackground=select_bg, highlightbackground=bg)
            except Exception:
                pass
        except Exception:
            pass

    def _on_theme_changed(self):
        """Callback when theme checkbox toggled: apply theme and save settings to cache."""
        try:
            self.apply_theme()
        except Exception:
            pass
        try:
            # save settings into cache immediately
            self._save_cache()
        except Exception:
            pass

    def _load_cache(self):
        """Load cached discovery file if present and validate entries.
        Cache format: list of {path, folder_title, meta: {...}} where meta may contain '__mtime' and '__size'.
        """
        try:
            if not getattr(self, 'cache_path', None):
                return
            if not self.cache_path.exists():
                return
            try:
                with self.cache_path.open('r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                return

            # support both old-list format and new dict-with-settings format
            settings = {}
            if isinstance(data, dict):
                settings = data.get('settings', {}) or {}
                items = data.get('items') or data.get('out') or []
            else:
                items = data
            # apply settings (e.g., dark mode)
            try:
                if settings.get('dark_mode') is not None:
                    self.dark_mode_var.set(bool(settings.get('dark_mode')))
                if settings.get('play_mode'):
                    self.play_mode = settings.get('play_mode')
            except Exception:
                pass

            # validate and load
            self.all_mp3_paths.clear()
            for rec in items:
                try:
                    p = Path(rec['path'])
                    if not p.exists():
                        continue
                    folder_title = rec.get('folder_title') or strip_leading_numbers(p.parent.name)
                    self.all_mp3_paths.append((p, folder_title))
                    # restore metadata
                    meta = rec.get('meta', {})
                    if meta:
                        self._metadata[str(p)] = meta
                except Exception:
                    pass
        except Exception:
            return

    def _save_cache(self):
        """Persist current discovery results to cache for faster next startup."""
        try:
            if not getattr(self, 'cache_path', None):
                return
            out = []
            for p, folder_title in self.all_mp3_paths:
                try:
                    rec = {'path': str(p), 'folder_title': folder_title}
                    meta = self._metadata.get(str(p))
                    if meta:
                        rec['meta'] = meta
                    out.append(rec)
                except Exception:
                    pass
            try:
                settings = {}
                try:
                    settings['dark_mode'] = bool(self.dark_mode_var.get())
                except Exception:
                    settings['dark_mode'] = False
                try:
                    settings['play_mode'] = self.play_mode
                except Exception:
                    settings['play_mode'] = 'sequential'
                payload = {'items': out, 'settings': settings}
                with self.cache_path.open('w', encoding='utf-8') as f:
                    json.dump(payload, f)
            except Exception:
                pass
        except Exception:
            pass

    def _inc_excluded_short(self):
        try:
            self._excluded_short += 1
            # update status label
            try:
                min_d = self.min_duration_var.get() if hasattr(self, 'min_duration_var') else MIN_DURATION_SECONDS
                self.current_label.config(text=f"Found {len(self.all_mp3_paths)} audio files (excluded {self._excluded_short} < {min_d}s)")
            except Exception:
                pass
        except Exception:
            pass

    def _on_min_duration_changed(self):
        """Called when the min duration entry changes: trigger a re-scan so UI reflects new cutoff."""
        try:
            # parse user input from string var, update IntVar with a safe integer
            try:
                s = (self.min_duration_strvar.get() or '').strip()
                if s == '':
                    val = MIN_DURATION_SECONDS
                else:
                    val = int(s)
            except Exception:
                val = MIN_DURATION_SECONDS
            try:
                self.min_duration_var.set(val)
                # keep the string in sync (normalize formatting)
                self.min_duration_strvar.set(str(val))
            except Exception:
                pass
            # kick off a background re-scan (scan_and_populate already schedules UI updates)
            threading.Thread(target=self.scan_and_populate, daemon=True).start()
        except Exception:
            pass

    def _add_discovered_file(self, full: Path, folder_title: str, meta: dict):
        """Add a single discovered file to internal lists and the visible listbox (main thread)."""
        try:
            key = str(full)
            # avoid adding duplicates if this path was already known/displayed
            if key in self._seen_paths:
                # still merge metadata if provided
                try:
                    if meta:
                        self._metadata[key] = {**self._metadata.get(key, {}), **meta}
                except Exception:
                    pass
                return

            # Re-check duration here to avoid adding files that were mis-measured
            try:
                dur = meta.get('duration') if meta else 0
            except Exception:
                dur = 0
            if not dur:
                try:
                    dur = ensure_duration(full, self._metadata)
                except Exception:
                    dur = 0
            min_d = self.min_duration_var.get() if hasattr(self, 'min_duration_var') else MIN_DURATION_SECONDS
            if dur and dur < min_d:
                # count as excluded and do not add
                try:
                    self.after(0, self._inc_excluded_short)
                except Exception:
                    pass
                return
            key = str(full)
            # mark as seen so future scans won't re-add
            try:
                self._seen_paths.add(key)
            except Exception:
                pass
            # merge metadata for this file
            try:
                if meta:
                    self._metadata[key] = {**self._metadata.get(key, {}), **meta}
            except Exception:
                pass
            # add to master list
            try:
                self.all_mp3_paths.append((full, folder_title))
            except Exception:
                pass

            # decide if matches current search
            q = (self.search_var.get() or '').strip().lower()
            match = True
            if q:
                searchable = [folder_title.lower()]
                try:
                    if meta and meta.get('title'):
                        searchable.append(str(meta.get('title')).lower())
                except Exception:
                    pass
                try:
                    if meta and meta.get('artist'):
                        searchable.append(str(meta.get('artist')).lower())
                except Exception:
                    pass
                match = any(q in s for s in searchable)

            if match:
                # add to visible list
                try:
                    self.mp3_paths.append((full, folder_title))
                    self.listbox.insert(tk.END, folder_title)
                except Exception:
                    pass

            # update status label with running count
            try:
                if self._excluded_short:
                    self.current_label.config(text=f"Found {len(self.all_mp3_paths)} audio files (excluded {self._excluded_short} < {min_d}s)")
                else:
                    self.current_label.config(text=f"Found {len(self.all_mp3_paths)} audio files")
            except Exception:
                pass
        except Exception:
            pass

    def browse_folder(self):
        path = filedialog.askdirectory(initialdir=str(self.songs_dir) if self.songs_dir.exists() else None)
        if path:
            self.songs_dir = Path(path)
            self.dir_label.config(text=f"Songs dir: {self.songs_dir}")
            threading.Thread(target=self.scan_and_populate, daemon=True).start()

    def scan_and_populate(self):
        # Perform file discovery and metadata retrieval on background thread,
        # but apply UI updates on the main thread to avoid tkinter thread-safety issues.
        try:
            if not self.songs_dir.exists():
                # schedule UI update to show not found
                self.after(0, lambda: self.listbox.insert(tk.END, "(Songs directory not found)"))
                return
        except Exception as e:
            print(f"Error checking songs_dir: {e}")
            self.after(0, lambda: self.listbox.insert(tk.END, "(Songs directory error)"))
            return

        # indicate scanning but do not clear the currently-displayed list;
        # we want incremental discovery that preserves what's already shown
        try:
            self.after(0, lambda: self.current_label.config(text="Scanning..."))
        except Exception:
            pass

        local_all = []
        local_meta = {}
        excluded_short = 0

        # Process each folder and pick only the first supported audio file in it
        for root, dirs, files in sorted(os_walk(self.songs_dir)):
            try:
                # find the first filename in sorted order that matches supported extensions
                first_fn = None
                for fn in sorted(files):
                    if any(fn.lower().endswith(ext) for ext in SUPPORTED_AUDIO_EXTS):
                        first_fn = fn
                        break
                if not first_fn:
                    continue
                full = Path(root) / first_fn
                # try to reuse cached metadata if file unchanged
                key = str(full)
                meta = {}
                try:
                    mtime = full.stat().st_mtime
                    size = full.stat().st_size
                except Exception:
                    mtime = None
                    size = None

                cached = self._metadata.get(key)
                if cached and mtime is not None and size is not None and cached.get('__mtime') == mtime and cached.get('__size') == size:
                    meta = cached
                    # already have duration and tags from cache
                else:
                    meta = get_mp3_metadata(full)
                    if mtime is not None:
                        meta['__mtime'] = mtime
                    if size is not None:
                        meta['__size'] = size
                # ensure duration is known (may compute and cache). Prefer cached value.
                dur = meta.get('duration') or 0
                if not dur:
                    try:
                        dur = ensure_duration(full, self._metadata)
                        if dur:
                            meta['duration'] = dur
                    except Exception:
                        dur = 0
                # skip very short files (count as excluded)
                min_d = self.min_duration_var.get() if hasattr(self, 'min_duration_var') else MIN_DURATION_SECONDS
                if dur and dur < min_d:
                    excluded_short += 1
                    continue

                folder_title = strip_leading_numbers(full.parent.name)
                local_all.append((full, folder_title))
                # add this file to the UI immediately
                try:
                    self.after(0, lambda p=full, t=folder_title, m=meta: self._add_discovered_file(p, t, m))
                except Exception:
                    pass
            except Exception:
                # ignore errors per-folder
                continue

        # Apply results to UI on main thread
        def apply_results():
            try:
                # merge remaining metadata
                try:
                    self._metadata.update(local_meta)
                except Exception:
                    pass
                # final status update
                count = len(self.all_mp3_paths)
                if count == 0:
                    try:
                        self.current_label.config(text="No audio files found")
                    except Exception:
                        pass
                if excluded_short:
                    try:
                        self.current_label.config(text=f"Found {count} audio files (excluded {excluded_short} < {min_d}s)")
                    except Exception:
                        pass
                else:
                    try:
                        self.current_label.config(text=f"Found {count} audio files")
                    except Exception:
                        pass
                print(f"scan_and_populate: found {count} audio files in {self.songs_dir} (excluded_short={excluded_short})")
                try:
                    self._save_cache()
                except Exception:
                    pass
            except Exception as e:
                print(f"Error applying scan results: {e}")

        self.after(0, apply_results)

    def play_selected(self):
        idx = self.listbox.curselection()
        if not idx:
            messagebox.showinfo("Select", "Please select an audio file from the list.")
            return
        index = idx[0]
        try:
            path = self.mp3_paths[index][0]
        except IndexError:
            return
        self._play_path(path)

    def on_double_click(self, event):
        self.play_selected()

    def _play_path(self, path: Path):
        try:
            if not audio.load_and_play(str(path)):
                messagebox.showerror("Playback error", f"Failed to play {path}")
                return
            
            # display folder title as the song name
            folder_title = strip_leading_numbers(path.parent.name)
            # if we stored folder title in mp3_paths, prefer that
            for p, t in self.mp3_paths:
                if p == path:
                    folder_title = t
                    break
            self.current_label.config(text=f"Playing: {folder_title}")
            # Update now-playing display (thumbnail + title)
            self.now_title_label.config(text=f"Now: {folder_title}")
            # also update the right-side metadata panel to reflect the playing file
            try:
                self._update_meta_display(path)
            except Exception:
                pass
            # start updating progress
            self._playing_path = path
            # Initialize manual timing base so progress/time are consistent
            try:
                self._start_time = time.time()
            except Exception:
                self._start_time = None
            self._pause_time = None
            self._paused_offset = 0.0
            # cancel previous updater if any
            if self._progress_after_id:
                try:
                    self.after_cancel(self._progress_after_id)
                except Exception:
                    pass
                self._progress_after_id = None
            # ensure pause button shows correct action when starting playback
            try:
                self.pause_btn.config(text="Pause")
            except Exception:
                pass
            self.update_progress()
            # load background thumbnail if available
            bg = get_osu_background(path.parent)
            if bg and HAS_PIL and Image and ImageTk:
                try:
                    img = Image.open(bg)
                    # create thumbnail keeping aspect ratio, fit into 120x80
                    resampling = getattr(Image, 'Resampling', None)
                    if resampling is not None:
                        resample = getattr(resampling, 'LANCZOS', None)
                    else:
                        resample = getattr(Image, 'LANCZOS', None)
                    if resample is not None:
                        img.thumbnail((120, 80), resample)
                    else:
                        img.thumbnail((120, 80))
                    photo = ImageTk.PhotoImage(img)
                    self.now_image_label.config(image=photo)
                    setattr(self.now_image_label, '_photo_ref', photo)
                except Exception:
                    self.now_image_label.config(image='')
                    if hasattr(self.now_image_label, '_photo_ref'):
                        delattr(self.now_image_label, '_photo_ref')
            else:
                self.now_image_label.config(image='')
                if hasattr(self.now_image_label, '_photo_ref'):
                    delattr(self.now_image_label, '_photo_ref')
            self.paused = False
        except Exception as e:
            messagebox.showerror("Playback error", f"Failed to play {path}: {e}")

    def toggle_pause(self):
        if not audio.is_audio_initialized():
            return
        # If nothing is playing, do nothing
        if not self._playing_path:
            return

        if not self.paused:
            audio.pause()
            self.paused = True
            self.pause_btn.config(text="Resume")
            self.current_label.config(text=self.current_label.cget("text") + " (paused)")
            # record pause time for manual timing calculations
            try:
                self._pause_time = time.time()
            except Exception:
                self._pause_time = None
        else:
            # Attempt to unpause; if unpause isn't supported by backend, fall back
            unpaused = audio.unpause()
            if not unpaused:
                # compute paused position from manual timer
                pos_sec = 0
                try:
                    if self._start_time and self._pause_time:
                        pos_sec = (self._pause_time - self._start_time) + self._paused_offset
                except Exception:
                    pos_sec = 0
                try:
                    audio.restart_playback(str(self._playing_path))
                    self.seek_to(pos_sec)
                except Exception:
                    pass

            self.paused = False
            self.pause_btn.config(text="Pause")
            # remove (paused) suffix
            txt = self.current_label.cget("text").replace(" (paused)", "")
            self.current_label.config(text=txt)
            # adjust manual timing to account for pause duration
            try:
                if self._pause_time and self._start_time:
                    pause_duration = time.time() - self._pause_time
                    self._start_time += pause_duration
            except Exception:
                pass
            self._pause_time = None

    def stop(self):
        audio.stop()
        self.current_label.config(text="Not playing")
        # clear now-playing and cancel progress updates
        self._playing_path = None
        # clear manual timing
        self._start_time = None
        self._pause_time = None
        self._paused_offset = 0.0
        # reset pause button state
        try:
            self.pause_btn.config(text="Pause")
        except Exception:
            pass
        self.paused = False
        if self._progress_after_id:
            try:
                self.after_cancel(self._progress_after_id)
            except Exception:
                pass
            self._progress_after_id = None
        self.now_title_label.config(text="Now: Not playing")
        self.now_image_label.config(image='')
        if hasattr(self.now_image_label, '_photo_ref'):
            delattr(self.now_image_label, '_photo_ref')
        self.progress['value'] = 0
        self.time_label.config(text="0:00 / 0:00")

    def toggle_loop(self):
        """Toggle looping of the current song. When enabled, the current track will replay after ending."""
        try:
            # kept for backwards-compat; map into play_mode
            if self.play_mode == 'loop':
                self.play_mode = 'sequential'
            else:
                self.play_mode = 'loop'
            try:
                if self.mode_btn:
                    mode_text = self.play_mode.capitalize()
                    self.mode_btn.config(text=f"Mode: {mode_text}")
            except Exception:
                pass
        except Exception:
            pass

    def cycle_play_mode(self):
        """Cycle play mode between 'sequential' -> 'loop' -> 'shuffle' -> sequential."""
        try:
            if self.play_mode == 'sequential':
                self.play_mode = 'loop'
            elif self.play_mode == 'loop':
                self.play_mode = 'shuffle'
            else:
                self.play_mode = 'sequential'
            # update button text
            try:
                if self.mode_btn:
                    mode_text = self.play_mode.capitalize()
                    self.mode_btn.config(text=f"Mode: {mode_text}")
            except Exception:
                pass
            # persist mode into cache
            try:
                self._save_cache()
            except Exception:
                pass
        except Exception:
            pass

    def _on_track_end(self):
        """Called when the current track finishes playing. Decide whether to loop or play next."""
        try:
            # if loop enabled, restart same track
            if self.play_mode == 'loop' and self._playing_path:
                try:
                    audio.restart_playback(str(self._playing_path))
                except Exception:
                    pass
                try:
                    self._start_time = time.time()
                except Exception:
                    self._start_time = None
                self._pause_time = None
                self._paused_offset = 0.0
                # continue progress polling
                try:
                    if self._progress_after_id:
                        try:
                            self.after_cancel(self._progress_after_id)
                        except Exception:
                            pass
                    self._progress_after_id = self.after(500, self.update_progress)
                except Exception:
                    pass
                return

            # otherwise, play the next visible song (in self.mp3_paths)
            try:
                if not self._playing_path:
                    return

                # If shuffle mode, pick a random song from the whole library (`all_mp3_paths`).
                if self.play_mode == 'shuffle':
                    if not self.all_mp3_paths:
                        return
                    # avoid picking the same track if possible
                    candidates = [p for p, t in self.all_mp3_paths if p != self._playing_path]
                    if not candidates:
                        candidates = [p for p, t in self.all_mp3_paths]
                    if candidates:
                        next_path = random.choice(candidates)
                        try:
                            # find the matching entry in mp3_paths to highlight in UI
                            for i, (p, t) in enumerate(self.mp3_paths):
                                if p == next_path:
                                    try:
                                        self.listbox.selection_clear(0, tk.END)
                                        self.listbox.selection_set(i)
                                        self.listbox.see(i)
                                    except Exception:
                                        pass
                                    break
                            self._play_path(next_path)
                        except Exception:
                            pass
                    return

                # otherwise behave sequentially within visible list
                # find current index in visible list (mp3_paths)
                cur_index = None
                for i, (p, t) in enumerate(self.mp3_paths):
                    if p == self._playing_path:
                        cur_index = i
                        break
                if cur_index is None:
                    # playing file not in visible list, stop
                    self.stop()
                    return
                next_index = cur_index + 1
                if next_index >= len(self.mp3_paths):
                    # end of list
                    self.stop()
                    return
                # select and play next
                next_path = self.mp3_paths[next_index][0]
                try:
                    self.listbox.selection_clear(0, tk.END)
                    self.listbox.selection_set(next_index)
                    self.listbox.see(next_index)
                    self._play_path(next_path)
                except Exception:
                    pass
            except Exception:
                pass
        except Exception:
            pass

    def toggle_fullscreen(self, event=None):
        try:
            # Toggle between zoomed (maximized) and normal windowed state
            if self.state() == 'zoomed':
                self.state('normal')
                w = int(self.winfo_screenwidth() * 0.8)
                h = int(self.winfo_screenheight() * 0.8)
                self.geometry(f"{w}x{h}")
            else:
                self.state('zoomed')
        except Exception:
            pass

    def on_volume_change(self, val):
        """Callback for volume scale. `val` is a string from the scale command."""
        try:
            v = float(val)
        except Exception:
            try:
                v = self.volume_var.get()
            except Exception:
                return
        audio.set_volume(v)
        try:
            # update label
            self.volume_label.config(text=f"Vol: {int(v*100)}%")
        except Exception:
            pass

    def on_select(self, event):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        try:
            path = self.mp3_paths[idx][0]
        except IndexError:
            return
        meta = self._metadata.get(str(path), {})
        # display song name based on folder name
        title = strip_leading_numbers(path.parent.name)
        artist = meta.get('artist') or ''
        if not artist:
            # parse artist from folder name if not present in tags
            artist = parse_artist_from_folder(title) or ''
            # persist parsed artist into metadata cache so it is available later
            try:
                key = str(path)
                meta_entry = self._metadata.get(key, {})
                if not meta_entry.get('artist'):
                    meta_entry['artist'] = artist
                    self._metadata[key] = meta_entry
            except Exception:
                pass
        album = meta.get('album') or ''
        duration = format_duration(meta.get('duration')) if meta.get('duration') else ''
        self.meta_title.config(text=f"Title: {title}")
        self.meta_artist.config(text=f"Artist: {artist}")
        self.meta_album.config(text=f"Album: {album}")
        self.meta_duration.config(text=f"Duration: {duration}")
        self.meta_path.config(text=f"Path: {path}")
        # Try to load background from the first .osu file in the folder
        bg = get_osu_background(path.parent)
        if bg and HAS_PIL and Image and ImageTk:
            try:
                img = Image.open(bg)
                # create thumbnail keeping aspect ratio, fit into 220x140
                resampling = getattr(Image, 'Resampling', None)
                if resampling is not None:
                    resample = getattr(resampling, 'LANCZOS', None)
                else:
                    resample = getattr(Image, 'LANCZOS', None)
                if resample is not None:
                    img.thumbnail((220, 140), resample)
                else:
                    img.thumbnail((220, 140))
                photo = ImageTk.PhotoImage(img)
                self.meta_image_label.config(image=photo)
                # retain reference on the label widget to avoid GC
                setattr(self.meta_image_label, '_photo_ref', photo)
            except Exception:
                # clear image on error
                self.meta_image_label.config(image='')
                if hasattr(self.meta_image_label, '_photo_ref'):
                    delattr(self.meta_image_label, '_photo_ref')
        else:
            # clear image if none found or PIL missing
            self.meta_image_label.config(image='')
            if hasattr(self.meta_image_label, '_photo_ref'):
                delattr(self.meta_image_label, '_photo_ref')

    def _update_meta_display(self, path: Path):
        """Update the right-side metadata panel (title/artist/album/duration/path/image) for `path`."""
        try:
            meta = self._metadata.get(str(path), {})
            # display song name based on folder name
            title = strip_leading_numbers(path.parent.name)
            artist = meta.get('artist') or ''
            if not artist:
                artist = parse_artist_from_folder(title) or ''
                # persist parsed artist into metadata cache
                try:
                    key = str(path)
                    meta_entry = self._metadata.get(key, {})
                    if not meta_entry.get('artist'):
                        meta_entry['artist'] = artist
                        self._metadata[key] = meta_entry
                except Exception:
                    pass
            album = meta.get('album') or ''
            duration = format_duration(meta.get('duration')) if meta.get('duration') else ''
            try:
                self.meta_title.config(text=f"Title: {title}")
                self.meta_artist.config(text=f"Artist: {artist}")
                self.meta_album.config(text=f"Album: {album}")
                self.meta_duration.config(text=f"Duration: {duration}")
                self.meta_path.config(text=f"Path: {path}")
            except Exception:
                pass

            # load background image for meta panel
            bg = get_osu_background(path.parent)
            if bg and HAS_PIL and Image and ImageTk:
                try:
                    img = Image.open(bg)
                    resampling = getattr(Image, 'Resampling', None)
                    if resampling is not None:
                        resample = getattr(resampling, 'LANCZOS', None)
                    else:
                        resample = getattr(Image, 'LANCZOS', None)
                    if resample is not None:
                        img.thumbnail((220, 140), resample)
                    else:
                        img.thumbnail((220, 140))
                    photo = ImageTk.PhotoImage(img)
                    self.meta_image_label.config(image=photo)
                    setattr(self.meta_image_label, '_photo_ref', photo)
                except Exception:
                    self.meta_image_label.config(image='')
                    if hasattr(self.meta_image_label, '_photo_ref'):
                        delattr(self.meta_image_label, '_photo_ref')
            else:
                try:
                    self.meta_image_label.config(image='')
                except Exception:
                    pass
                if hasattr(self.meta_image_label, '_photo_ref'):
                    delattr(self.meta_image_label, '_photo_ref')
        except Exception:
            pass

    def _on_listbox_motion(self, event):
        """Schedule showing a tooltip near the mouse with the full list item text after a short delay."""
        try:
            lb = event.widget
            idx = lb.nearest(event.y)
            if idx is None:
                self._hide_title_tooltip()
                return
            try:
                text = lb.get(idx)
            except Exception:
                text = ''
            if not text:
                self._hide_title_tooltip()
                return

            # if mouse is still over same index, don't reschedule
            if self._last_tooltip_index == idx and self._title_tooltip:
                # update position if visible
                try:
                    if self._title_tooltip.winfo_exists():
                        x = event.x_root + 12
                        y = event.y_root + 18
                        self._title_tooltip.wm_geometry(f"+{x}+{y}")
                except Exception:
                    pass
                return

            self._last_tooltip_index = idx
            # cancel previous scheduled show
            try:
                if self._tooltip_after_id:
                    self.after_cancel(self._tooltip_after_id)
            except Exception:
                pass

            # schedule showing tooltip after delay
            try:
                x = event.x_root + 12
                y = event.y_root + 18
                self._tooltip_after_id = self.after(self._tooltip_delay_ms, lambda: self._show_title_tooltip(x, y, text, idx))
            except Exception:
                pass
        except Exception:
            pass

    def _hide_title_tooltip(self, event=None):
        try:
            if self._title_tooltip:
                try:
                    self._title_tooltip.destroy()
                except Exception:
                    pass
                self._title_tooltip = None
            # cancel any scheduled show
            try:
                aid = getattr(self, '_tooltip_after_id', None)
                if aid is not None:
                    if hasattr(self, 'after_cancel') and callable(self.after_cancel):
                        self.after_cancel(aid)
            except Exception:
                pass
            self._tooltip_after_id = None
            self._last_tooltip_index = None
        except Exception:
            pass

    def _show_title_tooltip(self, x, y, text, idx):
        """Create and show the tooltip immediately at x,y with given text."""
        try:
            # clear any previous tooltip
            try:
                if self._title_tooltip:
                    self._title_tooltip.destroy()
            except Exception:
                pass

            dark = bool(self.dark_mode_var.get()) if hasattr(self, 'dark_mode_var') else False
            if dark:
                bg = '#222222'
                fg = '#f0f0f0'
            else:
                bg = '#ffffe0'
                fg = '#000000'

            tw = tk.Toplevel(self)
            tw.wm_overrideredirect(True)
            # use tk.Label for easier bg/fg control
            lbl = tk.Label(tw, text=text, bg=bg, fg=fg, bd=1, relief='solid')
            lbl.pack(ipadx=6, ipady=3)
            try:
                tw.wm_geometry(f"+{x}+{y}")
            except Exception:
                pass
            self._title_tooltip = tw
            # clear scheduled id
            self._tooltip_after_id = None
        except Exception:
            pass

    def _clear_search(self):
        self.search_var.set('')
        self.refresh_list()

    def update_progress(self):
        """Poll playback position and update the progress bar and time label."""
        try:
            path = self._playing_path
            if not path or not audio.is_audio_initialized():
                return

            total = self._metadata.get(str(path), {}).get('duration') or 0
            # if duration unknown, try to compute and cache it
            if not total:
                total = ensure_duration(path, self._metadata)

            # Prefer manual timing base for progress display
            busy = audio.is_busy()
            if not busy and not self.paused:
                # playback finished; handle end-of-track behavior (loop or advance)
                try:
                    if self._progress_after_id:
                        self.after_cancel(self._progress_after_id)
                    self._progress_after_id = None
                except Exception:
                    pass
                try:
                    self.after(100, self._on_track_end)
                except Exception:
                    self.stop()
                return

            # Compute position using manual base when possible
            pos_sec = 0
            try:
                if self._start_time is not None:
                    if self.paused and self._pause_time:
                        pos_sec = (self._pause_time - self._start_time) + self._paused_offset
                    else:
                        pos_sec = (time.time() - self._start_time) + self._paused_offset
                else:
                    # fallback to pygame get_pos
                    pos_ms = audio.get_pos()
                    pos_sec = pos_ms / 1000.0
            except Exception:
                # fallback to pygame get_pos
                try:
                    pos_ms = audio.get_pos()
                    pos_sec = pos_ms / 1000.0
                except Exception:
                    pos_sec = 0

            if total:
                frac = min(1.0, pos_sec / total)
                self.progress['value'] = int(frac * 1000)
                self.time_label.config(text=f"{format_duration(int(pos_sec))} / {format_duration(total)}")
            else:
                # unknown total
                self.progress['value'] = 0
                self.time_label.config(text=f"{format_duration(int(pos_sec))} / 0:00")

            # schedule next poll
            self._progress_after_id = self.after(500, self.update_progress)
        except Exception:
            self._progress_after_id = None

    def refresh_list(self):
        """Refresh visible listbox entries based on `self.search_var`.
        Matches against folder title, cached tag title, and artist (case-insensitive substring).
        """
        q = (self.search_var.get() or '').strip().lower()
        self.listbox.delete(0, tk.END)
        self.mp3_paths.clear()
        for path, folder_title in self.all_mp3_paths:
            # gather searchable strings
            searchable = [folder_title.lower()]
            meta = self._metadata.get(str(path), {})
            if meta.get('title'):
                searchable.append(str(meta.get('title')).lower())
            if meta.get('artist'):
                searchable.append(str(meta.get('artist')).lower())

            # decide if item matches query
            match = True
            if q:
                match = any(q in s for s in searchable)

            if match:
                self.mp3_paths.append((path, folder_title))
                self.listbox.insert(tk.END, folder_title)

    def on_progress_click(self, event):
        """Handle click/drag on the progress bar to seek."""
        try:
            widget = event.widget
            w = widget.winfo_width()
            if w <= 0:
                return
            x = event.x
            frac = max(0.0, min(1.0, x / w))
            # compute target seconds
            if not self._playing_path:
                return
            total = self._metadata.get(str(self._playing_path), {}).get('duration') or ensure_duration(self._playing_path, self._metadata)
            if not total:
                return
            target = frac * total
            self.seek_to(target)
        except Exception:
            pass

    def seek_to(self, pos_sec: float):
        """Seek to pos_sec (seconds) in the currently playing file."""
        if not self._playing_path:
            return
        # clamp
        total = self._metadata.get(str(self._playing_path), {}).get('duration') or ensure_duration(self._playing_path, self._metadata)
        if total and pos_sec > total:
            pos_sec = total
        try:
            # Attempt several seek methods in order for best compatibility
            success = audio.seek_set_pos(pos_sec)
            if not success:
                success = audio.seek_play_start(pos_sec)
            if not success:
                audio.restart_playback(str(self._playing_path))
            
            # update manual timing regardless of which method succeeded
            self._start_time = time.time() - float(pos_sec)
            self._pause_time = None
            self._paused_offset = 0.0
            self.paused = False
            # restart progress polling
            if self._progress_after_id:
                try:
                    self.after_cancel(self._progress_after_id)
                except Exception:
                    pass
            self.update_progress()
        except Exception:
            pass
