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
- `src/hantek_scope/processing/`: análise sem Qt (por enquanto, `trigger.py`).
- `src/hantek_scope/ui/`: interface; `scope_plot.py` é o gráfico (eixos, cursores,
  linhas do trigger, roda e posição do mouse) e `worker.py`, a thread de aquisição.

O gráfico trabalha em divisões (x de 0 a 10, y de -4 a 4). A conversão para
tempo e tensão fica na `MainWindow`. O tempo exibido é relativo ao disparo.
A captura cobre `CAPTURE_DIVS` (o dobro da tela) para o trigger ter margem.

Regras:

- A thread de aquisição nunca toca em widgets. Ela guarda só o quadro mais
  recente, e a interface o busca, descartando quadros se estiver atrasada.
- Os dados de `Frame` estão sempre em volts, independentemente do backend.
- Novas análises (FFT, Lissajous, filtros, operações) entram em um pacote
  `processing/` sem Qt, recebendo `Frame` e devolvendo arrays NumPy.

## Hipóteses a validar com o aparelho real

Validado com o 6022BL real (Windows 11, firmware 0x0210, 2026-11-08):

- Captura correta do sinal de calibração (1 kHz, 0 a 2 V) e calibração da EEPROM.
- Dois canais sem perda de amostras até 8 MS/s. De 10 a 16 MS/s a perda varia
  entre execuções; a partir de 24 MS/s sempre há perda, a partir de ~6.000 amostras.
- O início das leituras não mostrou amostras ruins.
- Com o firmware aberto, o aparelho vira outra instância USB (`DSO-6022BL`, com
  número de série) e precisa de WinUSB próprio. O driver da Hantek, se instalado,
  ainda carrega o firmware original a cada conexão.

Ainda não verificado: acomodação após trocar o ganho e taxas acima de 8 MS/s com
um canal só.

Para medir perdas, usar o sinal de calibração no CH2 e conferir se todos os
períodos têm 1 ms.

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
