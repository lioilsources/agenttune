# agenttune

<!-- Max 80 řádků. Co se nevejde, jde do docs/ a sem jeden odkaz. Veřejné repo: žádné LAN IP, domovské cesty, ID. -->

## Účel
Evoluční smyčka pro lokální coding model na SPARKu: eval → data → LoRA trénink → eval →
povýšení. Zadání: `DevConventions/Prompts/02-AGENTTUNE_PLAN.md`.

## Build a test
```
python3 scripts/inventory_sessions.py <adresář s přepisy>   # jen počty
scripts/inventory_repos.sh <adresář s repy>                  # kandidáti na eval
```
Testy zatím nejsou; skripty běží na M2 a jen čtou.

## Mapa
- `scripts/`: inventarizační skripty (Python, bash)
- `docs/DATA_INVENTORY.md`: kolik je dat, kandidáti na eval
- `docs/ENV.md`: stav prostředí na SPARKu
- `docs/DECISIONS.md`: rozpory s DevConventions a rozhodnutí

## Invarianty
- Repo je veřejné (DECISIONS R3): do gitu nikdy přepisy sessions ani jejich výňatky,
  `data/raw`, `data/out`, `data/REVIEW.md`, `rules.md`, výsledky evalu s diffy, modely,
  jména klientských rep.
- Přepisy se čistí na M2; na SPARK jde jen vyčištěný `sft.jsonl`/`dpo.jsonl` (R2).
- Trénink na SPARKu jen přes broker, profil `train` (R1).

## Kam dál
`docs/DECISIONS.md`, `docs/ENV.md`, `docs/DATA_INVENTORY.md`
