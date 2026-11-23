# skid (`yt_mp3_tagger`)

Локальная GUI-утилита на Python (`tkinter`) для скачивания аудио с YouTube в MP3 через `yt-dlp` и `ffmpeg` с базовым редактором ID3-тегов (`mutagen`) и переименованием файла.

## Возможности

- Проверка ссылки на YouTube (`youtube.com`, `youtu.be`, `m.youtube.com`, `music.youtube.com`).
- Выбор битрейта при перекодировании в MP3 (`128`, `192`, `320` kbps) и индикатор прогресса.
- Редактирование `Artist` и `Title`, а также имени файла (без `.mp3`) после загрузки.
- Сброс интерфейса в исходное состояние после сохранения тегов.
- Быстрое открытие каталога `downloads` и запись лога `yt_mp3_tagger.log` (очищается при каждом запуске).

## Зависимости

- Python 3.9+ и `tkinter`
- `ffmpeg` в системе (`PATH`)
- Python-пакеты: `yt-dlp[default]`, `mutagen`

## Установка и запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U "yt-dlp[default]" mutagen
python yt_mp3_tagger.py