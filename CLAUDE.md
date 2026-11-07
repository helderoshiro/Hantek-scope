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

O backend `hantek6022.py` só foi testado com um `Oscilloscope` falso. Conferir:

- Uso da PyHT6022 no Windows 11 e o fluxo do Zadig nos dois IDs USB.
- Limite de 24 MS/s com dois canais: vem da banda do USB 2.0, sem medição. Procurar
  amostras perdidas nas taxas altas.
- Primeiras amostras de cada leitura e após trocar ganho: podem precisar de descarte.
- Calibração da EEPROM: uma EEPROM em branco geraria offsets errados.
- Sentido da chave do aparelho (osciloscópio/analisador lógico).

## Cuidados com o aparelho e a biblioteca

- Nunca escrever na EEPROM (`set_calibration_values`, `write_eeprom`).
- Não usar `Oscilloscope.flash_firmware()`: ele espera sem limite pelo aparelho.
- `QTest.qWait` longo não libera o GIL para a thread de aquisição; para medir
  desempenho, usar `app.exec()` com `QTimer`.

## Convenções

- Interface e documentação em português; identificadores em inglês.
- Commits no estilo Conventional Commits (`feat:`, `fix:`, `docs:`...).
- Uma branch por funcionalidade; a `main` sempre funcional.
- Versões em SemVer, com tag `vX.Y.Z` e registro no `CHANGELOG.md`.
- Toda lógica sem Qt deve ter teste em `tests/`.
