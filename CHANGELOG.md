# Changelog

O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e
as versões seguem o [SemVer](https://semver.org/lang/pt-BR/).

## [Não lançado]

### Adicionado

- Zoom pela roda no ponto do mouse e arrasto horizontal do traço. O disparo pode
  ficar fora da tela, indicado por uma seta, e "Disparo ao centro" o traz de volta.
- Tempos de 20 ms a 10 s/div; a partir de 100 ms/div, modo roll com captura
  contínua, sem lacunas entre as leituras.
- Taxas de até 16 MS/s quando só o CH1 é necessário.
- FFT do trecho visível, com janelas Hann, flat top, Blackman-Harris e
  retangular, escala em dB ou linear e eixo de frequência linear ou log.
- Modo XY (CH2 contra CH1).
- Média de 2 a 128 capturas alinhadas pelo disparo.
- Autoset de escalas, posição, tempo/div e trigger.
- Medições de ciclo de trabalho, larguras de pulso, subida, descida e atraso e
  fase entre os canais.

### Alterado

- A imagem salva inclui a FFT, quando ela está aberta.

## [0.4.0] - 2026-11-08

### Adicionado

- Seleção da ponteira (1x ou 10x) por canal, com correção de escalas,
  medições, cursores, trigger e dados salvos.
- Posição vertical por canal, com marca do 0 V na borda esquerda.
- Acoplamento AC por software, com a faixa de entrada escolhida pelo sinal
  completo para o nível contínuo não saturar o conversor.
- Inversão de canal.
- Filtro de 60 Hz (e harmônicas até 180 Hz) por canal, ativo a partir de 2 ms/div.
- Traço MATH com soma, diferença, produto e divisão entre os canais, com escala
  e posição próprias, ajuste automático da escala, medições, cursores e CSV.

### Alterado

- O CSV registra ponteira, acoplamento, inversão e filtro de cada canal, e as
  tensões passam a ser as da ponta da ponteira.

## [0.3.0] - 2026-11-08

### Adicionado

- Medições automáticas por canal sobre o gráfico: frequência, período, Vpp,
  Vmín, Vmáx, Vméd e RMS, calculadas na parte visível da tela.
- Botão para pausar a tela sem parar a aquisição.
- Salvar a tela como PNG, com rodapé de escalas e data.
- Salvar os dados da tela em CSV, no formato padrão ou no do Excel em português.
- Até 6 cursores de tempo e 6 de tensão, com diferença em relação ao primeiro;
  clique direito em um cursor o remove.

### Alterado

- Cursores passam a ser adicionados por botões, em vez das caixas de seleção.

## [0.2.0] - 2026-11-08

### Adicionado

- Trigger por borda (subida ou descida), com modos Auto, Normal e Único,
  histerese contra ruído e alinhamento com precisão menor que uma amostra.
  O nível e a posição do disparo podem ser arrastados no gráfico.
- Escalas de tempo (relativo ao disparo) e de tensão nos eixos, uma por canal.
- Cursores de tempo (ΔT e 1/ΔT) e de tensão (ΔV), com leitura sobre o gráfico.
- Posição do mouse em tempo e tensão na barra de status.
- Roda do mouse muda o tempo/div; com Ctrl, os volts/div do CH1; com Shift, os
  do CH2.

### Alterado

- A captura cobre o dobro da tela, para o trigger achar a borda. Nas escalas
  mais lentas, a taxa de amostragem cai pela metade.

## [0.1.0] - 2026-11-08

### Adicionado

- Simulador de 2 canais com quantização de 8 bits.
- Aquisição em thread separada, sempre exibindo o quadro mais recente.
- Interface com Executar/Parar, tempo por divisão e, por canal, ativação e
  volts por divisão, que também seleciona a faixa de entrada do aparelho.
- Suporte ao Hantek 6022BL via PyHT6022, com envio de firmware e calibração da
  EEPROM, testado com o aparelho real no Windows 11. Taxa máxima de 8 MS/s com
  dois canais, a maior sem perda de amostras nas medições.
- Seleção de fonte na interface, abrindo no simulador se não houver aparelho.
- Licença GPL-3.0.
