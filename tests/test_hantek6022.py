import numpy as np
import pytest
import usb1
from PyHT6022.Firmware import dso6022bl_firmware
from PyHT6022.LibUsbScope import Oscilloscope

from hantek_scope.acquisition import hantek6022
from hantek_scope.acquisition.base import CaptureConfig, ScopeError
from hantek_scope.acquisition.hantek6022 import Hantek6022Backend, range_index_for
from hantek_scope.timebase import capture_for


class FakeDevice:
    def __init__(self, vendor_id, product_id=Oscilloscope.PRODUCT_ID_BL):
        self._vendor_id = vendor_id
        self._product_id = product_id

    def getVendorID(self):
        return self._vendor_id

    def getProductID(self):
        return self._product_id


class FakeHandle:
    def __init__(self):
        self.firmware_packets = 0
        self.bulk_sizes = []
        self.next_byte = 0

    def controlWrite(self, *args, timeout=0):
        self.firmware_packets += 1
        return len(args[4])

    def bulkRead(self, endpoint, size, timeout=0):
        # Bytes numerados, para conferir que nada se perde entre leituras.
        self.bulk_sizes.append(size)
        data = bytes((self.next_byte + i) % 256 for i in range(size))
        self.next_byte = (self.next_byte + size) % 256
        return data


class FakeScope(Oscilloscope):
    """Mantém as conversões reais da biblioteca e troca só o acesso USB."""

    def __init__(self, firmware=True, found=True, product_id=Oscilloscope.PRODUCT_ID_BL,
                 boots_firmware=True, open_error=None, read_error=None, adc=128):
        super().__init__()
        self.found = found
        self.product_id = product_id
        self.has_firmware = firmware
        self.boots_firmware = boots_firmware
        self.open_error = open_error
        self.read_error = read_error
        self.adc = adc
        self.uploaded = 0
        self.calls = []
        self.read_sizes = []

    def setup(self):
        if not self.found:
            return False
        vendor = self.FIRMWARE_PRESENT_VENDOR_ID if self.has_firmware else self.NO_FIRMWARE_VENDOR_ID
        self.device = FakeDevice(vendor, self.product_id)
        self.is_device_firmware_present = self.has_firmware
        return True

    def open_handle(self):
        if self.open_error is not None:
            raise self.open_error
        self.device_handle = FakeHandle()
        return True

    def close_handle(self, release_interface=False):
        if self.device_handle is not None:
            self.uploaded += self.device_handle.firmware_packets
            if self.boots_firmware and self.device_handle.firmware_packets:
                self.has_firmware = True
        self.device_handle = None
        return True

    def get_calibration_values(self, size=32, timeout=0):
        self.calls.append("calibration")

    def set_sample_rate(self, rate_index, timeout=0):
        self.calls.append(("rate", rate_index))
        return True

    def set_ch1_voltage_range(self, range_index, timeout=0):
        self.calls.append(("ch1", range_index))
        return True

    def set_ch2_voltage_range(self, range_index, timeout=0):
        self.calls.append(("ch2", range_index))
        return True

    def start_capture(self, timeout=0):
        self.calls.append("start")
        return True

    def stop_capture(self, timeout=0):
        self.calls.append("stop")
        return True

    def set_num_channels(self, nchannels, timeout=0):
        self.calls.append(("channels", nchannels))
        self.num_channels = nchannels
        return True

    def read_data(self, data_size=0x400, raw=False, timeout=0):
        if self.read_error is not None:
            raise self.read_error
        self.read_sizes.append(data_size)
        second = bytes([self.adc]) * data_size if self.num_channels == 2 else b""
        return bytes([self.adc]) * data_size, second


def backend_with(scope):
    return Hantek6022Backend(scope_factory=lambda: scope)


@pytest.mark.parametrize(
    ("volts_per_div", "index"),
    [(0.05, 10), (0.1, 10), (0.2, 5), (0.5, 2), (1.0, 1), (5.0, 1)],
)
def test_range_covers_the_screen_height(volts_per_div, index):
    assert range_index_for(volts_per_div) == index


def test_sample_rates_stay_within_usb_bandwidth():
    assert max(Hantek6022Backend.sample_rates) == 16e6
    assert min(Hantek6022Backend.sample_rates) == 20e3
    assert max(Hantek6022Backend.single_channel_rates) == hantek6022.MAX_ONE_CHANNEL_RATE


def test_every_timebase_uses_a_rate_the_device_accepts():
    for time_per_div in (1e-6, 200e-6, 10e-3):
        config = capture_for(time_per_div, Hantek6022Backend.sample_rates)
        assert config.sample_rate in hantek6022.RATE_INDEX


def test_open_without_device_reports_not_found():
    with pytest.raises(ScopeError, match="não encontrado"):
        backend_with(FakeScope(found=False)).open()


def test_other_model_is_rejected():
    with pytest.raises(ScopeError, match="não encontrado"):
        backend_with(FakeScope(product_id=Oscilloscope.PRODUCT_ID_BE)).open()


def test_missing_driver_names_the_usb_id():
    scope = FakeScope(firmware=False, open_error=usb1.USBErrorNotSupported())
    with pytest.raises(ScopeError, match="04B4:602A"):
        backend_with(scope).open()


def test_open_uploads_bl_firmware_when_missing():
    scope = FakeScope(firmware=False)
    backend_with(scope).open()
    assert scope.uploaded == len(list(dso6022bl_firmware))
    assert scope.is_device_firmware_present
    assert "calibration" in scope.calls


def test_open_skips_upload_when_firmware_is_present():
    scope = FakeScope(firmware=True)
    backend_with(scope).open()
    assert scope.uploaded == 0


