from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator, Literal


@dataclass
class Event:
    kind: Literal["think", "answer", "done", "cutoff", "usage"]
    text: str = ""
    data: dict | None = None     # used by "usage": {"prompt_tokens": .., "completion_tokens": ..}


@dataclass
class Reply:
    thinking: str
    answer: str
    complete: bool          # False = ran out of tokens before finishing


class ChatModel(ABC):
    """The only thing the rest of the app talks to. All model quirks live in subclasses."""

    name: str = "base"
    temperature: float = 0.7
    max_tokens: int = 1024

    @abstractmethod
    def stream(self, messages: list[dict]) -> Iterator[Event]:
        """Yield think/answer pieces, then one 'done' or 'cutoff', then one 'usage'."""

    def ask(self, messages: list[dict]) -> Reply:
        thinking, answer, complete = [], [], False
        for ev in self.stream(messages):
            if ev.kind == "think":
                thinking.append(ev.text)
            elif ev.kind == "answer":
                answer.append(ev.text)
            elif ev.kind == "done":
                complete = True
        return Reply("".join(thinking).strip(), "".join(answer).strip(), complete)

    def ask_json(self, messages: list[dict], schema: dict, max_tokens: int = 512) -> dict:
        """Return one JSON object matching `schema`. Used by the agent to pick tools."""
        raise NotImplementedError(f"{self.name} unterstützt keine JSON-Ausgabe")

    def load(self) -> None:
        """Warm up (load weights). Optional."""

    def unload(self) -> None:
        """Free GPU memory. Optional."""