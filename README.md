# Hantek Scope

Interface amigável e fluida para o osciloscópio USB Hantek 6022BL.

> **Estado atual:** a interface funciona apenas com um **simulador**. O suporte
> ao aparelho real ainda não foi implementado.

## Como rodar

Requer Python 3.12 ou mais recente. No PowerShell, a partir da pasta do projeto:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m hantek_scope
```

## Testes

```powershell
.venv\Scripts\python -m pytest
```

## Roteiro

- Suporte ao 6022BL real via USB
- Trigger, cursores e medições
- FFT, Lissajous (XY), filtros e operações entre canais
- Exportação de dados
- Analisador lógico

As mudanças de cada versão ficam no [CHANGELOG](CHANGELOG.md).