def test_upload_gives_up_when_device_does_not_return(monkeypatch):
    monkeypatch.setattr(hantek6022, "REENUMERATION_TIMEOUT_S", 0.2)
    scope = FakeScope(firmware=False, boots_firmware=False)
    with pytest.raises(ScopeError, match="Desconecte"):
        backend_with(scope).open()


def test_configure_sends_only_what_changed():
    scope = FakeScope()
    backend = backend_with(scope)
    backend.open()
    scope.calls.clear()

    backend.configure(CaptureConfig(1e6, 1000, (1.0, 0.1)))
    assert scope.calls == [("channels", 2), ("rate", 1), ("ch1", 1), ("ch2", 10)]

    scope.calls.clear()
    backend.configure(CaptureConfig(4e6, 2000, (1.0, 0.1)))
    assert scope.calls == [("rate", 4)]


def test_read_frame_trims_to_requested_samples_and_converts_to_volts():
    scope = FakeScope(adc=153)
    backend = backend_with(scope)
    backend.open()
    backend.configure(CaptureConfig(1e6, 300, (1.0, 1.0)))

    frame = backend.read_frame()

    assert frame.channels.shape == (2, 300)
    assert scope.read_sizes == [1536]  # 300 + 1024 descartadas, em pacotes inteiros.
    # (153 - 128) contagens, com 5,12 V / 128 por contagem e ganho padrão de 1,01.
    np.testing.assert_allclose(frame.channels, 25 * 5.12 * 1.01 / 128)


def test_read_requests_whole_usb_packets():
    scope = FakeScope()
    backend = backend_with(scope)
    backend.open()
    backend.configure(CaptureConfig(8e6, 241, (1.0, 1.0)))
    backend.read_frame()
    # 241 amostras mais as 1024 do FIFO, em 2 canais: 2530 bytes, ou 5 pacotes de 512.
    assert scope.read_sizes == [1280]


def test_usb_failure_during_read_becomes_scope_error():
    scope = FakeScope()
    backend = backend_with(scope)
    backend.open()
    backend.configure(CaptureConfig(1e6, 100, (1.0, 1.0)))
    scope.read_error = usb1.USBErrorNoDevice()
    with pytest.raises(ScopeError, match="comunicação"):
        backend.read_frame()


def test_is_connected_only_for_the_6022bl():
    assert backend_with(FakeScope()).is_connected()
    assert not backend_with(FakeScope(found=False)).is_connected()
    assert not backend_with(FakeScope(product_id=Oscilloscope.PRODUCT_ID_BE)).is_connected()


def test_single_channel_reads_only_ch1():
    scope = FakeScope(adc=153)
    backend = backend_with(scope)
    backend.open()
    backend.configure(CaptureConfig(30e6, 300, (1.0, 1.0), channels=1))
    assert ("channels", 1) in scope.calls
    frame = backend.read_frame()
    assert frame.channels.shape == (2, 300)
    np.testing.assert_allclose(frame.channels[0], 25 * 5.12 * 1.01 / 128)
    assert not frame.channels[1].any()
    # Com um canal, o FIFO velho são 2048 amostras: 2348 bytes, ou 5 pacotes de 512.
    assert scope.read_sizes == [2560]


def test_stream_keeps_capturing_and_loses_no_bytes_between_frames():
    scope = FakeScope()
    backend = backend_with(scope)
    backend.open()
    backend.configure(CaptureConfig(20e3, 300, (1.0, 1.0), stream=True))
    scope.calls.clear()
    adc = []
    original = scope.adc_to_voltage
    scope.adc_to_voltage = lambda counts, **kw: adc.append(counts) or original(counts, **kw)
    for _ in range(3):
        backend.read_frame()
    assert scope.calls == ["start"]  # Liga uma vez e não desliga entre quadros.
    # Bytes intercalados CH1, CH2: depois dos 2048 bytes velhos do FIFO, o CH1
    # recebe os pares, sem pular nenhum entre um quadro e o seguinte.
    ch1 = np.concatenate([adc[0], adc[2], adc[4]])
    assert np.array_equal(ch1, np.arange(2048, 2048 + 1800, 2) % 256)
    # Cada leitura pede pacotes inteiros; a sobra fica para a próxima.
    assert all(size % 512 == 0 for size in scope.device_handle.bulk_sizes)


def test_stream_stops_when_the_rate_changes_and_on_close():
    scope = FakeScope()
    backend = backend_with(scope)
    backend.open()
    backend.configure(CaptureConfig(20e3, 300, (1.0, 1.0), stream=True))
    backend.read_frame()
    backend.configure(CaptureConfig(20e3, 300, (0.1, 1.0), stream=True))
    assert "stop" not in scope.calls  # Trocar o ganho não interrompe a captura.
    backend.configure(CaptureConfig(50e3, 300, (0.1, 1.0), stream=True))
    assert scope.calls.count("stop") == 1
    backend.read_frame()
    backend.close()
    assert scope.calls.count("stop") == 2


def test_triggered_read_drops_the_stale_fifo_bytes():
    scope = FakeScope()
    # Primeiras 1024 amostras velhas (ADC 0), depois as da captura (ADC 200).
    scope.read_data = lambda size, raw=False, timeout=0: (
        bytes([0]) * 1024 + bytes([200]) * (size - 1024),
        bytes([0]) * 1024 + bytes([200]) * (size - 1024),
    )
    backend = backend_with(scope)
    backend.open()
    backend.configure(CaptureConfig(1e6, 500, (1.0, 1.0)))
    frame = backend.read_frame()
    assert frame.channels.shape == (2, 500)
    assert np.all(frame.channels > 0)
