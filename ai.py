"""Облачные ИИ-агенты Капри.

Поддерживаются Anthropic (Claude) и любой OpenAI-совместимый API: OpenAI, DeepSeek, Groq,
Mistral, xAI, OpenRouter, Google Gemini, а также пользовательский endpoint."""
import datetime
import json
import logging
import os
import urllib.error
import urllib.request

from persona import CHARACTER, SPEECH_RULES, user_context

log = logging.getLogger("capri")

SYSTEM = f"""{CHARACTER}

{SPEECH_RULES}
Отвечай кратко и по-русски, как Капри: живо, с характером, без канцелярита.
Если не знаешь свежих данных, честно скажи об этом; не выдумывай факты.
Не пересказывай рассуждения и внутренние размышления вслух."""

PROVIDERS = {
    "anthropic": ("Anthropic (Claude)", "https://api.anthropic.com", "claude-opus-5-5"),
    "openai": ("OpenAI (GPT)", "https://api.openai.com/v1", "gpt-4o-mini"),
    "deepseek": ("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"),
    "groq": ("Groq", "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "mistral": ("Mistral", "https://api.mistral.ai/v1", "mistral-small-latest"),
    "xai": ("xAI (Grok)", "https://api.x.ai/v1", "grok-2-latest"),
    "openrouter": ("OpenRouter", "https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
    "gemini": ("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash"),
    "ollama": ("Ollama (локальный)", "http://localhost:11434/v1", "llama3.2"),
    "ollama_cloud": ("Ollama Cloud (платный API)", "https://ollama.com/v1", "gemma4:31b"),
    "custom": ("Свой URL (OpenAI-совместимый)", "", ""),
}

ENV_KEYS = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "xai": "XAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "ollama_cloud": "OLLAMA_API_KEY",
}


def provider_label(provider):
    return PROVIDERS.get(provider, PROVIDERS["custom"])[0]


def fetch_models(provider, base_url="", api_key=""):
    """Пытается получить список моделей у провайдера. Возвращает список имён (до 80)."""
    base = (base_url or PROVIDERS.get(provider, ("", "", ""))[1] or "").strip().rstrip("/")
    names = []
    try:
        if provider == "ollama":
            root = base
            if root.endswith("/v1"):
                root = root[:-3]
            url = root.rstrip("/") + "/api/tags"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            for m in data.get("models", []):
                name = m.get("name") or m.get("model")
                if name and name not in names:
                    names.append(name)
        else:
            url = base.rstrip("/") + "/models"
            headers = {"Accept": "application/json"}
            if api_key:
                if provider == "anthropic":
                    headers["x-api-key"] = api_key
                    headers["anthropic-version"] = "2023-06-01"
                else:
                    headers["Authorization"] = "Bearer " + api_key
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            for m in (data.get("data") or data.get("models") or []):
                name = m.get("id") or m.get("name") or m.get("model")
                if name and name not in names:
                    names.append(name)
    except Exception:
        return []
    return names[:80]


class CloudAI:
    def __init__(self, cfg):
        self.provider = cfg.get("ai_provider", "anthropic")
        if self.provider not in PROVIDERS:
            self.provider = "anthropic"
        self.history = []
        self.client = None
        self.base_url = (cfg.get("ai_base_url") or "").strip().rstrip("/")
        if not self.base_url:
            self.base_url = PROVIDERS[self.provider][1].rstrip("/")
        stored_model = (cfg.get("model") or "").strip()
        default_model = PROVIDERS[self.provider][2]
        # старые конфиги хранят anthropic-модель; для других агентов она не подходит
        if not stored_model or (self.provider != "anthropic" and stored_model == "claude-opus-5-5"):
            stored_model = default_model
        self.model = stored_model
        if self.provider == "ollama":
            models = self._ollama_models()
            if models and (not self.model or self.model not in models):
                self.model = models[0]

        if self.provider == "anthropic":
            key = (cfg.get("anthropic_api_key") or cfg.get("ai_api_key")
                   or os.environ.get("ANTHROPIC_API_KEY"))
            cfg.setdefault("anthropic_api_key", key or "")
            cfg.setdefault("ai_api_key", key or "")
            self.api_key = key or ""
            if self.api_key:
                try:
                    import anthropic
                    self.client = anthropic.Anthropic(api_key=self.api_key, timeout=60, max_retries=2)
                except Exception:
                    log.exception("Не удалось создать клиент Anthropic")
                    self.client = None
        else:
            key = cfg.get("ai_api_key") or os.environ.get(ENV_KEYS.get(self.provider, ""))
            self.api_key = key or ""
            # для локальных OpenAI-совместимых серверов ключ может не требоваться
            self._available_by_url = (not self.base_url.startswith("https://")
                                      and (not self.api_key))

    @property
    def available(self):
        if self.provider == "anthropic":
            return self.client is not None
        if self.provider == "ollama":
            return bool(self.base_url)
        if self._available_by_url:
            return True
        return bool(self.api_key)

    def _ollama_models(self):
        try:
            root = self.base_url.rstrip("/")
            if root.endswith("/v1"):
                root = root[:-3]
            url = root.rstrip("/") + "/api/tags"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception:
            return []
        names = []
        for m in data.get("models", []):
            name = m.get("name") or m.get("model")
            if name and name not in names:
                names.append(name)
        return names

    def _call_anthropic(self, system, messages, tools):
        import anthropic
        extra = {"tools": tools} if tools else {}
        for _ in range(4):
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=2000,
                system=system,
                messages=messages,
                **extra,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                extra_body={"fallbacks": "default"},
            )
            if getattr(resp, "stop_reason", None) == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            if getattr(resp, "stop_reason", None) == "refusal":
                return "Мне не разрешили это обсуждать."
            text = " ".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
            return text or "Ладно, промолчу. Спроси иначе."
        return "Что-то пошло не так, спроси ещё раз."

    def _call_openai_compat(self, system, messages):
        url = self.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + messages,
            "temperature": 0.7,
            "max_tokens": 1500,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"HTTP {e.code}: {body}")
        except Exception as e:
            raise RuntimeError(str(e))
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError("Некорректный ответ сервера")
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        text = (content or "").strip()
        if not text:
            text = choice.get("text") or data.get("response") or ""
        return (text or "Пустой ответ от модели.").strip()

    def _call(self, system, messages, tools=None):
        if self.provider == "anthropic":
            return self._call_anthropic(system, messages, tools or [])
        return self._call_openai_compat(system, messages)

    def ask(self, question):
        if not self.available:
            if self.provider == "anthropic":
                return ("Нет ключа облачного ИИ: добавь Anthropic API key в настройках "
                        "или в config.json (anthropic_api_key).")
            return (f"Нет ключа для {provider_label(self.provider)}: добавь API key в настройках "
                    "или свой URL, если сервер локальный.")
        now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
        self.history.append({"role": "user", "content": f"[{now}] {question}"})
        self.history = self.history[-12:]
        while self.history and self.history[0]["role"] != "user":
            self.history.pop(0)
        tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 3}] if self.provider == "anthropic" else []
        try:
            answer = self._call(SYSTEM + "\n" + user_context(), self.history, tools)
        except Exception as e:
            self.history.pop()
            msg = str(e)
            log.error("Cloud AI error: %s", msg)
            lower = msg.lower()
            if "401" in msg or "auth" in lower or "invalid" in lower:
                return "Неверный API key. Проверь его в настройках."
            if "429" in msg or "rate" in lower:
                return "Слишком много запросов к облаку, подожди немного."
            if "connection" in lower or "timed out" in lower or "timeout" in lower:
                return "Нет связи с облачным ИИ. Проверь интернет и адрес сервера."
            return f"Облачный ИИ ({provider_label(self.provider)}) ответил ошибкой: {msg[:180]}"
        self.history.append({"role": "assistant", "content": answer})
        return answer

    def summarize_report(self, report):
        if not self.available:
            return None
        try:
            return self._call(
                SYSTEM + "\n" + user_context(),
                [{"role": "user", "content":
                  "Вот отчёт диагностики ПК. Сделай краткое резюме в 2-4 предложениях: "
                  "память/CPU/GPU, топ процессов по памяти и возможные причины. "
                  "Без воды и без рекомендаций.\n\n" + report[:30000]}],
                [],
            )
        except Exception:
            log.exception("summarize_report")
            return None


# обратная совместимость со старым именем
AI = CloudAI
AI.__name__ = "AI"
