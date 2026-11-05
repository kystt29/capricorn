"""Распознавание речи (Vosk офлайн + Google для точности) и синтез голоса."""
import asyncio
import ctypes
import json
import logging
import math
import os
import queue
import random
import re
import shutil
import struct
import tempfile
import threading
import time
import wave

log = logging.getLogger("capri")
RATE = 16000


def _model_complete(folder):
    return os.path.isfile(os.path.join(folder, "am", "final.mdl"))


def ascii_model_path(path):
    """Vosk не открывает пути с кириллицей (ошибка «синтаксическая ошибка в имени файла») — копируем
    модель туда, где путь латиницей. Если и имя пользователя Windows русское, его папки не подходят,
    поэтому дальше пробуем общие папки C:\\ProgramData и C:\\Users\\Public."""
    if path.isascii():
        return path
    bases = [os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
             os.environ.get("PUBLIC", r"C:\Users\Public"), tempfile.gettempdir()]
    for base in bases:
        target = os.path.join(base, "Capri", os.path.basename(path.rstrip("\\/")))
        if not base or not target.isascii():
            continue
        try:
            if not _model_complete(target):  # копия могла оборваться в прошлый раз
                shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(path, target)
            log.info("Модель распознавания скопирована в %s (в исходном пути есть кириллица)", target)
            return target
        except OSError as e:
            log.info("Не удалось скопировать модель в %s: %s", target, e)
    return path


def input_devices():
    """Устройства ввода [(название, индекс)] — по одному на микрофон, как в настройках звука Windows."""
    import sounddevice as sd
    apis = sd.query_hostapis()
    mme = next((i for i, a in enumerate(apis) if "MME" in a["name"]), None)
    out, seen = [], set()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0 or (mme is not None and d["hostapi"] != mme):
            continue
        name = d["name"].strip()
        low = name.lower()
        if "sound mapper" in low or "переназначение звуковых" in low or "первичный драйвер" in low:
            continue  # служебные «устройства по умолчанию»
        if name not in seen:
            seen.add(name)
            out.append((name, i))
    return out


def default_input_name():
    try:
        import sounddevice as sd
        return sd.query_devices(kind="input")["name"].strip()
    except Exception:
        return "неизвестно"


def resolve_device(name):
    """Индекс устройства по сохранённому названию (или None — микрофон Windows по умолчанию)."""
    if not name:
        return None
    devices = input_devices()
    for n, i in devices:
        if n == name:
            return i
    for n, i in devices:  # название могло обрезаться или чуть измениться
        if name.lower()[:20] in n.lower() or n.lower()[:20] in name.lower():
            return i
    log.info("Микрофон «%s» не найден, беру микрофон по умолчанию", name)
    return None


