from machine import I2C, Pin  # type: ignore # noqa: INP001 -- This is a micropython library


class ADS112:
    """ADS1112 I2C ADC driver for ESP32."""
    def __init__(self, i2c, address: int):
        """:param address: I2C address of the ADS1112 device (can be specified as hex, e.g., 0x48)"""

        self.i2cInstance = i2c
        self.i2cAddress = address

        # Available Registers
        registers = {
            "MUX_GAIN_PGA": 0x00,
            "DR_MODE_CM_VREF_TS": 0x01,
            "DRDY_DCNT_CRC_BCS_IDAC": 0x02,
            "IDAC1_IDAC2": 0x03,
        }

    def readRegister(self, register: int) -> int:
        """Read a 16-bit register from the ADS1112."""
        self.i2cInstance.writeto(self.i2cAddress, bytes([register]))
        data = self.i2cInstance.readfrom(self.i2cAddress, 2)
        return int.from_bytes(data, 'big')
