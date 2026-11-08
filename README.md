# Hantek Scope

Interface amigável e fluida para o osciloscópio USB Hantek 6022BL.

> **Estado atual:** funciona com o 6022BL real no Windows 11 e com o simulador.
> Com os dois canais, a taxa máxima é 16 MS/s; só com o CH1, 30 MS/s.

## Como rodar

Requer Python 3.12 ou mais recente. No PowerShell, a partir da pasta do projeto:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m hantek_scope
```

Se o 6022BL estiver conectado, o programa o usa; caso contrário, abre no
simulador. A fonte em uso aparece no painel lateral e na barra de status.

## Uso

| Ação | Resultado |
|---|---|
| Roda do mouse sobre o gráfico | Muda o tempo/div, aproximando o ponto sob o mouse |
| Arrastar o fundo do gráfico | Desloca o traço na horizontal |
| Ctrl + roda | Muda os volts/div do CH1 |
| Shift + roda | Muda os volts/div do CH2 |
| Arrastar a linha "T" horizontal | Muda o nível do trigger |
| Arrastar a linha "T" vertical | Muda a posição do disparo na tela |
| Arrastar um cursor (T1, V1...) | Move o cursor; crie-os com "+ Tempo" e "+ Tensão" |
| Clique direito em um cursor | Remove o cursor |
| "Posição" do canal | Desloca o traço; a marca à esquerda (1▶, 2▶, M▶) indica o 0 V |

O eixo de tempo mostra o tempo a partir do disparo. O eixo esquerdo usa a escala
do CH1 e o direito, a do CH2. A posição do mouse aparece no canto da barra de
status.

Para examinar uma borda longe do disparo, ponha o mouse sobre ela e role a roda
para cima: a escala diminui com a borda parada sob o mouse. O disparo pode sair
da tela; uma seta na borda indica de que lado ele está, e "Disparo ao centro"
volta ao início.

### Aquisição

- **Autoset:** procura os sinais e ajusta volts/div, posição, tempo/div (dois
  períodos na tela) e o trigger no meio do sinal.
- **Média:** junta de 2 a 128 capturas alinhadas pelo disparo, o que reduz o
  ruído de um sinal repetitivo. Qualquer ajuste recomeça a média.
- **Tempos lentos e modo roll:** o tempo/div vai até 10 s. A partir de 100 ms/div
  o traço corre da direita para a esquerda, sem trigger, com o aparelho
  capturando sem parar. O eixo mostra o tempo até a amostra mais recente.
- **Um canal:** com o CH2 desligado, sem MATH, sem modo XY e com o trigger no
  CH1, só o CH1 é amostrado, e a taxa pode ir a 30 MS/s. A barra de status
  indica "(só CH1)".

As medições automáticas aparecem embaixo do gráfico e usam só a parte visível
da tela. A frequência só é mostrada quando o sinal passa de 0,2 divisão de pico
a pico, para o ruído de um canal sem sinal não virar frequência. Opcionalmente,
mostram também os pulsos (ciclo de trabalho, larguras positiva e negativa,
subida e descida de 10% a 90%) e o atraso e a fase do CH2 em relação ao CH1.

"Pausar tela" congela a imagem sem parar a aquisição. "Salvar imagem" grava um
PNG com as escalas no rodapé. "Salvar dados" grava em CSV as amostras visíveis,
com o tempo a partir do disparo; escolha o formato "Excel em português" para
abrir direto no Excel com vírgula decimal.

### Canais

- **Ponteira 1x/10x:** corrige escalas, medições, cursores, trigger e CSV para a
  tensão na ponta da ponteira. Ajuste a chave da ponteira para o mesmo valor.
- **Acoplamento AC:** remove o nível contínuo no software. Como o contínuo ainda
  passa pelo conversor, nesse modo a faixa de entrada do aparelho é escolhida
  pelo sinal completo, e não pela escala da tela. Um contínuo acima da faixa
  máxima (5 V na entrada, 50 V com ponteira 10x) satura o conversor.
- **Inverter:** mostra o canal com o sinal trocado.
- **Filtro 60 Hz:** remove a interferência da rede e suas harmônicas (120 e
  180 Hz). Precisa de pelo menos dois ciclos da rede na captura, ou seja, de
  2 ms/div ou mais; abaixo disso fica inativo e o painel avisa. Um sinal
  legítimo nessas frequências também é removido.

### Matemática

O traço MATH (verde) mostra CH1 + CH2, CH1 − CH2, CH1 × CH2 ou CH1 ÷ CH2, com
escala e posição próprias. Ao escolher a operação, a escala se ajusta ao sinal;
o botão "Ajustar escala" refaz o ajuste. Na divisão, os pontos em que o CH2 está
muito perto de zero ficam em branco. O MATH aparece nas medições, pode receber os
cursores de tensão e vai para o CSV.

### Análise

- **FFT:** mostra, abaixo da tela, o espectro do trecho visível do CH1, CH2 ou
  MATH, em dB (dBV) ou linear, com o pico e a resolução. A janela Hann serve
  para uso geral; a flat top lê a amplitude certa; a Blackman-Harris separa
  componentes fracos de fortes. No gráfico da FFT, roda e arrasto dão zoom, e
  "Ajustar FFT" volta a enquadrar.
- **Modo XY:** desenha o CH2 contra o CH1, nas escalas e posições dos canais,
  para figuras de Lissajous e comparação de fase.

## Driver do 6022BL no Windows

Coloque a chave do aparelho em **H** (osciloscópio). Na posição P, ele pode se
identificar como analisador lógico Saleae (`0925:3881`).

O programa precisa do driver WinUSB, instalado com o [Zadig](https://zadig.akeo.ie/).
O aparelho aparece no Windows em etapas, e cada uma é um dispositivo diferente:

| Nome no Zadig | ID USB | Firmware em uso |
|---|---|---|
| `Hantek6022BL` | `04B4:602A` | nenhum (se não houver driver da Hantek) |
| `Hantek6022BL` | `04B5:602A` | original da Hantek, carregado pelo driver dela |
| `DSO-6022BL` | `04B5:602A` | aberto, carregado por este programa |

1. Conecte o aparelho, abra o Zadig e ative **Options > List All Devices**.
2. Selecione `Hantek6022BL`, escolha **WinUSB** e clique em **Replace Driver**.
3. Abra este programa e clique em **Executar**. Ele envia o firmware aberto, e o
   aparelho reaparece como `DSO-6022BL`. É normal surgir um erro de acesso aqui.
4. Sem desconectar o cabo, repita o passo 2 para `DSO-6022BL` e clique em
   **Executar** de novo.

A troca é feita uma vez só. O firmware não é gravado no aparelho: ele vai para a
RAM e é enviado de novo sempre que o aparelho é ligado. Enquanto o WinUSB estiver
instalado, o programa original da Hantek não funciona com o aparelho.

## Testes

```powershell
.venv\Scripts\python -m pytest
```

## Roteiro

- Estatística das medições (mínimo, máximo e média ao longo do tempo)
- Persistência, traço de referência e janela de zoom
- Trigger por largura de pulso e holdoff
- Analisador lógico e decodificação de UART, I2C e SPI

As mudanças de cada versão ficam no [CHANGELOG](CHANGELOG.md).

## Licença

[GPL-3.0](LICENSE). A comunicação com o aparelho e o firmware vêm do
[Hantek6022API](https://github.com/Ho-Ro/Hantek6022API), também sob GPL-3.0.
