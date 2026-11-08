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
- `src/hantek_scope/processing/`: análise sem Qt (`trigger.py`, `measurements.py`,
  `timing.py` para pulsos e atraso, `channel.py` para ponteira, inversão, filtro
  de rede e AC, `math_channel.py` para as operações entre canais e `spectrum.py`
  para a FFT).
- `src/hantek_scope/export.py`: gravação de CSV, sem Qt.
- `src/hantek_scope/ui/`: interface; `scope_plot.py` é o gráfico (eixos, cursores,
  linhas do trigger, roda, arrasto e posição do mouse), `analysis_plots.py` traz
  a FFT e o XY, `units.py` formata valores e `worker.py` é a thread de aquisição.

O gráfico trabalha em divisões (x de 0 a 10, y de -4 a 4). A conversão para
tempo e tensão fica na `MainWindow`. O tempo exibido é relativo ao disparo, que
fica em `_trigger_x` e pode estar fora da tela. A captura cobre `CAPTURE_DIVS`
(o dobro da tela) mais a distância do disparo até a tela.

No modo roll (`is_roll`, a partir de 100 ms/div), o worker entrega todos os
quadros (`take_frames`), o backend lê em fluxo contínuo (`CaptureConfig.stream`)
e a `MainWindow` emenda os trechos pelos horários (`Frame.end_time`).

Regras:

- A thread de aquisição nunca toca em widgets. Fora do roll, a interface pega só
  o quadro mais recente e descarta os outros se estiver atrasada.
- Os dados de `Frame` estão sempre em volts na entrada do aparelho,
  independentemente do backend. A `MainWindow` guarda o quadro original e os
  traços condicionados (`_traces`, `_math`), refeitos a cada ajuste.
- Novas análises (FFT, Lissajous, filtros, operações) entram em um pacote
  `processing/` sem Qt, recebendo `Frame` e devolvendo arrays NumPy.

## Hipóteses a validar com o aparelho real

Validado com o 6022BL real (Windows 11, firmware 0x0210, 2026-11-08):

- Captura correta do sinal de calibração (1 kHz, 0 a 2 V) e calibração da EEPROM.
- Cada leitura começa com 2048 bytes velhos do FIFO do aparelho (o salto no
  sinal fica na amostra 1024 com dois canais), mas só em parte das leituras,
  o que fazia parecer perda intermitente. `STALE_FIFO_BYTES` os descarta.
- Depois disso, de 4 a 16 MS/s com dois canais, 2% a 3% das capturas perdem um
  trecho no meio, igual em todas as taxas; ler com transferências assíncronas
  em fila não mudou nada. A média descarta essas capturas.
- Modo roll com leitura contínua: 20 mil pontos por tela sem lacuna, sinal de
  calibração medido em 1 kHz.
- Um canal funciona, mas a perda de amostras não foi medida (o sinal de
  calibração precisa estar no CH1); o limite de 30 MS/s vem da taxa em bytes.
- Com o firmware aberto, o aparelho vira outra instância USB (`DSO-6022BL`, com
  número de série) e precisa de WinUSB próprio. O driver da Hantek, se instalado,
  ainda carrega o firmware original a cada conexão.

Ainda não verificado: acomodação após trocar o ganho, taxas acima de 8 MS/s com
um canal só e o filtro de 60 Hz com interferência real da rede (no aparelho, só
com o sinal de calibração).

O filtro de rede ajusta senos por mínimos quadrados ponderados por uma janela
de Hann; sem a janela, sinais sem ciclos inteiros na captura vazam alguns por
cento para o ajuste.

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
