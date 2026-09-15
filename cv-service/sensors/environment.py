"""
Sensor de temperatura/umidade SIMULADO.

Em produção real, isso seria:
  - DHT22/AM2302 via pyserial + lib como adafruit-circuitpython-dht
  - BME280 via I2C (pico/bus servo)
  - API meteorológica externa (OpenWeather, OpenMeteo)

Por enquanto gera valores oscilando em torno de um setpoint realista
(temperatura ~25°C, umidade ~60%). Padrões:
  - diária lenta (~12h) simulando variação dia/noite
  - ruído gaussiano pequeno (~0.5°C, ~3% umidade)
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass


@dataclass
class EnvironmentReading:
    temperature_c: float
    humidity_pct: float
    source: str = "simulado"


class SimulatedEnvironmentSensor:
    """Gera temp/umidade realistas que oscilam suavemente."""

    def __init__(
        self,
        temp_base: float = 25.0,
        temp_amplitude_day: float = 5.0,
        humidity_base: float = 60.0,
        humidity_amplitude_day: float = 15.0,
        period_seconds: float = 12 * 60,  # 12 min = "12 horas" comprimidas pra demo
    ):
        self.temp_base = temp_base
        self.temp_amplitude = temp_amplitude_day
        self.humidity_base = humidity_base
        self.humidity_amplitude = humidity_amplitude_day
        self.period_seconds = period_seconds
        self._start = time.time()

    def read(self) -> EnvironmentReading:
        elapsed = time.time() - self._start
        # onda senoidal suave
        phase = (elapsed / self.period_seconds) * 2 * math.pi
        temp = self.temp_base + self.temp_amplitude * math.sin(phase) + random.gauss(0, 0.4)
        # umidade costuma ser inversamente proporcional à temperatura
        humidity = (
            self.humidity_base
            - self.humidity_amplitude * math.sin(phase) * 0.6
            + random.gauss(0, 2.5)
        )
        humidity = max(20.0, min(95.0, humidity))
        return EnvironmentReading(
            temperature_c=round(temp, 1),
            humidity_pct=round(humidity, 1),
            source="simulado",
        )

    def status(self) -> dict:
        r = self.read()
        return {
            "temperature_c": r.temperature_c,
            "humidity_pct": r.humidity_pct,
            "source": r.source,
            "temp_status": "Normal" if 18 <= r.temperature_c <= 32 else "Alerta",
            "humidity_status": "Normal" if 40 <= r.humidity_pct <= 80 else "Alerta",
            "ts": time.time(),
        }
