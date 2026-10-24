# Upstream licensing inspection — 2026-10-24

This is an evidence inventory, not a new license grant.

| Source | Inspection | Consequence for CodeLoop |
| --- | --- | --- |
| [QUSETIONS/MiniCode-Python](https://github.com/QUSETIONS/MiniCode-Python) | Public root listing and README inspected on 2026-10-24 expose no root LICENSE; [pyproject.toml](https://raw.githubusercontent.com/QUSETIONS/MiniCode-Python/main/pyproject.toml) has no project license declaration. This establishes what was visible in those locations, not that no permission exists anywhere. | Permission covering the imported Python contribution remains unverified. |
| [LiuMengxuan04/MiniCode](https://github.com/LiuMengxuan04/MiniCode) | [Root LICENSE](https://raw.githubusercontent.com/LiuMengxuan04/MiniCode/main/LICENSE) states MIT, copyright 2026 Liu Mengxuan. The local ts-src/LICENSE is retained. | This grant does not by itself establish licensing for another author's Python additions or an unrecorded imported revision. |
| CodeLoop imported baseline | Initial import 3036dd76e4ca676541a79a64dc6d24ec20baf433 does not record an exact upstream revision. See CONTRIBUTIONS.md. | Do not assert an exact upstream delta or apply a root MIT license as a substitute for missing permission. |

## Trial decision

Retain the existing view/evaluate notice and no root license until Python upstream permission and contribution provenance are resolved. Do not change repository visibility or contact authors as part of this inspection. A future authorization request should identify the imported code/revision and intended redistribution terms.

GitHub's [licensing guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository) distinguishes public visibility and platform viewing/forking from a general reuse license. Existing third-party notices remain in place.
