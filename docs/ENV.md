# Prostředí na SPARKu (fáze 0, kroky 1–2)

**Stav: blokováno, 2. 11. 2026.** Broker (`sparkctl catalog`) nemá profil pro trénink ani pro vlastní kontejner, jen `comfy`, `director`, `gemma`, `gptoss` a `llm`. Podle pravidel se na SPARKu nesmí spouštět kontejnery mimo broker, takže testy PyTorch, bitsandbytes, flash-attn, unsloth, trl ani llama.cpp zatím neproběhly. Viz `DECISIONS.md` R1.

Známé ze `DevConventions/shared/spark-gotchas.md`: GB10, unified paměť 121,7 GiB; torch vidí jen `MemFree`, page cache se nepočítá; CUDA alokace OOM killer nevidí. Před tréninkem je potřeba vyklidit page cache (`posix_fadvise(DONTNEED)`) a neběžet souběžně s ComfyUI ani LLM.

| test | výsledek | verze |
|---|---|---|
| PyTorch vidí GPU, bf16 matmul | čeká na R1 | |
| bitsandbytes 4bit na sm_121 | čeká na R1 | |
| flash-attn / SDPA | čeká na R1 | |
| unsloth 10 kroků na 0.5B | čeká na R1 | |
| trl SFTTrainer / DPOTrainer | čeká na R1 | |
| llama.cpp CUDA build, HF→GGUF, llama-server | čeká na R1 | |
