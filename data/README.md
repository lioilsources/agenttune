# Dataset builder (UC27, fáze 2)

Z lokálních přepisů Claude Code staví `sft.jsonl` a `dpo.jsonl` pro QLoRA na L40S. Builder běží jen lokálně. Surové přepisy nikam neodcházejí, ven jde jen vyčištěný výstup z `data/out/`. Obojí je v `.gitignore`.

```
data/collect.sh   # záloha přepisů do data/raw/projects (rsync jen *.jsonl, nikdy nemaže)
python3 data/build.py --raw data/raw/projects --tasks evals/tasks --out data/out --private data/config.private.json
python3 -m unittest discover -s data/tests
```

`collect.sh` pouštěj před každým buildem. Claude Code staré přepisy maže a záloha drží i ty, které už z `~/.claude/projects` zmizely.

## Git a eval

Každá session dostane štítek podle gitu repa, ve kterém běžela (`cwd`):

- **holdout:** repo patří holdout úloze (`split: holdout` v `evals/tasks/*/task.yaml`). Session vypadne ze všech výstupů.
- **eval_fix:** session vytvořila opravný commit (`fix:`) kterékoli eval úlohy, dev i holdout. Taky vypadne celá.

Repo se určuje pro každou epizodu zvlášť: podle `cwd` jejích řádků a absolutních cest v nástrojích. Session spuštěná ve složce nad repy (dev root) pracuje v několika repech. Taková session nevypadne celá, ale přijde jen o epizody v holdout repu, o epizody, které holdout repo zmiňují, a o epizody v repu, kde vytvořila opravný commit eval úlohy. Celá vypadne, když začala přímo v takovém repu.
- **success:** aspoň jeden její commit je dosažitelný z nějaké větve a není revertnutý. Jen z těchto sessions jde SFT.
- **failed, no_commit, no_repo:** do SFT nejdou. Pravidlové DPO páry a kandidáti ano.

Commity session se hledají dvojím způsobem:
- z výpisu `[větev abc1234]`,
- podle času, protože `git commit -q` hash nevypíše. Bere se každý commit repa do ±180 s od úspěšného volání `git commit`.

Epizoda, po které přijde krátká výtka („ne“, „špatně“, „nefunguje“, „oprav“, „proč jsi“, „wrong“…), do SFT nejde. Když další epizoda edituje text, který ta první právě zapsala, vznikne DPO pár `in-session-correction`:
- rejected je původní volání `write`/`edit`,
- chosen je totéž volání s aplikovanou opravou.

`config.private.json` (vzor v `config.private.example.json`) obsahuje:
- jména vlastníka,
- kmeny jmen, které se nahrazují i uvnitř slov,
- výrazné názvy svazků,
- globy `.env` souborů, jejichž tajné hodnoty se nesmí objevit ve výstupu,
- `exclude`: vyřazené projekty (např. klientská práce). Vypadnou celé, i s epizodami jinde, kde se zmiňují v cestě nebo textu.

Jinde než lokálně se nezapisuje.

## Výstup

| soubor | co |
|---|---|
| `sft.jsonl`, `sft_eval.jsonl` | `{"messages", "tools", "meta"}`. Jedna epizoda = jedno zadání a celá práce na něm, končí odpovědí asistenta. |
| `dpo.jsonl`, `dpo_eval.jsonl` | `{"prompt", "chosen", "rejected", "tools", "meta"}`. Chosen i rejected jsou přesně jeden tah asistenta, prompt končí zprávou `user` nebo `tool`. |
| `dpo_candidates*.jsonl` | Páry, kterým chybí jedna strana. Doplní ji `synth_dpo.py` na L40S. |
| `report.json` | Jen počty: co prošlo, co a proč vypadlo. |
| `sample/` | Asi 20 příkladů ke kontrole, napříč délkami a pravidly. |

Eval je ~5 % epizod podle hashe.

