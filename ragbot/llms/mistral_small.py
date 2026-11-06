from ragbot.llms.llamacpp import LlamaCppModel


class MistralSmall(LlamaCppModel):
    name = "mistral-small-3.2"
    filename = "mistral-small-3.2/mistralai_Mistral-Small-3.2-24B-Instruct-2506-Q5_K_M.gguf"
    temperature = 0.15          # Mistral's recommendation
    max_tokens = 1024
    n_ctx = 16384
    # No quirks: follows system prompts, no thinking block -> default hooks are fine.