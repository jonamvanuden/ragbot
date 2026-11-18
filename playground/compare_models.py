"""Fair speed + quality test: Mistral vs Qwen3.6 (thinking OFF), same question."""
import glob
import time

from jinja2.sandbox import ImmutableSandboxedEnvironment
from llama_cpp import Llama

QUESTION = "Erkläre in zwei Sätzen, was ein Urlaubsplaner in einer Personalabteilung ist."
MODELS = {
    "mistral":   "models/mistral-small-3.2/*.gguf",
    "qwen3.6":   "models/qwen3.6-35b-a3b/*.gguf",
}


def render(llm, messages):
    """Apply the model's own chat template, with thinking switched off (Qwen) – other models ignore the flag."""
    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
    env.globals["raise_exception"] = lambda msg: (_ for _ in ()).throw(Exception(msg))
    env.globals["strftime_now"] = lambda fmt: time.strftime(fmt)
    tmpl = env.from_string(llm.metadata["tokenizer.chat_template"])
    return tmpl.render(messages=messages, add_generation_prompt=True, enable_thinking=False,
                       bos_token="", eos_token="")


for name, pattern in MODELS.items():
    path = sorted(glob.glob(pattern))[0]
    print(f"\n===== {name}: {path}")
    llm = Llama(model_path=path, n_gpu_layers=-1, n_ctx=8192, verbose=False)
    prompt = render(llm, [{"role": "user", "content": QUESTION}])
    llm.create_completion(prompt, max_tokens=1)                 # warm-up (loads kernels, fills cache)
    t0 = time.perf_counter()
    out = llm.create_completion(prompt, max_tokens=300, temperature=0.3)
    secs = time.perf_counter() - t0
    n = out["usage"]["completion_tokens"]
    print(out["choices"][0]["text"].strip())
    print(f"→ {n} tokens in {secs:.1f}s = {n / secs:.1f} tok/s")
    llm.close()
    del llm
