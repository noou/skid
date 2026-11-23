from pathlib import Path
import logging
import platform
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.parse import parse_qs, urlparse
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError
import yt_dlp


LOG_PATH = Path(__file__).resolve().with_name("yt_mp3_tagger.log")
log = logging.getLogger("yt_mp3_tagger")


def setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    # Очищаем предыдущий лог-файл перед новым запуском
    log_file.write_text("", encoding="utf-8")

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s")

    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)


def install_exception_hooks() -> None:
    def _handle_sys_exc(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.critical("Необработанное исключение", exc_info=(exc_type, exc_value, exc_tb))

    def _handle_thread_exc(args: threading.ExceptHookArgs) -> None:
        if issubclass(args.exc_type, SystemExit):
            return
        thread_name = args.thread.name if args.thread else "unknown"
        log.critical(
            "Необработанное исключение в потоке %s",
            thread_name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _handle_sys_exc
    threading.excepthook = _handle_thread_exc


def is_valid_youtube_url(raw_url: str) -> bool:
    try:
        parsed = urlparse(raw_url.strip())
    except ValueError:
        return False

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False

    host = parsed.netloc.lower().removeprefix("www.")
    path_parts = [p for p in parsed.path.split("/") if p]

    if host == "youtu.be":
        return len(path_parts) >= 1 and bool(path_parts[0])

    if host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            vid = parse_qs(parsed.query).get("v", [""])[0]
            return bool(vid)
        if len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live"}:
            return bool(path_parts[1])

    return False


def sanitize_filename_stem(raw_name: str) -> str:
    cleaned = raw_name.strip()
    if cleaned.lower().endswith(".mp3"):
        cleaned = cleaned[:-4].strip()
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", cleaned).strip(" .")
    return cleaned


class App(tk.Tk):
    def __init__(self) -> None:
        log.info("Инициализация App: python=%s, platform=%s", sys.executable, platform.platform())
        super().__init__()
        self.title("YT -> MP3 + ID3")
        self.geometry("470x340")
        self.minsize(470, 320)

        self.out_dir = Path("downloads")
        self.out_dir.mkdir(exist_ok=True)
        log.debug("Каталог выгрузки: %s", self.out_dir.resolve())

        self.current_mp3: Path | None = None

        self._build_ui()
        self._set_editor_enabled(False)
        log.info("UI построен, состояние: ожидание ссылки")

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}

        tk.Label(self, text="Ссылка на YouTube:").pack(anchor="w", **pad)
        row_url = tk.Frame(self)
        row_url.pack(fill="x", padx=10)

        self.url_var = tk.StringVar()
        tk.Entry(row_url, textvariable=self.url_var).pack(side="left", fill="x", expand=True)

        self.bitrate_var = tk.StringVar(value="192")
        ttk.Combobox(
            row_url,
            textvariable=self.bitrate_var,
            values=["128", "192", "320"],
            width=6,
            state="readonly",
        ).pack(side="left", padx=(6, 0))

        row_btn = tk.Frame(self)
        row_btn.pack(fill="x", padx=10, pady=(6, 4))

        self.dl_btn = tk.Button(row_btn, text="Скачать MP3", command=self.start_download)
        self.dl_btn.pack(side="left")

        tk.Button(row_btn, text="Каталог", command=self.open_catalog).pack(
            side="left", padx=(6, 0)
        )

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=10, pady=(4, 2))

        self.status_var = tk.StringVar(value="Ждём ссылку")
        self.status_label = tk.Label(
            self, textvariable=self.status_var, anchor="w", justify="left", wraplength=445
        )
        self.status_label.pack(fill="x", padx=10)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=8)

        self.id3_header = tk.Label(self, text="Редактор ID3-тегов:")
        self.id3_header.pack(anchor="w", padx=10)

        self.form = tk.Frame(self)
        self.form.pack(fill="x", padx=10)
        self.form.columnconfigure(1, weight=1)

        self.fields: dict[str, tk.Entry] = {}
        tag_rows = [
            ("artist", "Исполнитель (Artist)"),
            ("title", "Название (Title)"),
        ]
        for row_idx, (key, text) in enumerate(tag_rows):
            tk.Label(self.form, text=text, width=22, anchor="w").grid(
                row=row_idx, column=0, pady=2
            )
            entry = tk.Entry(self.form)
            entry.grid(row=row_idx, column=1, sticky="ew", pady=2)
            self.fields[key] = entry

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=(8, 6))

        file_form = tk.Frame(self)
        file_form.pack(fill="x", padx=10)
        file_form.columnconfigure(1, weight=1)

        tk.Label(file_form, text="Имя файла (без .mp3):", width=22, anchor="w").grid(
            row=0, column=0, pady=2
        )
        self.filename_entry = tk.Entry(file_form)
        self.filename_entry.grid(row=0, column=1, sticky="ew", pady=2)

        self.save_btn = tk.Button(
            self, text="Сохранить теги", command=self.save_tags, state="disabled"
        )
        self.save_btn.pack(anchor="e", padx=10, pady=(8, 10))

    def _set_editor_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        header_fg = "#000000" if enabled else "#6b7280"
        self.id3_header.config(fg=header_fg)
        self.save_btn.config(state=state)
        self.filename_entry.config(state=state)
        for entry in self.fields.values():
            entry.config(state=state)
        log.debug("Редактор тегов переведён в состояние: %s", state)

    def _reset_to_idle(self) -> None:
        log.info("Сброс состояния в исходное ('Ждём ссылку')")
        self.current_mp3 = None
        self.url_var.set("")
        for entry in list(self.fields.values()) + [self.filename_entry]:
            entry.config(state="normal")
            entry.delete(0, tk.END)
        self._set_editor_enabled(False)
        self.dl_btn.config(state="normal")
        self.progress["value"] = 0
        self.status_var.set("Ждём ссылку")

    def open_catalog(self) -> None:
        target = str(self.out_dir.resolve())
        system = platform.system()
        log.info("Открытие каталога %s на платформе %s", target, system)
        try:
            if system == "Windows":
                subprocess.Popen(["explorer", target])
            elif system == "Darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as exc:
            log.exception("Ошибка при открытии каталога %s", target)
            messagebox.showerror("Ошибка", f"Не удалось открыть каталог: {exc}")

    def set_status(self, text: str, pct: float | None = None) -> None:
        def _apply() -> None:
            self.status_var.set(text)
            if pct is not None:
                self.progress["value"] = max(0.0, min(100.0, pct))

        self.after(0, _apply)

    def start_download(self) -> None:
        url = self.url_var.get().strip()
        bitrate = self.bitrate_var.get()
        log.info("Нажата кнопка скачивания: url=%r, bitrate=%s", url, bitrate)

        if not url:
            log.warning("Пустая ссылка")
            messagebox.showwarning("Пусто", "Вставь ссылку на YouTube.")
            return

        if not is_valid_youtube_url(url):
            log.warning("Ссылка не прошла валидацию YouTube: %r", url)
            messagebox.showwarning(
                "Некорректная ссылка",
                "Укажи корректную ссылку на видео YouTube (youtube.com или youtu.be).",
            )
            return

        self._set_editor_enabled(False)
        self.dl_btn.config(state="disabled")
        self.set_status("Запуск...", 0)

        threading.Thread(
            target=self._download_worker,
            name="yt-dlp-worker",
            args=(url, bitrate),
            daemon=True,
        ).start()

    def _download_worker(self, url: str, bitrate: str) -> None:
        log.info("Старт фоновой загрузки: url=%s, bitrate=%s", url, bitrate)
        last_logged_pct = -1

        def hook(d: dict) -> None:
            nonlocal last_logged_pct
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes", 0)
                pct = (done * 95.0 / total) if total else 0
                pct_int = int(pct)
                if pct_int // 10 > last_logged_pct // 10:
                    last_logged_pct = pct_int
                    log.debug("Прогресс скачивания: %d%%", pct_int)
                self.set_status(f"Скачивание: {pct:.0f}%", pct)
            elif status == "finished":
                log.info("Поток скачан, запуск конвертации в MP3")
                self.set_status("Конвертация в MP3...", 96)

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(self.out_dir / "%(title)s.%(ext)s"),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": bitrate,
                }
            ],
            "progress_hooks": [hook],
            "noplaylist": True,
            "quiet": True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                mp3_path = Path(ydl.prepare_filename(info)).with_suffix(".mp3")

            log.info("Загрузка и конвертация завершены: %s", mp3_path)
            self.after(0, lambda p=mp3_path, i=info: self._on_download_done(p, i))
        except Exception as exc:
            log.exception("Ошибка при скачивании/конвертации: %s", url)
            self.after(0, lambda err=str(exc): self._on_download_error(err))

    def _on_download_done(self, mp3_path: Path, info: dict) -> None:
        self.current_mp3 = mp3_path
        self.dl_btn.config(state="normal")
        self.set_status(f"Готово: {mp3_path.name}", 100)
        self._set_editor_enabled(True)
        self._load_tags_from_file(mp3_path, fallback_title=info.get("title") or mp3_path.stem)

    def _on_download_error(self, err: str) -> None:
        self.dl_btn.config(state="normal")
        self._set_editor_enabled(False)
        self.set_status("Ошибка", 0)
        messagebox.showerror("Ошибка загрузки", err)

    def _load_tags_from_file(self, path: Path, fallback_title: str) -> None:
        log.debug("Чтение ID3-тегов из %s", path)
        try:
            tags = EasyID3(path)
        except ID3NoHeaderError:
            log.debug("ID3-заголовок отсутствует, создаём новый для %s", path)
            tags = EasyID3()

        defaults = {
            "artist": tags.get("artist", [""])[0],
            "title": tags.get("title", [fallback_title])[0],
        }

        for key, entry in self.fields.items():
            entry.delete(0, tk.END)
            entry.insert(0, defaults[key])

        self.filename_entry.delete(0, tk.END)
        self.filename_entry.insert(0, path.stem)

    def save_tags(self) -> None:
        if not self.current_mp3 or not self.current_mp3.exists():
            log.warning("Попытка сохранить теги при отсутствующем файле: %s", self.current_mp3)
            messagebox.showwarning("Нет файла", "Сначала скачай MP3-файл.")
            self._reset_to_idle()
            return

        new_stem = sanitize_filename_stem(self.filename_entry.get())
        if not new_stem:
            log.warning("Пустое или некорректное имя файла при сохранении")
            messagebox.showwarning("Некорректное имя", "Укажи имя файла без .mp3.")
            return

        target_path = self.current_mp3.with_name(f"{new_stem}.mp3")
        if target_path != self.current_mp3:
            if target_path.exists():
                log.warning("Целевой файл уже существует: %s", target_path)
                messagebox.showerror(
                    "Файл уже существует",
                    f"Файл {target_path.name} уже есть в каталоге.",
                )
                return
            try:
                log.info("Переименование %s -> %s", self.current_mp3, target_path)
                self.current_mp3.rename(target_path)
                self.current_mp3 = target_path
            except Exception as exc:
                log.exception("Ошибка переименования файла в %s", target_path)
                messagebox.showerror("Ошибка переименования", str(exc))
                return

        try:
            tags = EasyID3(self.current_mp3)
        except ID3NoHeaderError:
            tags = EasyID3()

        for key, entry in self.fields.items():
            val = entry.get().strip()
            if val:
                tags[key] = [val]
            elif key in tags:
                del tags[key]

        tags.save(self.current_mp3)
        log.info("ID3-теги сохранены в %s: %s", self.current_mp3, dict(tags))
        messagebox.showinfo("Сохранено", f"Сохранено: {self.current_mp3.name}")
        self._reset_to_idle()


if __name__ == "__main__":
    setup_logging(LOG_PATH)
    install_exception_hooks()
    log.info("=== Запуск приложения yt_mp3_tagger ===")
    try:
        app = App()
        app.mainloop()
    except Exception:
        log.critical("Критическая ошибка в mainloop", exc_info=True)
        raise
    finally:
        log.info("=== Завершение работы yt_mp3_tagger ===")