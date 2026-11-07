import math
import time
from collections.abc import Callable

import numpy as np
import usb1
from PyHT6022.Firmware import dso6022bl_firmware
from PyHT6022.LibUsbScope import Oscilloscope

from hantek_scope.acquisition.base import CaptureConfig, Frame, ScopeError
from hantek_scope.timebase import VERTICAL_DIVS

# Dois canais a 30 MS/s ou mais exigem 60 MB/s, acima do limite do USB 2.0.
MAX_TWO_CHANNEL_RATE = 24e6
RATE_INDEX = {
    rate: index
    for index, (_, rate) in Oscilloscope.SAMPLE_RATES.items()
    if rate <= MAX_TWO_CHANNEL_RATE
}
USB_PACKET_BYTES = 512
CONTROL_TIMEOUT_MS = 1000
READ_MARGIN_MS = 1000
REENUMERATION_TIMEOUT_S = 10.0

MSG_NOT_FOUND = (
    "Hantek 6022BL não encontrado. Verifique o cabo USB e se a chave do aparelho "
    "está no modo osciloscópio."
)
MSG_NO_ACCESS = (
    "Sem acesso ao aparelho ({ids}). Feche outros programas que o estejam usando e "
    "verifique se o driver WinUSB está instalado para este ID (veja o README)."
)
MSG_FIRMWARE_TIMEOUT = (
    "O aparelho não voltou com o firmware esperado. Desconecte o cabo USB, conecte "
    "novamente e tente de novo."
)
MSG_COMMUNICATION = "Falha na comunicação com o aparelho: {error}"


def range_index_for(volts_per_div: float) -> int:
    """Escolhe a menor faixa de entrada em que a altura da tela cabe."""
    half_screen_v = volts_per_div * VERTICAL_DIVS / 2
    fitting = [i for i in (10, 5, 2) if 5.0 / i >= half_screen_v]
    return fitting[0] if fitting else 1


class Hantek6022Backend:
    """Acesso ao aparelho real pela biblioteca PyHT6022.

    O firmware fica na RAM do aparelho e é enviado sempre que ele é ligado.
    """

    name = "Hantek 6022BL"
    n_channels = 2
    sample_rates = tuple(sorted(RATE_INDEX))

    def __init__(self, scope_factory: Callable[[], Oscilloscope] = Oscilloscope) -> None:
        self._scope_factory = scope_factory
        self._scope: Oscilloscope | None = None
        self._config: CaptureConfig | None = None
        self._rate: float | None = None
        self._range_indexes = [1, 1]

    def is_connected(self) -> bool:
        try:
            scope = self._scope_factory()
            return bool(scope.setup()) and scope.device.getProductID() == scope.PRODUCT_ID_BL
        except usb1.USBError:
            return False

    def open(self) -> None:
        scope = self._scope_factory()
        try:
            if not scope.setup() or scope.device.getProductID() != scope.PRODUCT_ID_BL:
                raise ScopeError(MSG_NOT_FOUND)
            self._open_handle(scope)
            if not scope.is_device_firmware_present:
                self._upload_firmware(scope)
                self._open_handle(scope)
            scope.get_calibration_values(timeout=CONTROL_TIMEOUT_MS)
        except usb1.USBError as exc:
            scope.close_handle()
            raise ScopeError(MSG_COMMUNICATION.format(error=exc)) from exc
        except ScopeError:
            scope.close_handle()
            raise
        self._scope = scope
        self._rate = None
        self._range_indexes = [None, None]

    def close(self) -> None:
        if self._scope is not None:
            self._scope.close_handle(release_interface=True)
            self._scope = None

    def configure(self, config: CaptureConfig) -> None:
        scope = self._require_scope()
        indexes = [range_index_for(v) for v in config.volts_per_div] or [1, 1]
        setters = (scope.set_ch1_voltage_range, scope.set_ch2_voltage_range)
        try:
            if config.sample_rate != self._rate:
                scope.set_sample_rate(RATE_INDEX[config.sample_rate], timeout=CONTROL_TIMEOUT_MS)
                self._rate = config.sample_rate
            # Só envia o que mudou: cada troca de ganho aciona o circuito de entrada.
            for channel, (setter, index) in enumerate(zip(setters, indexes)):
                if index != self._range_indexes[channel]:
                    setter(index, timeout=CONTROL_TIMEOUT_MS)
                    self._range_indexes[channel] = index
        except usb1.USBError as exc:
            raise ScopeError(MSG_COMMUNICATION.format(error=exc)) from exc
        self._config = config

    def read_frame(self) -> Frame:
        scope = self._require_scope()
        if self._config is None:
            raise RuntimeError("configure() deve ser chamado antes de read_frame()")
        rate = self._config.sample_rate
        n = self._config.n_samples
        timeout_ms = math.ceil(1000 * n / rate) + READ_MARGIN_MS
        try:
            raw = scope.read_data(_samples_in_whole_packets(n), raw=True, timeout=timeout_ms)
        except usb1.USBError as exc:
            raise ScopeError(MSG_COMMUNICATION.format(error=exc)) from exc
        channels = [
            scope.adc_to_voltage(
                np.frombuffer(data, dtype=np.uint8)[:n].astype(float),
                voltage_range=self._range_indexes[i],
                channel=i + 1,
            )
            for i, data in enumerate(raw)
        ]
        return Frame(sample_rate=rate, channels=np.stack(channels))

    def _require_scope(self) -> Oscilloscope:
        if self._scope is None:
            raise RuntimeError("open() deve ser chamado antes de usar o aparelho")
        return self._scope

    def _open_handle(self, scope: Oscilloscope) -> None:
        ids = f"{scope.device.getVendorID():04X}:{scope.device.getProductID():04X}"
        try:
            opened = scope.open_handle()
        except (usb1.USBErrorNotSupported, usb1.USBErrorAccess, usb1.USBErrorNotFound) as exc:
            raise ScopeError(MSG_NO_ACCESS.format(ids=ids)) from exc
        if not opened:
            raise ScopeError(MSG_NO_ACCESS.format(ids=ids))

    def _upload_firmware(self, scope: Oscilloscope) -> None:
        # Oscilloscope.flash_firmware() espera sem limite pelo aparelho, e travaria
        # o programa se o cabo fosse desconectado durante o envio.
        for packet in dso6022bl_firmware:
            scope.device_handle.controlWrite(
                0x40,
                scope.RW_FIRMWARE_REQUEST,
                packet.value,
                scope.RW_FIRMWARE_INDEX,
                packet.data,
                timeout=CONTROL_TIMEOUT_MS,
            )
        scope.close_handle()

        deadline = time.monotonic() + REENUMERATION_TIMEOUT_S
        while time.monotonic() < deadline:
            if scope.setup() and scope.is_device_firmware_present:
                return
            time.sleep(0.1)
        raise ScopeError(MSG_FIRMWARE_TIMEOUT)


def _samples_in_whole_packets(n_samples: int) -> int:
    """Arredonda a leitura para pacotes USB inteiros, nos dois canais intercalados.

    Pedir menos que um pacote inteiro causa erro de overflow no libusb.
    """
    packets = math.ceil(2 * n_samples / USB_PACKET_BYTES)
    return packets * USB_PACKET_BYTES // 2
