import json
import time

from jinja2.sandbox import ImmutableSandboxedEnvironment
from llama_cpp import LlamaGrammar

from ragbot.llms.llamacpp import LlamaCppModel


class Qwen36(LlamaCppModel):
    """Qwen3.6 35B-A3B (MoE). Quirk: thinks by default -> we render its chat template
    ourselves with enable_thinking=False and generate with create_completion."""

    name = "qwen3.6-35b-a3b"
    filename = "qwen3.6-35b-a3b/Qwen3.6-35B-A3B-UD-IQ4_XS.gguf"
    temperature = 0.7            # Qwen's recommendation for non-thinking mode
    max_tokens = 1024
    n_ctx = 16384

    def __init__(self):
        super().__init__()
        self._template = None

    def _prompt(self, messages: list[dict]) -> str:
        """Model's own chat template, thinking switched off."""
        if self._template is None:
            env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
            env.globals["raise_exception"] = lambda msg: (_ for _ in ()).throw(Exception(msg))
            env.globals["strftime_now"] = lambda fmt: time.strftime(fmt)
            self._template = env.from_string(self._llm.metadata["tokenizer.chat_template"])
        return self._template.render(messages=messages, add_generation_prompt=True,
                                     enable_thinking=False, bos_token="", eos_token="")

    def _deltas(self, messages):
        """Streaming answer (used by stream() for chat and agent answers)."""
        for chunk in self._llm.create_completion(
            self._prompt(messages),
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            top_p=0.8,
            top_k=20,
            presence_penalty=1.5,
            stream=True,
        ):
            yield chunk["choices"][0]["text"]

    def ask_json(self, messages, schema, max_tokens=512):
        """Router + agent decisions: same prompt, output forced into the JSON schema."""
        self.load()
        grammar = LlamaGrammar.from_json_schema(json.dumps(schema), verbose=False)
        with self._lock:
            out = self._llm.create_completion(
                self._prompt(self.prepare(messages)),
                max_tokens=max_tokens,
                temperature=0.1,
                grammar=grammar,
            )
        text = out["choices"][0]["text"]
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"{self.name}: keine gültige JSON-Antwort: {text[:200]}") from e