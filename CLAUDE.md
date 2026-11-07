# Hantek Scope

Interface de osciloscópio para o Hantek 6022BL, em Python, PySide6 e pyqtgraph.
Objetivo: uso geral, com interface mais amigável e fluida que as alternativas.

## Comandos (PowerShell)

- Instalar: `.venv\Scripts\python -m pip install -e ".[dev]"`
- Executar: `.venv\Scripts\python -m hantek_scope`
- Testar: `.venv\Scripts\python -m pytest`

## Arquitetura

- `src/hantek_scope/acquisition/`: backends que implementam `ScopeBackend`
  (`base.py`). Não pode depender de Qt.
- `src/hantek_scope/timebase.py`: converte tempo por divisão em configuração de
  captura.
- `src/hantek_scope/ui/`: interface e `AcquisitionWorker`, a thread de aquisição.

Regras:

- A thread de aquisição nunca toca em widgets. Ela guarda só o quadro mais
  recente, e a interface o busca, descartando quadros se estiver atrasada.
- Os dados de `Frame` estão sempre em volts, independentemente do backend.
- Novas análises (FFT, Lissajous, filtros, operações) entram em um pacote
  `processing/` sem Qt, recebendo `Frame` e devolvendo arrays NumPy.

## Hipóteses a validar com o aparelho real

- As taxas de amostragem em `simulator.py` e a faixa de ±5 V são suposições.
- Volts por divisão só afeta a exibição por enquanto. No aparelho real, também
  deverá selecionar o ganho de entrada.

## Convenções

- Interface e documentação em português; identificadores em inglês.
- Commits no estilo Conventional Commits (`feat:`, `fix:`, `docs:`...).
- Uma branch por funcionalidade; a `main` sempre funcional.
- Versões em SemVer, com tag `vX.Y.Z` e registro no `CHANGELOG.md`.
- Toda lógica sem Qt deve ter teste em `tests/`.
