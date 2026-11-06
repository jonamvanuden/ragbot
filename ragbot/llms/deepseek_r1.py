import re

from ragbot.llms.base import Event
from ragbot.llms.llamacpp import LlamaCppModel

# Chinese/Japanese/Korean characters + CJK punctuation and fullwidth forms
CJK = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")

LANGUAGE_RULE = "Antworte ausschließlich auf Deutsch – niemals auf Englisch oder Chinesisch."


class DeepSeekR1(LlamaCppModel):
    name = "deepseek-r1-14b"
    filename = "deepseek-r1-14b/DeepSeek-R1-Distill-Qwen-14B-Q4_K_M.gguf"
    temperature = 0.6           # DeepSeek's recommendation
    max_tokens = 1500           # thinking eats tokens
    n_ctx = 8192

    def __init__(self):
        super().__init__()
        self._ban_cjk: dict[int, float] = {}

    # Quirk 1: R1 drifts into Chinese -> forbid every CJK token outright.
    def load(self):
        first_time = self._llm is None
        super().load()
        if first_time:
            self._ban_cjk = {
                tok: -100.0
                for tok in range(self._llm.n_vocab())
                if CJK.search(self._llm.detokenize([tok]).decode("utf-8", errors="ignore"))
            }
            print(f"[{self.name}] blocked {len(self._ban_cjk)} CJK tokens")

    def generation_kwargs(self):
        return {"logit_bias": self._ban_cjk}

    # Quirk 2: R1 ignores system prompts -> fold them + a German rule into the last user turn.
    def prepare(self, messages):
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        msgs = [dict(m) for m in messages if m["role"] != "system"]
        if msgs and msgs[-1]["role"] == "user":
            extra = f"\n\n({system})" if system else ""
            msgs[-1]["content"] += f"{extra}\n\n{LANGUAGE_RULE}"
        return msgs

    # Quirk 3: output is "<think>...</think> answer" -> split it while streaming.
    def parse(self, deltas):
        raw, sent_t, sent_a = "", 0, 0
        for d in deltas:
            raw += d
            if "</think>" in raw:
                think, answer = raw.split("</think>", 1)
                think, answer = think.replace("<think>", ""), answer.lstrip()
            else:
                # hold back a few chars so a half-arrived "</think>" never leaks
                think, answer = raw.replace("<think>", "")[:-9], ""
            if len(think) > sent_t:
                yield Event("think", think[sent_t:])
                sent_t = len(think)
            if len(answer) > sent_a:
                yield Event("answer", answer[sent_a:])
                sent_a = len(answer)
        yield Event("done" if "</think>" in raw else "cutoff")