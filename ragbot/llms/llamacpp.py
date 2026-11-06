import json
import threading
from typing import Iterator

from llama_cpp import Llama

from ragbot import config
from ragbot.llms.base import ChatModel, Event


class LlamaCppModel(ChatModel):
    """Shared llama.cpp plumbing. Subclasses set `filename` and override the hooks."""

    filename: str = ""
    n_ctx: int = 8192

    def __init__(self):
        self.path = config.MODELS_DIR / self.filename
        self._llm: Llama | None = None
        self._lock = threading.Lock()      # llama.cpp = one generation at a time

    # ---------- lifecycle ----------
    def load(self):
        if self._llm is None:
            if not self.path.exists():
                raise FileNotFoundError(f"{self.name}: model file not found at {self.path}")
            self._llm = Llama(
                model_path=str(self.path),
                n_ctx=self.n_ctx,
                n_gpu_layers=-1,
                n_batch=512,
                verbose=False,
            )

    def unload(self):
        if self._llm is not None:
            self._llm.close()
            self._llm = None

    # ---------- hooks for subclasses ----------
    def prepare(self, messages: list[dict]) -> list[dict]:
        """Adjust messages to what this model handles well."""
        return messages

    def parse(self, deltas: Iterator[str]) -> Iterator[Event]:
        """Turn raw text pieces into events. Default: everything is answer."""
        for d in deltas:
            if d:
                yield Event("answer", d)
        yield Event("done")

    # ---------- the interface ----------
    def stream(self, messages):
        self.load()
        with self._lock:
            produced = {"n": 0}

            def counted():
                for d in self._deltas(self.prepare(messages)):
                    if d:
                        produced["n"] += 1      # llama.cpp streams ~1 token per chunk
                    yield d

            yield from self.parse(counted())
            completion = produced["n"]
            # n_tokens = everything in the context after generation (prompt + answer)
            prompt = max(self._llm.n_tokens - completion, 0)
            yield Event("usage", data={"prompt_tokens": prompt, "completion_tokens": completion})

    def ask_json(self, messages, schema, max_tokens=512):
        """llama.cpp turns the JSON schema into a grammar: the model can only produce valid JSON."""
        self.load()
        with self._lock:
            out = self._llm.create_chat_completion(
                messages=self.prepare(messages),
                temperature=0.1,
                max_tokens=max_tokens,
                response_format={"type": "json_object", "schema": schema},
                **self.generation_kwargs(),
            )
        text = out["choices"][0]["message"]["content"] or ""
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"{self.name}: keine gültige JSON-Antwort: {text[:200]}") from e

    def _deltas(self, messages) -> Iterator[str]:
        for chunk in self._llm.create_chat_completion(
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
            **self.generation_kwargs(),
        ):
            yield chunk["choices"][0]["delta"].get("content") or ""

    def generation_kwargs(self) -> dict:
        """Extra llama.cpp sampling options (e.g. logit_bias). Default: none."""
        return {}