Na každém řádku je stejný seznam šesti nástrojů OpenCode, ve kterém model poběží (`evals/harness`): `bash`, `read`, `edit`, `write`, `grep`, `glob`, se stejnými jmény argumentů (`filePath`, `oldString`, `newString`, `include`). `bash` má povinné `description`, když chybí, doplní se „Run command“. Volání má `arguments` jako JSON string a `tool_call_id` sedí s `id`. Výsledky nástrojů delší než ~2 000 znaků se zkrátí na začátek a konec s `[truncated N chars]`. Příklad nad 16k tokenů (odhad) vypadne.

## Co vypadne celé

- **Epizoda s tajemstvím kdekoli**, ani maskovat se nebude. Tajemstvím se rozumí:
  - token se známým prefixem, JWT nebo privátní klíč,
  - `Bearer` nebo přiřazení k `*token*`/`*secret*`/`*password*` s náhodně vypadající hodnotou,
  - heslo v URL,
  - přesná hodnota z lokálních `.env`.
- **Epizoda s nástrojem mimo těch šest** (subagenti, zprávy jiným sessions, MCP, artefakty). Zahodit jen volání by nechalo trajektorii reagovat na něco, co se nestalo.
- **Epizoda s odmítnutým voláním.** Ta se do SFT nedostane a jde do DPO kandidátů.
- Epizoda přerušená uživatelem nebo neukončená odpovědí.

## Co se nahradí

| co | čím |
|---|---|
| domovské cesty | `~` |
| `/Volumes/<svazek>` | `/work` |
| LAN a veřejné IP | `<IP>`; `127.0.0.1` a `0.0.0.0` zůstávají |
| e-maily | `<EMAIL>` |
| jména | `<NAME>` |
| Apple Key a Team ID | `<KEY_ID>`, `<TEAM_ID>`, `<APPLE_ID>` |
| SSH veřejné klíče | `<SSH_PUBKEY>` |
| URL Claude sessions | `<SESSION_URL>` |
| řádky s atribucí Claude v commitech | odstraní se |

Thinking bloky se zahazují.

## DPO

1. **Deterministické páry.** Chosen je skutečný tah, který pravidlo dodržel. Rejected je tentýž tah s porušeným pravidlem:
   - `git add -A` místo jmen souborů,
   - `git push --force`,
   - `git commit --no-verify`,
   - holé `return err` místo `fmt.Errorf("…: %w", err)` v ne-testovém Go (`go-errors-wrap`),
   - `go get -u ./...` nebo `flutter pub upgrade` před testem či buildem (`no-new-deps`).

   Liší se jen tím rozhodnutím. Na pravidlo nejvýš 400 párů, z jedné session nejvýš 15.
2. **Odmítnutá volání.** Volání zastavil hook, bezpečnostní klasifikátor nebo vlastník. Rejected je skutečný tah, chosen vygeneruje `synth_dpo.py` podle důvodu odmítnutí.
3. **Opravy v session** (`in-session-correction`), viz výše. Z jedné session nejvýš 15.
4. **Jazyk.** Chosen je česká závěrečná odpověď, rejected její anglický přepis od modelu.

5. **Go z historie rep** (`synth_go.py`). V přepisech skoro nejsou úpravy Go kódu, takže mutace `go-errors-wrap` nemá z čeho brát. Skript projde Go repa v dev rootu a z hunků commitů, které vracejí `fmt.Errorf("…: %w", err)`, udělá páry: chosen je skutečná úprava (edit, u nového souboru write), rejected totéž s holým `return err`. Bere jen páry, které by eval označil. Repa všech eval úloh (dev i holdout) a vyloučené projekty přeskočí celé, text jde přes stejný scrubber. Výstup `dpo_go.jsonl` a `dpo_go_eval.jsonl` se před tréninkem připojí k `dpo*.jsonl`.

   ```
   python3 data/synth_go.py --dev-root <složka s repy> --tasks evals/tasks --out data/out --private data/config.private.json
   ```

SFT trajektorie, která porušuje `go-errors-wrap` nebo `no-new-deps` (stejné kontroly jako eval, `agentdata/rules.py`), do SFT nejde. Vypnout jde přes `--no-rule-filter`.

`synth_dpo.py` čte jen vyčištěné kandidáty a volá libovolný OpenAI-kompatibilní endpoint (vLLM na L40S). Odpověď, která neprojde kontrolou formátu, přeskočí a započítá, nehádá ji.
