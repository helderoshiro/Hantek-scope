# Changelog

O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e
as versões seguem o [SemVer](https://semver.org/lang/pt-BR/).

## [Não lançado]

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
