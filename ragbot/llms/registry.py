import gc
import threading

from ragbot import config
from ragbot.llms.base import ChatModel
from ragbot.llms.deepseek_r1 import DeepSeekR1
from ragbot.llms.mistral_small import MistralSmall

# Adding a model = new file + one entry here.
MODELS: dict[str, type[ChatModel]] = {cls.name: cls for cls in (DeepSeekR1, MistralSmall)}

_active: ChatModel | None = None
_switch = threading.Lock()


def available() -> list[str]:
    return list(MODELS)


def get_model(name: str | None = None) -> ChatModel:
    """Return the requested model, unloading the previous one (only one fits in VRAM)."""
    global _active
    name = name or config.DEFAULT_MODEL
    if name not in MODELS:
        raise ValueError(f"Unknown model '{name}'. Available: {available()}")
    with _switch:
        if _active is None or _active.name != name:
            if _active is not None:
                _active.unload()
                gc.collect()
            _active = MODELS[name]()
        return _active