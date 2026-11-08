# Hantek Scope

Interface amigável e fluida para o osciloscópio USB Hantek 6022BL.

> **Estado atual:** funciona com o 6022BL real no Windows 11 e com o simulador.
> Com os dois canais, a taxa máxima é 8 MS/s.

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
| Roda do mouse sobre o gráfico | Muda o tempo/div |
| Ctrl + roda | Muda os volts/div do CH1 |
| Shift + roda | Muda os volts/div do CH2 |
| Arrastar a linha "T" horizontal | Muda o nível do trigger |
| Arrastar a linha "T" vertical | Muda a posição do disparo na tela |
| Arrastar um cursor (T1, V1...) | Move o cursor; crie-os com "+ Tempo" e "+ Tensão" |
| Clique direito em um cursor | Remove o cursor |

O eixo de tempo mostra o tempo a partir do disparo. O eixo esquerdo usa a escala
do CH1 e o direito, a do CH2. A posição do mouse aparece no canto da barra de
status.

As medições automáticas aparecem embaixo do gráfico e usam só a parte visível
da tela. A frequência só é mostrada quando o sinal passa de 0,2 divisão de pico
a pico, para o ruído de um canal sem sinal não virar frequência.

"Pausar tela" congela a imagem sem parar a aquisição. "Salvar imagem" grava um
PNG com as escalas no rodapé. "Salvar dados" grava em CSV as amostras visíveis,
com o tempo a partir do disparo; escolha o formato "Excel em português" para
abrir direto no Excel com vírgula decimal.

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

- Taxas maiores com um canal só e tempos por divisão mais lentos
- FFT, Lissajous (XY), filtros e operações entre canais
- Exportação de dados
- Analisador lógico

As mudanças de cada versão ficam no [CHANGELOG](CHANGELOG.md).

## Licença

[GPL-3.0](LICENSE). A comunicação com o aparelho e o firmware vêm do
[Hantek6022API](https://github.com/Ho-Ro/Hantek6022API), também sob GPL-3.0.