def _refresh_devices(sd):
    """Перечитать список звуковых устройств (PortAudio запоминает его при старте)."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        log.exception("Не удалось обновить список устройств")


LEVEL_SCALE = 6.0  # громкость для индикатора: обычная речь у микрофона даёт примерно половину шкалы
GATE_MAX = 0.35    # порог при самой низкой чувствительности — тише этого звук не слышен


class Listener(threading.Thread):
    """Постоянно слушает микрофон. on_final(text, audio_bytes) — законченная фраза,
    on_partial(text) — промежуточный результат (для быстрой реакции на имя).
    Микрофон можно сменить на ходу через set_device()."""

    def __init__(self, model_path, on_final, on_partial, on_error, device_name=None, gain=1.0, sensitivity=100):
        super().__init__(daemon=True)
        self.model_path = model_path
        self.on_final = on_final
        self.on_partial = on_partial
        self.on_error = on_error
        self.device_name = device_name  # None — микрофон Windows по умолчанию
        self.active_name = None         # какой микрофон реально открыт
        self.muted = threading.Event()
        self.reset_req = threading.Event()  # сбросить распознавание (отбросить эхо голоса Капри)
        self.switch = threading.Event()     # переоткрыть поток с другим микрофоном
        self.gain = gain                # громкость ввода для Капри: 1 — как есть, 2 — вдвое громче
        self.sensitivity = sensitivity  # 100 — слышит всё, меньше — тихие звуки (шум, фон) отбрасываются
        self.level = 0.0                # текущая громкость с микрофона 0..1 — для индикатора в настройках

    def set_device(self, name):
        self.device_name = name or None
        self.switch.set()

    def gate_level(self):
        """Порог громкости 0..1, ниже которого звук считается тишиной."""
        return (100 - self.sensitivity) / 100 * GATE_MAX

    def process(self, data):
        """Усиление и шумовой порог для блока звука int16. Заодно обновляет self.level."""
        import numpy as np
        x = np.frombuffer(data, np.int16).astype(np.float32)
        if self.gain != 1.0:
            x = np.clip(x * self.gain, -32768, 32767)
        self.level = min(1.0, float(np.sqrt(np.mean(x * x))) / 32768 * LEVEL_SCALE) if len(x) else 0.0
        if self.level < self.gate_level():
            x[:] = 0
        return x.astype(np.int16).tobytes()

    def run(self):
        try:
            import sounddevice as sd
            import vosk
            vosk.SetLogLevel(-1)
            model = vosk.Model(ascii_model_path(self.model_path))
            rec = vosk.KaldiRecognizer(model, RATE)
        except Exception as e:
            log.exception("Модель распознавания не загрузилась")
            self.on_error(str(e), "model")
            return
        worked = False  # микрофон уже хоть раз открылся
        while True:
            self.switch.clear()
            q = queue.Queue(maxsize=300)

            def callback(indata, frames, t, status, q=q):
                try:
                    q.put_nowait(bytes(indata))
                except queue.Full:
                    pass

            device = resolve_device(self.device_name)
            try:
                stream = sd.RawInputStream(samplerate=RATE, blocksize=4000, dtype="int16", channels=1,
                                           callback=callback, device=device)
            except Exception as e:
                if device is not None:  # выбранный микрофон не открылся — пробуем микрофон по умолчанию
                    log.info("Микрофон %s не открылся (%s), беру по умолчанию", self.device_name, e)
                    self.device_name = None
                    continue
                if worked:  # микрофон уже работал и пропал (сон, гарнитуру выдернули) — ждём, пока вернётся
                    log.info("Микрофон пока не открывается (%s), пробую снова", e)
                    time.sleep(3)
                    _refresh_devices(sd)
                    continue
                log.exception("Ошибка микрофона")
                self.on_error(str(e))
                return
            try:
                self.active_name = sd.query_devices(device if device is not None else sd.default.device[0])["name"]
            except Exception:
                self.active_name = default_input_name()
            rec.Reset()
            worked = True
            try:
                if self._listen(stream, q, rec) == "stale":
                    time.sleep(1)
                    _refresh_devices(sd)  # список устройств мог поменяться — иначе откроем «мёртвый» микрофон
            except Exception as e:
                log.exception("Ошибка микрофона/распознавания")
                self.on_error(str(e))
                return

    def _listen(self, stream, q, rec):
        with stream:
            log.info("Микрофон открыт: %s", self.active_name)
            buf = bytearray()
            was_muted = False
            last_audio = time.time()
            while not self.switch.is_set():
                try:
                    data = self.process(q.get(timeout=0.5))
                    last_audio = time.time()
                except queue.Empty:
                    # звук приходит каждые 0,25 с даже в тишине; если его нет — микрофон отвалился
                    # (сон компьютера, гарнитуру переподключили) — переоткрываем, иначе Капри «оглохнет» молча
                    if time.time() - last_audio > 3:
                        log.info("Микрофон перестал присылать звук — переоткрываю")
                        return "stale"
                    continue
                if self.muted.is_set():
                    was_muted = True
                    continue
                if was_muted or self.reset_req.is_set():
                    was_muted = False
                    self.reset_req.clear()
                    rec.Reset()
                    buf.clear()
                buf += data
                if rec.AcceptWaveform(data):
                    text = json.loads(rec.Result()).get("text", "").strip()
                    audio = bytes(buf)
                    buf.clear()
                    if text:
                        self.on_final(text, audio)
                else:
                    part = json.loads(rec.PartialResult()).get("partial", "")
                    if part:
                        self.on_partial(part)
                    if len(buf) > RATE * 2 * 25:
                        del buf[:RATE * 2 * 5]


def google_stt(audio, language="ru-RU"):
    """Повторно распознаёт фразу через Google — точнее Vosk на вопросах. None при ошибке."""
    try:
        import speech_recognition as sr
        r = sr.Recognizer()
        r.operation_timeout = 6
        return r.recognize_google(sr.AudioData(audio, RATE, 2), language=language)
    except Exception as e:
        log.info("Google STT недоступен: %s", e)
        return None


# ---------------- синтез речи ----------------

def _mci(cmd):
    buf = ctypes.create_unicode_buffer(256)
    err = ctypes.windll.winmm.mciSendStringW(cmd, buf, 255, 0)
    return err, buf.value


def play_mp3(path, stop_event, volume=100):
    """Играет mp3/wav, пока не закончится или не взведён stop_event. True — если прервали.
    volume 0..100 — громкость Капри (не трогает общую громкость Windows)."""
    _mci("close capri_tts")
    # wav (голос с эффектами) — как раньше, через waveaudio: громкость уже вшита в файл при обработке.
    # mp3 (чистый голос, если обработка не удалась) — через mpegvideo, он умеет менять громкость.
    wav = path.lower().endswith(".wav")
    if _mci(f'open "{path}" type {"waveaudio" if wav else "mpegvideo"} alias capri_tts')[0] != 0:
        raise RuntimeError("MCI не открыл файл")
    if not wav:
        _mci(f"setaudio capri_tts volume to {int(max(0, min(100, volume)) * 10)}")
    _mci("play capri_tts")
    interrupted = False
    try:
        while True:
            if stop_event.wait(0.05):
                interrupted = True
                _mci("stop capri_tts")
                break
            err, mode = _mci("status capri_tts mode")
            if err or mode != "playing":
                break
    finally:
        _mci("close capri_tts")
    return interrupted


def clean_for_speech(text):
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*#_`>|\[\]]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


FX_SR = 24000


def decode(path, sr=FX_SR):
    """Любой аудиофайл → моно float32."""
    import subprocess
    import numpy as np
    raw = subprocess.run([_ffmpeg(), "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"],
                         capture_output=True, check=True, creationflags=0x08000000).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


def trim_silence(x, sr=FX_SR, thr=0.012, pad=0.04):
    import numpy as np
    loud = np.nonzero(np.abs(x) > thr)[0]
    if not len(loud):
        return x[:0]
    p = int(sr * pad)
    return x[max(0, loud[0] - p):loud[-1] + p]


def write_wav(dst, x, sr=FX_SR, volume=100):
    """Записать wav, выровняв громкость; volume 0..100 — громкость Капри."""
    import numpy as np
    x = x / max(1e-6, float(np.max(np.abs(x)))) * 0.92 * max(0, min(100, volume)) / 100
    with wave.open(dst, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def capri_fx(src, dst, strength=1.0, seed=None):
    """Обработать файл src (mp3/wav) и сохранить в dst (wav)."""
    write_wav(dst, fx_process(decode(src), strength, seed))


def fx_process(x, strength=1.0, seed=None):
    """Цифровой голос Капри поверх синтезатора: хор из нескольких чуть расстроенных голосов
    («рождена очень многими людьми»), редкие глитч-заикания
    и лёгкий цифровой хруст."""
    import numpy as np
    sr = FX_SR
    if not len(x):
        raise RuntimeError("пустой звук")
    rnd = np.random.default_rng(seed)
    n = len(x)
    t = np.arange(n, dtype=np.float32)

    def shifted(factor, delay_ms, wobble=0.0):
        """Копия с другой высотой (простое передискретизирование) и задержкой, длина как у оригинала."""
        pos = t * factor + wobble * sr * 0.002 * np.sin(2 * np.pi * 0.7 * t / sr)
        pos -= delay_ms * sr / 1000
        return np.interp(pos, t, x, left=0.0, right=0.0)

    out = x.copy()
    s = strength
    for factor, delay, gain in ((1.012, 11, 0.32), (0.989, 19, 0.30), (1.024, 29, 0.18)):  # хор
        out += shifted(factor, delay, wobble=1.0) * gain * s

    # глитч-заикания: иногда короткий кусок повторяется 2–3 раза (только на сильном эффекте)
    slice_len = int(sr * 0.065)
    for _ in range(int(rnd.integers(0, 3) * s) if n > sr * 1.5 and s >= 0.7 else 0):
        at = int(rnd.integers(sr // 2, n - slice_len * 4))
        piece = out[at:at + slice_len].copy()
        reps = int(rnd.integers(2, 4))
        stutter = np.concatenate([piece * (0.9 ** i) for i in range(reps)])
        out = np.concatenate([out[:at], stutter, out[at:]])

    crushed = np.round(out * 96) / 96  # грубое квантование — цифровой хруст
    c = 0.22 * s * s  # на слабом эффекте хруста почти нет
    return out * (1 - c) + crushed * c


def _pct(value):
    m = re.match(r"([+-]?\d+)\s*%", value or "")
    return int(m.group(1)) if m else 0


def lively_semitones(sentences, base, rnd, lively=True):
    """Сдвиг высоты каждой фразы в полутонах: общий подъём + живая интонация
    (восклицания и вопросы выше, многоточия ниже)."""
    out = []
    for s in sentences:
        k = base
        if lively:
            k += rnd.uniform(-2.5, 3.0)
            end = s.rstrip()
            if end.endswith("!"):
                k += 4.0
            elif end.endswith("?"):
                k += 3.0
            elif end.endswith("…") or end.endswith("..."):
                k -= 2.5
        out.append(k)
    return out


def pitch_shift(x, semitones):
    """Повышает тон передискретизацией (заодно ускоряя; скорость заранее занижена в синтезаторе)."""
    import numpy as np
    factor = 2 ** (semitones / 12)
    pos = np.arange(0, len(x) - 1, factor)
    return np.interp(pos, np.arange(len(x)), x).astype(np.float32)


SILERO_PATH = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "Capri", "tts", "v4_ru.pt")
SILERO_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
_silero = None
_silero_lock = threading.Lock()


def silero_model():
    """Офлайн-синтезатор Silero (молодые живые голоса, работает на процессоре). Грузится один раз."""
    global _silero
    with _silero_lock:
        if _silero is None:
            import torch
            if not os.path.exists(SILERO_PATH):
                os.makedirs(os.path.dirname(SILERO_PATH), exist_ok=True)
                torch.hub.download_url_to_file(SILERO_URL, SILERO_PATH)
            torch.set_num_threads(4)
            model = torch.package.PackageImporter(SILERO_PATH).load_pickle("tts_models", "model")
            model.to(torch.device("cpu"))
            _silero = model
        return _silero


def silero_prosody(sentence, base, rnd, lively=True):
    """Высота и скорость для фразы в разметке Silero (x-low … x-high)."""
    levels = ["x-low", "low", "medium", "high", "x-high"]
    i = levels.index(base) if base in levels else 3
    rate = "medium"
    if lively:
        end = sentence.rstrip()
        if end.endswith("!"):
            i, rate = i + 1, "fast"
        elif end.endswith("?"):
            i += 1
        elif end.endswith("…") or end.endswith("..."):
            i, rate = i - 1, "slow"
        elif rnd.random() < 0.35:
            i += rnd.choice((-1, 1))
    return levels[max(0, min(4, i))], rate


TTS_CACHE = os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "Capri", "tts_cache")
CACHE_MAX_TEXT = 160    # фразы длиннее не кэшируем
CACHE_MAX_FILES = 600   # сколько готовых фраз хранить (~30 МБ)


def _trim_cache():
    try:
        files = sorted((os.path.join(TTS_CACHE, f) for f in os.listdir(TTS_CACHE)), key=os.path.getmtime)
        for f in files[:-CACHE_MAX_FILES]:
            os.remove(f)
    except OSError:
        pass


def clear_tts_cache(max_age_days=None, max_log_lines=5000):
    """Удалить старые файлы TTS‑кеша (и при необходимости обрезать capri.log). Возвращает число удалённых файлов."""
    removed = 0
    try:
        os.makedirs(TTS_CACHE, exist_ok=True)
        cutoff = time.time() - int(max_age_days) * 86400 if max_age_days is not None else None
        for name in os.listdir(TTS_CACHE):
            path = os.path.join(TTS_CACHE, name)
            if cutoff is not None:
                try:
                    if os.path.getmtime(path) > cutoff:
                        continue
                except OSError:
                    continue
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    except Exception:
        pass
    # ротация capri.log — BASE доступен из main? Держим лог в модуле capri, но voice.py не знает BASE.
    return removed


class Speaker:
    def __init__(self, cfg):
        self.voice = cfg.get("voice", "ru-RU-SvetlanaNeural")
        self.rate = cfg.get("voice_rate", "+5%")
        self.pitch = cfg.get("voice_pitch", "+8Hz")
        self.fx = float(cfg.get("voice_fx", 1.0))  # 0 — чистый голос, 1 — цифровой голос Капри
        self.lively = bool(cfg.get("voice_lively", True))  # у каждой фразы своя высота
        self.shift = float(cfg.get("voice_shift", 2.0))      # общий подъём тона в полутонах
        self.engine = cfg.get("tts_engine", "edge")          # edge — Microsoft онлайн, silero — офлайн
        self.silero_voice = cfg.get("silero_voice", "xenia")
        self.silero_pitch = cfg.get("silero_pitch", "high")
        self.path = os.path.join(tempfile.gettempdir(), f"capri_tts_{os.getpid()}.mp3")
        self.fx_path = os.path.join(tempfile.gettempdir(), f"capri_tts_{os.getpid()}.wav")
        self.stop_event = threading.Event()
        self.volume = int(cfg.get("voice_volume", 100))  # громкость Капри 0..100, общая громкость Windows не меняется
        self.edge_error = None  # почему не сработал голос Microsoft в последний раз (None — сработал)
        if self.engine == "silero":  # прогреваем модель заранее, чтобы первая фраза не тормозила
            threading.Thread(target=self._warmup, daemon=True).start()

    def _warmup(self):
        try:
            silero_model()
        except Exception:
            log.exception("Silero не загрузился")

    def stop(self):
        """Перебить: можно вызывать из любого потока."""
        self.stop_event.set()

    def say(self, text):
        """Блокирует поток до конца фразы. Возвращает True, если речь перебили."""
        self.stop_event.clear()
        text = clean_for_speech(text)
        if not text:
            return False
        try:
            interrupted = self._edge(text)
            self.edge_error = None
            return interrupted
        except Exception as e:
            self.edge_error = str(e)
            log.info("edge-tts недоступен (%s), использую голос Windows", e)
        try:
            return self._sapi(text)
        except Exception:
            log.exception("SAPI тоже не сработал")
            return False

    def render_silero(self, text, dst):
        import html
        import numpy as np
        model = silero_model()
        sentences = [s for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()] or [text]
        rnd = random.Random()
        gap = np.zeros(int(FX_SR * 0.12), np.float32)
        chunks = []
        for s in sentences:
            pitch, rate = silero_prosody(s, self.silero_pitch, rnd, self.lively)
            ssml = f'<speak><prosody pitch="{pitch}" rate="{rate}">{html.escape(s)}</prosody></speak>'
            audio = model.apply_tts(ssml_text=ssml, speaker=self.silero_voice, sample_rate=FX_SR,
                                    put_accent=True, put_yo=True)
            chunks += [trim_silence(audio.numpy().astype(np.float32)), gap]
        x = np.concatenate(chunks)
        if self.fx > 0:
            x = fx_process(x, self.fx)
        write_wav(dst, x, volume=self.volume)
        return dst

    def render(self, text, dst):
        """Синтез в wav-файл dst (с живой интонацией и эффектами) или в mp3, если обработка не удалась.
        Возвращает путь к готовому файлу."""
        if self.engine == "silero":
            try:
                return self.render_silero(text, dst)
            except Exception:
                log.exception("Silero не сработал, говорю голосом Microsoft")
        import edge_tts
        # уникальные временные файлы на каждый синтез — не конфликтуем с playback/другими потоками
        base = os.path.join(tempfile.gettempdir(), f"capri_tts_{os.getpid()}_{int(time.time() * 1000)}")
        self.path = base + ".mp3"
        # по предложениям: они синтезируются параллельно, поэтому длинный ответ ждём как одно предложение
        sentences = [s for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()] or [text]
        semis = lively_semitones(sentences, self.shift, random.Random(), self.lively)
        # тон поднимаем сами (Microsoft выше ~260 Гц не поднимает), а ускорение от этого
        # заранее гасим, замедляя синтез на столько же
        rates = [f"{round(_pct(self.rate) - (2 ** (k / 12) - 1) * 100):+d}%" for k in semis]
        parts = [f"{base}_{i}.mp3" for i in range(len(sentences))]

        async def go():  # фразы синтезируются параллельно — задержка как у одной
            await asyncio.gather(*(edge_tts.Communicate(s, self.voice, rate=r, pitch=self.pitch).save(f)
                                   for s, r, f in zip(sentences, rates, parts)))
        asyncio.run(go())
        try:
            import numpy as np
            gap = np.zeros(int(FX_SR * 0.13), np.float32)
            chunks = []
            for f, k in zip(parts, semis):
                chunks += [pitch_shift(trim_silence(decode(f)), k), gap]
            x = np.concatenate(chunks)
            if self.fx > 0:
                x = fx_process(x, self.fx)
            write_wav(base + ".wav", x, volume=self.volume)
            return base + ".wav"
        except Exception:
            log.exception("Обработка голоса не удалась, говорю чистым голосом")
            if len(parts) == 1:
                return parts[0]
            asyncio.run(edge_tts.Communicate(text, self.voice, rate=self.rate, pitch=self.pitch).save(self.path))
            return self.path

    # ---------- кэш: повторяющиеся фразы («Листаю», «Открываю ютуб») звучат сразу, без сервера ----------
    def _cache_path(self, text):
        import hashlib
        key = "|".join(map(str, (text, self.engine, self.voice, self.rate, self.pitch, self.shift, self.lively,
                                 self.fx, self.volume, self.silero_voice)))
        return os.path.join(TTS_CACHE, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".wav")

    def cached_render(self, text):
        """Путь к готовому звуку фразы: из кэша или свежий (и тогда кладём его в кэш)."""
        path = self._cache_path(text)
        if os.path.isfile(path):
            os.utime(path)  # свежие держим, старые потом удаляем
            return path
        out = self.render(text, self.fx_path)
        if out.endswith(".wav") and len(text) <= CACHE_MAX_TEXT:  # длинные ответы нейросети не повторяются
            try:
                os.makedirs(TTS_CACHE, exist_ok=True)
                shutil.copyfile(out, path)
                _trim_cache()
            except OSError:
                pass
        return out

    def prewarm(self, phrases):
        """Заранее озвучить постоянные фразы в фоне, по одной — чтобы потом они звучали сразу."""
        for text in phrases:
            text = clean_for_speech(text)
            if not text or os.path.isfile(self._cache_path(text)):
                continue
            try:
                self.cached_render(text)
            except Exception as e:
                log.info("Предзагрузка фраз остановлена: %s", e)
                return
            time.sleep(0.5)
        log.info("Постоянные фразы озвучены заранее")

    def _edge(self, text):
        path = self.cached_render(text)
        if self.stop_event.is_set():
            return True
        return play_mp3(path, self.stop_event, self.volume)

    def _sapi(self, text):
        import comtypes.client
        sp = comtypes.client.CreateObject("SAPI.SpVoice")
        voices = sp.GetVoices()
        for i in range(voices.Count):
            desc = voices.Item(i).GetDescription()
            if "Russian" in desc or "Irina" in desc or "Pavel" in desc:
                sp.Voice = voices.Item(i)
                break
        sp.Volume = max(0, min(100, self.volume))
        sp.Speak(text, 1)  # SVSFlagsAsync
        while not sp.WaitUntilDone(50):
            if self.stop_event.is_set():
                sp.Speak("", 2)  # SVSFPurgeBeforeSpeak — обрывает текущую речь
                return True
        return False


def make_glitch_sound(path, volume=100):
    """Короткий цифровой «треск» при появлении. volume 0..100 — громкость Капри."""
    rnd = random.Random(42)
    frames = bytearray()
    sr = 22050
    n = int(sr * 0.22)
    freq = 900
    for i in range(n):
        if i % 600 == 0:
            freq = rnd.choice([300, 700, 1200, 1800, 2400])
        env = (1 - i / n) ** 1.5
        sq = 1 if math.sin(2 * math.pi * freq * i / sr) > 0 else -1
        noise = rnd.uniform(-1, 1) if (i // 400) % 3 == 0 else 0
        v = int(5500 * volume / 100 * env * (0.6 * sq + 0.5 * noise))
        frames += struct.pack("<h", max(-32767, min(32767, v)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))


def play_sound_async(path):
    try:
        import winsound
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    except Exception:
        pass


def edge_voices(locale_prefix=""):
    """Список голосов Microsoft Edge TTS (ShortName). Если сеть/модуль недоступны — пустой список."""
    try:
        import asyncio
        import edge_tts
        result = asyncio.run(asyncio.wait_for(edge_tts.list_voices(), timeout=12))
    except Exception:
        return []
    out = []
    low = (locale_prefix or "").lower()
    for v in result or []:
        name = v.get("ShortName")
        if name and (not low or name.lower().startswith(low)):
            out.append(name)
    return sorted(set(out))
