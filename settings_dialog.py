"""Единое окно настроек Капри: обычные настройки + редактор config.json."""
import json
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

from entries_dialog import STYLE


class SettingsDialog(QDialog):
    def __init__(self, capri):
        super().__init__(None, Qt.WindowType.WindowStaysOnTopHint)
        self.capri = capri
        self.cfg = capri.cfg
        self.setWindowTitle("Капри — настройки")
        self.setStyleSheet(STYLE + """
        QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit {
            background: #120818; color: #ffd2e1; border: 1px solid #5a2a50; padding: 3px;
        }
        QCheckBox { color: #ffd2e1; }
        """)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._basic_tab(), "Основное")
        self.tabs.addTab(self._voice_tab(), "Голос")
        self.tabs.addTab(self._mic_tab(), "Микрофон")
        self.tabs.addTab(self._user_tab(), "Пользователь")
        self.tabs.addTab(self._ai_tab(), "ИИ")
        self.tabs.addTab(self._lists_tab(), "Списки")
        self.tabs.addTab(self._raw_tab(), "config.json")

        apply_btn = QPushButton("Применить")
        close_btn = QPushButton("Закрыть")
        apply_btn.clicked.connect(self.apply)
        close_btn.clicked.connect(self.close)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(close_btn)

        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        lay.addLayout(btn_row)
        self.resize(620, 520)

    # ---------- helpers ----------
    def _title(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("color: #28ff96; font-weight: bold;")
        return lbl

    def _check(self, caption, value):
        cb = QCheckBox(caption)
        cb.setChecked(bool(value))
        return cb

    def _spin(self, lo, hi, value, suffix=""):
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(int(value))
        if suffix:
            s.setSuffix(suffix)
        return s

    def _dspin(self, lo, hi, value, decimals=2, suffix=""):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(decimals)
        s.setValue(float(value))
        if suffix:
            s.setSuffix(suffix)
        return s

    # ---------- tabs ----------
    def _basic_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        cfg = self.cfg
        self.wake_words = QLineEdit(", ".join(cfg.get("wake_words", [])))
        self.listen_seconds = self._spin(1, 60, cfg.get("listen_window_seconds", 8), " с")
        self.followup_seconds = self._spin(1, 60, cfg.get("followup_seconds", 6), " с")
        self.hide_after = self._spin(1, 300, cfg.get("hide_after_seconds", 12), " с")
        self.greet_on_start = self._check("Приветствовать при запуске", cfg.get("greet_on_start", True))
        self.search_when_unsure = self._check("Искать в браузере, если не знаю ответа", cfg.get("search_when_unsure", True))
        self.chatter = self._check("Появляться самой", cfg.get("chatter", True))
        self.chatter_min = self._spin(1, 120, cfg.get("chatter_min_minutes", 15), " мин")
        self.chatter_max = self._spin(1, 120, cfg.get("chatter_max_minutes", 40), " мин")
        self.roam_chance = self._dspin(0, 1, cfg.get("roam_chance", 0.5), 2)
        self.teleport_on_answer = self._check("Прыгать молнией при ответе", cfg.get("teleport_on_answer", True))
        self.barge_in = self._check("Можно перебивать голосом", cfg.get("barge_in", True))
        self.use_google_stt = self._check("Уточнять фразу через Google", cfg.get("use_google_stt", True))
        self.type_press_enter = self._check("Отправлять сообщение Enter после «напиши»", cfg.get("type_press_enter", True))

        form.addRow("Слова-обращения (через запятую):", self.wake_words)
        form.addRow("Время слушания после появления:", self.listen_seconds)
        form.addRow("Время ожидания следующей команды:", self.followup_seconds)
        form.addRow("Исчезать через:", self.hide_after)
        for c in (self.greet_on_start, self.search_when_unsure, self.chatter, self.teleport_on_answer,
                  self.barge_in, self.use_google_stt, self.type_press_enter):
            form.addRow(c)
        form.addRow("Свои появления — от/до минут:", self._pair(self.chatter_min, self.chatter_max))
        form.addRow("Вероятность прогулки (0..1):", self.roam_chance)
        hint = QLabel("Большинство базовых настроек применяются после перезапуска Капри.")
        hint.setWordWrap(True)
        form.addRow(hint)
        return w

    def _pair(self, a, b):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(a)
        row.addWidget(QLabel("—"))
        row.addWidget(b)
        return box

    def _voice_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        cfg = self.cfg
        self.voice = QLineEdit(cfg.get("voice", "ru-RU-SvetlanaNeural"))
        self.tts_engine = QComboBox()
        self.tts_engine.addItems(["edge", "silero"])
        idx = self.tts_engine.findText(cfg.get("tts_engine", "edge"))
        self.tts_engine.setCurrentIndex(max(0, idx))
        self.voice_rate = QLineEdit(cfg.get("voice_rate", "+10%"))
        self.voice_pitch = QLineEdit(cfg.get("voice_pitch", "+15Hz"))
        self.voice_shift = self._spin(-12, 12, cfg.get("voice_shift", 0), " полутонов")
        self.voice_lively = self._check("Живой голос", cfg.get("voice_lively", False))
        self.voice_fx = self._dspin(0, 5, cfg.get("voice_fx", 1.8), 2)
        self.voice_volume = self._spin(0, 100, cfg.get("voice_volume", 100), "%")
        self.browser = QLineEdit(cfg.get("browser", ""))

        form.addRow("Голос Microsoft (edge):", self.voice)
        form.addRow("Движок TTS:", self.tts_engine)
        form.addRow("Скорость речи:", self.voice_rate)
        form.addRow("Высота (pitch):", self.voice_pitch)
        form.addRow("Сдвиг высоты:", self.voice_shift)
        form.addRow(self.voice_lively)
        form.addRow("Сила эффектов (×):", self.voice_fx)
        form.addRow("Громкость голоса:", self.voice_volume)
        form.addRow("Браузер (хром/яндекс/… или путь):", self.browser)

        test = QPushButton("Проверить голос")
        test.clicked.connect(lambda: self.capri.jobs.put(("say", "Проверка настроек голоса. Так мне слышно?")))
        form.addRow(test)
        hint = QLabel("Изменения голоса/TTS применяются после перезапуска Капри. Для микрофона и частоты речи см. отдельные окна.")
        hint.setWordWrap(True)
        form.addRow(hint)
        return w

    def _mic_tab(self):
        w = QWidget()
        form = QFormLayout(w)
        cfg = self.cfg
        self.mic_device = QComboBox()
        try:
            from voice import input_devices, default_input_name
            options = [(None, f"По умолчанию ({default_input_name()})")]
            options += [(n, n) for n, _ in input_devices()]
        except Exception:
            options = [(cfg.get("mic_device"), cfg.get("mic_device") or "по умолчанию")]
        for value, label in options:
            self.mic_device.addItem(label, value)
        cur = cfg.get("mic_device")
        for i in range(self.mic_device.count()):
            if self.mic_device.itemData(i) == cur:
                self.mic_device.setCurrentIndex(i)
                break
        self.mic_gain = self._dspin(0.5, 4.0, cfg.get("mic_gain", 1.0), 2, "×")
        self.mic_sensitivity = self._spin(0, 100, cfg.get("mic_sensitivity", 100), "%")

        form.addRow("Микрофон:", self.mic_device)
        form.addRow("Громкость микрофона для Капри:", self.mic_gain)
        form.addRow("Чувствительность:", self.mic_sensitivity)
        open_sound = QPushButton("Открыть окно звука…")
        open_sound.clicked.connect(self._open_sound)
        form.addRow(open_sound)
        return w

    def _open_sound(self):
        from sound_dialog import SoundDialog
        dlg = SoundDialog(self.capri)
        dlg.exec()

    def _user_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        cfg = self.cfg
        # profile UI reused lightweight
        self.user_name_mode = QComboBox()
        self.user_name_mode.addItems(["custom", "windows", "pc", "none"])
        names = {"custom": "Своё имя", "windows": "Имя пользователя Windows",
                 "pc": "Имя компьютера", "none": "Не называть по имени"}
        for i in range(self.user_name_mode.count()):
            self.user_name_mode.setItemText(i, names[self.user_name_mode.itemText(i)])
        idx = self.user_name_mode.findData(cfg.get("user_name_mode", "none"))
        # findData not set, so search by raw mapping
        for i in range(self.user_name_mode.count()):
            if self.user_name_mode.itemText(i) == cfg.get("user_name_mode", "none") or names.get(cfg.get("user_name_mode", "none")) == self.user_name_mode.itemText(i):
                self.user_name_mode.setCurrentIndex(i)
                break
        # store raw modes separately
        self._user_modes = ["custom", "windows", "pc", "none"]
        # rebuild combo with raw data as userData
        self.user_name_mode.clear()
        for m in self._user_modes:
            self.user_name_mode.addItem(names[m], m)
        for i in range(self.user_name_mode.count()):
            if self.user_name_mode.itemData(i) == cfg.get("user_name_mode", "none"):
                self.user_name_mode.setCurrentIndex(i)

        self.user_name = QLineEdit(cfg.get("user_name", ""))
        self.user_gender = QComboBox()
        self.user_gender.addItem("Мужской", "male")
        self.user_gender.addItem("Женский", "female")
        for i in range(self.user_gender.count()):
            if self.user_gender.itemData(i) == cfg.get("user_gender", "male"):
                self.user_gender.setCurrentIndex(i)

        form = QFormLayout()
        form.addRow("Как называть:", self.user_name_mode)
        form.addRow("Своё имя:", self.user_name)
        form.addRow("Род:", self.user_gender)
        lay.addLayout(form)
        hint = QLabel("Изменения применяются сразу.")
        lay.addWidget(hint)
        return w

    def _ai_tab(self):
        from ai import PROVIDERS
        w = QWidget()
        form = QFormLayout(w)
        cfg = self.cfg
        self.enable_ai = self._check("Включить облачный ИИ", cfg.get("enable_ai", False))
        self.ai_provider = QComboBox()
        current_provider = cfg.get("ai_provider", "anthropic")
        for key, (label, _base, _model) in PROVIDERS.items():
            self.ai_provider.addItem(label, key)
        for i in range(self.ai_provider.count()):
            if self.ai_provider.itemData(i) == current_provider:
                self.ai_provider.setCurrentIndex(i)
        self.ai_api_key = QLineEdit(cfg.get("ai_api_key") or cfg.get("anthropic_api_key", ""))
        self.ai_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_base_url = QLineEdit(cfg.get("ai_base_url", "") or PROVIDERS.get(current_provider, ("", "", ""))[1])
        self.ai_model = QComboBox()
        self.ai_model.setEditable(True)
        _model = (cfg.get("model") or PROVIDERS.get(current_provider, ("", "", ""))[2] or "").strip()
        if _model:
            self.ai_model.addItem(_model)
        self.ai_model.setCurrentText(_model)
        self.ai_status = QLabel("Проверка ИИ не выполнялась.")
        self.ai_status.setWordWrap(True)
        self.local_ai = self._check("Включить локальный ИИ (Ollama)", cfg.get("local_ai", True))
        self.local_model = QLineEdit(cfg.get("local_model", "qwen3:8b"))

        self.ai_provider.currentIndexChanged.connect(self._on_ai_provider_changed)

        form.addRow(self.enable_ai)
        form.addRow("Провайдер:", self.ai_provider)
        form.addRow("API key:", self.ai_api_key)
        form.addRow("Адрес (base URL):", self.ai_base_url)
        form.addRow("Модель:", self.ai_model)
        form.addRow(self.local_ai)
        form.addRow("Модель Ollama:", self.local_model)
        row_models = QHBoxLayout()
        refresh = QPushButton("Обновить модели")
        refresh.clicked.connect(self._refresh_ai_models)
        test = QPushButton("Тест ИИ")
        test.clicked.connect(self._test_ai)
        row_models.addWidget(refresh)
        row_models.addWidget(test)
        form.addRow("Список моделей:", row_models)
        form.addRow("Статус ИИ:", self.ai_status)
        hint = QLabel("Anthropic поддерживает веб-поиск; остальные работают как обычный чат. Свой адрес можно указать при «Свой URL». "
                      "Переключение локального ИИ/облачного лучше подтверждать перезапуском.")
        hint.setWordWrap(True)
        form.addRow(hint)
        return w

    def _on_ai_provider_changed(self):
        from ai import PROVIDERS
        key = self.ai_provider.currentData()
        _label, base, model = PROVIDERS.get(key, ("", "", ""))
        old = self.ai_base_url.text().strip()
        # при смене провайдера подставляем его стандартный адрес,
        # но не затираем вручную введённый адрес, если он отличается от стандартного старого
        presets = {v[1] for v in PROVIDERS.values()}
        if not old or old in presets:
            self.ai_base_url.setText(base)
        if not self.ai_model.currentText().strip() or self.ai_model.currentText().strip() in presets or self.ai_model.currentText().strip() in ("claude-opus-5-5",):
            if key != "anthropic" or model:
                if self.ai_model.count() == 0 and model:
                    self.ai_model.addItem(model)
                if model:
                    self.ai_model.setCurrentText(model)
        try:
            self._refresh_ai_models()
        except Exception:
            pass

    def _refresh_ai_models(self):
        from ai import PROVIDERS, fetch_models
        provider = self.ai_provider.currentData()
        base = self.ai_base_url.text().strip()
        key = self.ai_api_key.text().strip()
        current = self.ai_model.currentText().strip()
        models = fetch_models(provider, base, key)
        self.ai_model.blockSignals(True)
        if models:
            self.ai_model.clear()
            for m in models:
                self.ai_model.addItem(m)
        else:
            if self.ai_model.count() == 0 and current:
                self.ai_model.addItem(current)
        if current:
            self.ai_model.setCurrentText(current)
        self.ai_model.blockSignals(False)
        if models:
            self.ai_status.setText(f"Найдено моделей: {len(models)}")
        else:
            self.ai_status.setText("Список недоступен. Введите имя модели вручную.")

    def _test_ai(self):
        from PySide6.QtWidgets import QApplication, QMessageBox
        from ai import CloudAI
        cfg = dict(self.capri.cfg)
        cfg["enable_ai"] = True
        cfg["ai_provider"] = self.ai_provider.currentData() or "anthropic"
        cfg["ai_api_key"] = self.ai_api_key.text().strip()
        cfg["ai_base_url"] = self.ai_base_url.text().strip()
        cfg["model"] = self.ai_model.currentText().strip()
        ai = CloudAI(cfg)
        if not ai.available:
            QMessageBox.warning(self, "Капри", "ИИ недоступен: заполните ключ/адрес (для локального Ollama проверьте, что он запущен).")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            answer = ai.ask("Ответь одним словом: ok")
        finally:
            QApplication.restoreOverrideCursor()
        QMessageBox.information(self, "Тест ИИ", f"Ответ: {answer}")

    def _lists_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        btn = QPushButton("Открыть «Мои сайты, программы и ответы»…")
        btn.clicked.connect(self._open_entries)
        lay.addWidget(btn)
        lay.addWidget(QLabel("Сайты, программы и свои ответы удобнее править в отдельном окне."))
        return w

    def _open_entries(self):
        from entries_dialog import EntriesDialog
        self.capri.reload_config_if_changed(speak=False)
        dlg = EntriesDialog(self.capri.actions)
        dlg.exec()
        self.capri.reload_config_if_changed(speak=False)

    def _raw_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.raw_text = QTextEdit()
        self.raw_text.setPlainText(json.dumps(self.cfg, ensure_ascii=False, indent=2))
        lay.addWidget(QLabel("Ручное редактирование config.json. Сохраняйте корректный JSON."))
        lay.addWidget(self.raw_text, 1)
        row = QHBoxLayout()
        fmt = QPushButton("Форматировать")
        save = QPushButton("Сохранить текст")
        open_file = QPushButton("Открыть файл")
        fmt.clicked.connect(self._format_raw)
        save.clicked.connect(self._save_raw)
        open_file.clicked.connect(lambda: __import__("os").startfile(__import__("os").path.join(__import__("os").path.dirname(__file__), "config.json")))
        row.addWidget(fmt)
        row.addWidget(save)
        row.addWidget(open_file)
        lay.addLayout(row)
        return w

    def _format_raw(self):
        try:
            data = json.loads(self.raw_text.toPlainText())
            self.raw_text.setPlainText(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Капри", f"JSON с ошибкой: {e}")

    def _save_raw(self):
        try:
            data = json.loads(self.raw_text.toPlainText())
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Капри", f"Не сохранено: JSON с ошибкой: {e}")
            return
        try:
            with open(__import__("os").path.join(__import__("os").path.dirname(__file__), "config.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Капри", f"Не удалось записать: {e}")
            return
        self.capri.reload_config_if_changed(speak=False)
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(self, "Капри", "config.json сохранён. Полное применение — после перезапуска Капри.")

    # ---------- apply ----------
    def collect(self):
        cfg = self.cfg
        updates = {}
        updates["wake_words"] = [x.strip() for x in self.wake_words.text().split(",") if x.strip()]
        updates["listen_window_seconds"] = int(self.listen_seconds.value())
        updates["followup_seconds"] = int(self.followup_seconds.value())
        updates["hide_after_seconds"] = int(self.hide_after.value())
        updates["greet_on_start"] = bool(self.greet_on_start.isChecked())
        updates["search_when_unsure"] = bool(self.search_when_unsure.isChecked())
        updates["chatter"] = bool(self.chatter.isChecked())
        updates["chatter_min_minutes"] = int(self.chatter_min.value())
        updates["chatter_max_minutes"] = int(self.chatter_max.value())
        updates["roam_chance"] = float(self.roam_chance.value())
        updates["teleport_on_answer"] = bool(self.teleport_on_answer.isChecked())
        updates["barge_in"] = bool(self.barge_in.isChecked())
        updates["use_google_stt"] = bool(self.use_google_stt.isChecked())
        updates["type_press_enter"] = bool(self.type_press_enter.isChecked())

        voice_keys = ["voice", "tts_engine", "voice_rate", "voice_pitch", "voice_shift", "voice_lively", "voice_fx",
                      "voice_volume", "browser"]
        updates["voice"] = self.voice.text().strip()
        updates["tts_engine"] = self.tts_engine.currentText()
        updates["voice_rate"] = self.voice_rate.text().strip()
        updates["voice_pitch"] = self.voice_pitch.text().strip()
        updates["voice_shift"] = int(self.voice_shift.value())
        updates["voice_lively"] = bool(self.voice_lively.isChecked())
        updates["voice_fx"] = float(self.voice_fx.value())
        updates["voice_volume"] = int(self.voice_volume.value())
        updates["browser"] = self.browser.text().strip()

        updates["mic_device"] = self.mic_device.currentData()
        updates["mic_gain"] = float(self.mic_gain.value())
        updates["mic_sensitivity"] = int(self.mic_sensitivity.value())

        updates["enable_ai"] = bool(self.enable_ai.isChecked())
        updates["ai_provider"] = self.ai_provider.currentData() or "anthropic"
        updates["ai_api_key"] = self.ai_api_key.text().strip()
        updates["ai_base_url"] = self.ai_base_url.text().strip()
        updates["model"] = self.ai_model.currentText().strip()
        updates["local_ai"] = bool(self.local_ai.isChecked())
        updates["local_model"] = self.local_model.text().strip()

        user_mode = self.user_name_mode.currentData() or "none"
        user_name = self.user_name.text().strip()
        user_gender = self.user_gender.currentData() or "male"

        return updates, user_mode, user_name, user_gender

    def apply(self):
        capri = self.capri
        updates, user_mode, user_name, user_gender = self.collect()
        try:
            from ai import PROVIDERS
            old_provider = self.capri.cfg.get("ai_provider")
            new_provider = updates.get("ai_provider")
            if new_provider and new_provider != old_provider:
                presets = {v[1] for v in PROVIDERS.values()}
                if (not updates.get("ai_base_url")) or updates["ai_base_url"] in presets:
                    updates["ai_base_url"] = PROVIDERS[new_provider][1]
                    self.ai_base_url.setText(updates["ai_base_url"])
                # модель тоже часто стоит «от прошлого» — подставляем стандартную
                old_models = {v[2] for v in PROVIDERS.values()}
                if (not updates.get("model")) or updates["model"] in old_models:
                    updates["model"] = PROVIDERS[new_provider][2]
                    self.ai_model.setCurrentText(updates["model"])
        except Exception:
            pass

        old_voice = {k: capri.cfg.get(k) for k in ("voice", "tts_engine", "voice_rate", "voice_pitch", "voice_shift", "voice_lively", "voice_fx", "browser")}
        old_ai = {k: capri.cfg.get(k) for k in ("enable_ai", "ai_provider", "ai_api_key", "ai_base_url", "model", "local_ai", "local_model")}

        live_keys = {
            "chatter", "search_when_unsure", "voice_volume", "voice_fx",
            "mic_gain", "mic_sensitivity", "mic_device",
        }
        try:
            for key, value in updates.items():
                if key in live_keys or key in ("user_name_mode", "user_name", "user_gender"):
                    continue
                if capri.cfg.get(key) != value:
                    capri.cfg[key] = value
                    from main import save_setting
                    save_setting(key, value)
            # wake words compile
            words = sorted(capri.cfg.get("wake_words", []), key=len, reverse=True)
            if words:
                capri.wake_re = re.compile(r"(^|\s)(" + "|".join(map(re.escape, words)) + r")(?=\s|$|[,.!?])", re.IGNORECASE)
            # live profile
            capri.set_profile(user_mode, user_name, user_gender)
            # toggles (save again, idempotent)
            capri.set_chatter(bool(updates["chatter"]))
            capri.set_search_when_unsure(bool(updates["search_when_unsure"]))
            # volume/fx/mic
            capri.set_voice_volume(int(updates["voice_volume"]))
            capri.set_voice_fx(float(updates["voice_fx"]))
            capri.set_mic_tuning(gain=float(updates["mic_gain"]), sensitivity=int(updates["mic_sensitivity"]))
            capri.set_mic(updates["mic_device"], announce=False)
            # voice/tts rebuild if needed
            voice_changed = any(capri.cfg.get(k) != old_voice.get(k) for k in old_voice)
            if voice_changed:
                from voice import Speaker, make_glitch_sound
                import tempfile, os
                capri.speaker = Speaker(capri.cfg)
                capri.speaker.fx = float(capri.cfg.get("voice_fx", 1.8))
                if capri.glitch_wav:
                    try:
                        make_glitch_sound(capri.glitch_wav, capri.speaker.volume)
                    except Exception:
                        pass
            # ai
            local_was = capri.local is not None
            ai_changed = any(capri.cfg.get(k) != old_ai.get(k) for k in old_ai)
            if ai_changed:
                capri.cfg["enable_ai"] = bool(updates["enable_ai"])
                capri.cfg["ai_provider"] = updates["ai_provider"]
                capri.cfg["ai_api_key"] = updates["ai_api_key"]
                capri.cfg["ai_base_url"] = updates["ai_base_url"]
                capri.cfg["model"] = updates["model"]
                # совместимость со старым кодом/конфигами
                if updates["ai_provider"] == "anthropic":
                    capri.cfg["anthropic_api_key"] = updates["ai_api_key"]
                capri.cfg["local_ai"] = bool(updates["local_ai"])
                capri.cfg["local_model"] = updates["local_model"]
                from ai import AI
                capri.ai = AI(capri.cfg)
                capri.ai.model = updates["model"]
                if updates["local_ai"]:
                    if capri.local is None:
                        from local_ai import LocalAI
                        capri.local = LocalAI(capri.cfg)
                        capri.local.on_model_change = capri._remember_model
                        if not local_was:
                            import threading
                            threading.Thread(target=capri.local.start_server, daemon=True).start()
                    else:
                        capri.local.model = updates["local_model"]
                else:
                    capri.local = None
            # sync raw tab with applied config (best effort)
            try:
                self.raw_text.setPlainText(json.dumps(capri.cfg, ensure_ascii=False, indent=2))
            except Exception:
                pass
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Капри", "Настройки применены. Некоторые из них вступят в силу после перезапуска Капри.")
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Капри", f"Не удалось применить настройки: {e}")