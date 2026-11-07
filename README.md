# Hantek Scope

Interface amigável e fluida para o osciloscópio USB Hantek 6022BL.

> **Estado atual:** o simulador funciona. O suporte ao 6022BL está implementado,
> mas **ainda não foi testado com o aparelho real**.

## Como rodar

Requer Python 3.12 ou mais recente. No PowerShell, a partir da pasta do projeto:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m hantek_scope
```

Se o 6022BL estiver conectado, o programa o usa; caso contrário, abre no
simulador. A fonte em uso aparece no painel lateral e na barra de status.

## Driver do 6022BL no Windows

O aparelho precisa do driver WinUSB, instalado com o [Zadig](https://zadig.akeo.ie/).
Ele aparece com dois IDs USB, e cada um precisa do driver:

1. Conecte o aparelho, abra o Zadig e ative **Options > List All Devices**.
2. Selecione o dispositivo `04B4:602A`, escolha **WinUSB** e clique em instalar.
3. Abra este programa e clique em **Executar**. Ele envia o firmware para a RAM do
   aparelho, que reaparece como `04B5:602A`. É normal surgir um erro de acesso aqui.
4. Repita o passo 2 para `04B5:602A` e clique em **Executar** de novo.

O firmware não é gravado de forma permanente: ele é enviado a cada vez que o
aparelho é ligado.

## Testes

```powershell
.venv\Scripts\python -m pytest
```

## Roteiro

- Validar o suporte ao 6022BL com o aparelho real
- Trigger, cursores e medições
- FFT, Lissajous (XY), filtros e operações entre canais
- Exportação de dados
- Analisador lógico

As mudanças de cada versão ficam no [CHANGELOG](CHANGELOG.md).

## Licença

[GPL-3.0](LICENSE). A comunicação com o aparelho e o firmware vêm do
[Hantek6022API](https://github.com/Ho-Ro/Hantek6022API), também sob GPL-3.0.
