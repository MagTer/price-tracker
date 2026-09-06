# ICA-nattprobe — resultat

**Frågan:** byter ICA priserna vid midnatt (automatisk batch) eller på
morgonen (manuellt)? Om midnatt kan price-trackerns ICA-fönster flyttas till
efter midnatt så måndagens erbjudanden finns i appen till frukost.

Varje rad är en probekörning (svensk tid). Cellerna är EFFEKTIVT pris
(kampanjpris om det finns, annars ordinarie). Priset byts i intervallet
mellan raden där värdet ändras och raden före.

| Körning (svensk tid) | Bregott Maxi | Bregott Björksätra | Falukorv Maxi | Köttbullar Björksätra |
|---|---|---|---|---|
| Tue 28/07 10:17 (07-28 08:17 UTC) | 39.90 | 50.22 | 37.65 | 78.45 |
| Sun 02/08 22:30 (08-02 20:30 UTC) | 39.90 | 50.22 | 37.65 | 78.45 |
| Mon 03/08 00:15 (08-02 22:15 UTC) | 48.95 | 50.22 | 37.65 | 78.45 |
| Mon 03/08 01:00 (08-02 23:00 UTC) | 48.95 | 50.22 | 37.65 | 78.45 |
| Mon 03/08 03:00 (08-03 01:00 UTC) | 48.95 | 50.22 | ERR 404: blocked? | 78.45 |
| Mon 03/08 06:00 (08-03 04:00 UTC) | 48.95 | 50.22 | 37.65 | 78.45 |

**PRISBYTE UPPTÄCKT** senast Mon 03/08 00:15: Bregott Maxi: 39.90 → 48.95

Rådata: `results.jsonl`. Upplägg och städning: `README.md`.
