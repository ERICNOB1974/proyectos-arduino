#!/usr/bin/env python3
"""Pruebas automatizadas para simulación ESP32/FreeRTOS de caldera y pileta.

Protocolo: P:<temperatura pileta>, C:<temperatura caldera> + salto de línea.
No requiere dependencias aparte de pyserial.

Cada prueba usa pasos de estabilización y observa eventos reportados por el ESP32.
No puede demostrar el estado físico de GPIO, solo las trazas Serial.
"""

import argparse
import re
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass

try:
    import serial
except ImportError:
    print("Instalá pyserial: sudo pacman -S python-pyserial")
    sys.exit(2)


def normalize(value):
    return "".join(
        c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)
    ).lower()


def kind(line):
    t = normalize(line)
    if "bomba" in t and "encendida" in t:
        return "BOMBA_ON"
    if "bomba" in t and "apagada" in t:
        return "BOMBA_OFF"
    if "valvula" in t and "abierta" in t:
        return "GAS_ON"
    if "valvula" in t and "cerrada" in t:
        return "GAS_OFF"
    if "buffer temperatura pileta:" in t:
        return "BUFFER_P"
    if "buffer temperatura caldera:" in t:
        return "BUFFER_C"
    if "temperatura pileta:" in t:
        return "LECTURA_P"
    if "temperatura caldera:" in t:
        return "LECTURA_C"
    return "OTRO"


@dataclass(frozen=True)
class Event:
    ts: float
    type: str
    line: str


class TestError(Exception):
    pass


class Device:
    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baud, timeout=0.15, write_timeout=2)
        self.events = []
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.stop = threading.Event()
        self.initial = time.monotonic()
        self.pump = None
        self.gas = None
        self.reader_error = None
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def _reader(self):
        while not self.stop.is_set():
            try:
                data = self.ser.readline()
            except (serial.SerialException, OSError) as exc:
                self.reader_error = str(exc)
                return
            if not data:
                continue
            line = data.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            event = Event(time.monotonic(), kind(line), line)
            with self.lock:
                self.events.append(event)
                if event.type == "BOMBA_ON":
                    self.pump = True
                if event.type == "BOMBA_OFF":
                    self.pump = False
                if event.type == "GAS_ON":
                    self.gas = True
                if event.type == "GAS_OFF":
                    self.gas = False
            print(f"[{event.ts - self.initial:7.1f} s] ESP32: {line}", flush=True)

    def send(self, sensor, temperature):
        if sensor not in ("P", "C"):
            raise ValueError("Sólo se permiten P y C")
        if not (0 <= temperature <= 100):
            raise ValueError("Temperatura fuera de rango 0..100")
        data = f"{sensor}:{temperature}\n".encode("ascii")
        with self.write_lock:
            self.ser.write(data)
            self.ser.flush()
        print(f"             PC: {data.decode().strip()}", flush=True)

    def set(self, p=None, c=None):
        if p is not None:
            self.send("P", p)
        if c is not None:
            time.sleep(0.12)
            self.send("C", c)

    def mark(self):
        with self.lock:
            return len(self.events)

    def get(self, marker):
        if self.reader_error:
            raise TestError(f"Se perdió la conexión: {self.reader_error}")
        with self.lock:
            return self.events[marker:].copy()

    def wait(self, event_type, timeout, marker, label=None):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.get(marker):
                if event.type == event_type:
                    return event
            time.sleep(0.08)
        raise TestError(label or f"No apareció {event_type} en {timeout:.1f} s")

    def none(self, event_type, duration, marker, label=None):
        end = time.monotonic() + duration
        while time.monotonic() < end:
            for event in self.get(marker):
                if event.type == event_type:
                    raise TestError(
                        label or f"Evento inesperado {event_type}: {event.line}"
                    )
            time.sleep(0.08)

    def no_repeated_off(self, duration):
        m = self.mark()
        self.none(
            "BOMBA_OFF",
            duration,
            m,
            "La bomba imprime APAGADA repetidamente sin cambiar de estado",
        )

    def settle(self, p=30, c=50, seconds=19):
        """Normaliza entradas. C=40 fuerza bomba OFF antes de C=50.
        No presupone que el dispositivo se reinicie entre pruebas.
        """
        self.set(p=30, c=40)
        time.sleep(18)
        self.set(c=c, p=p)
        time.sleep(seconds)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=1)
        self.ser.close()


class Suite:
    def __init__(self, device, slack):
        self.d = device
        self.slack = slack
        self.results = []

    def expect(self, condition, description):
        if not condition:
            raise TestError(description)

    def run(self, key, fn):
        print(f"\n{'=' * 65}\nPRUEBA: {key}\n{'=' * 65}", flush=True)
        try:
            fn()
        except (TestError, serial.SerialException, OSError) as exc:
            self.results.append((key, False, str(exc)))
            print(f"ATENCION, ERROR DE FUNCIONAMIENTO\n{key}: {exc}", flush=True)
        else:
            self.results.append((key, True, ""))
            print(f"APROBADO: {key}", flush=True)

    def transmission(self):
        m = self.d.mark()
        self.d.set(p=27, c=53)
        self.d.wait("BUFFER_P", 4 + self.slack, m, "No confirmó recepción P:27")
        self.d.wait(
            "BUFFER_C",
            4 + self.slack,
            m,
            "No confirmó recepción C:53 (revisá que C acepte 0..100 y no sólo 0/1)",
        )

    def gas_limits(self):
        self.d.set(p=30, c=44)
        m = self.d.mark()
        self.d.wait("GAS_ON", 19 + self.slack, m, "A 44°C no abrió válvula de gas")
        m = self.d.mark()
        self.d.set(c=52)
        time.sleep(18)
        self.d.none("GAS_OFF", 0.2, m, "Cerró gas dentro de histéresis (52°C)")
        m = self.d.mark()
        self.d.set(c=60)
        self.d.wait("GAS_OFF", 19 + self.slack, m, "A 60°C no cerró válvula de gas")
        m = self.d.mark()
        self.d.set(c=50)
        time.sleep(18)
        self.d.none("GAS_ON", 0.2, m, "Reabrió gas antes de llegar al mínimo (50°C)")
        m = self.d.mark()
        self.d.set(c=45)
        self.d.wait("GAS_ON", 19 + self.slack, m, "A 45°C no reabrió válvula de gas")

    def pump_start_55(self):
        self.d.set(p=25, c=44)
        time.sleep(19)
        m = self.d.mark()
        self.d.set(c=54)
        self.d.none("BOMBA_ON", 19, m, "Encendió bomba con caldera a 54°C")
        m = self.d.mark()
        self.d.set(c=55)
        self.d.wait(
            "BOMBA_ON", 19 + self.slack, m, "No encendió bomba a 55°C con pileta fría"
        )

    def pump_low_temp(self):
        self.d.set(p=25, c=44)
        time.sleep(19)
        m = self.d.mark()
        self.d.set(c=56)
        self.d.wait(
            "BOMBA_ON", 19 + self.slack, m, "No se encendió para preparar la prueba"
        )
        m = self.d.mark()
        self.d.set(c=44)
        self.d.wait(
            "BOMBA_OFF",
            19 + self.slack,
            m,
            "No apagó bomba cuando la caldera bajó de 45°C",
        )
        self.d.no_repeated_off(3.1)

    def pump_high_temp(self):
        self.d.set(p=25, c=44)
        time.sleep(19)
        m = self.d.mark()
        self.d.set(c=56)
        self.d.wait(
            "BOMBA_ON", 19 + self.slack, m, "No encendió bomba para preparar prueba"
        )
        m = self.d.mark()
        self.d.set(c=61)
        self.d.wait("BOMBA_OFF", 19 + self.slack, m, "No apagó bomba con caldera >60°C")

    def pool_hysteresis(self):
        self.d.set(p=30, c=44)
        time.sleep(19)
        self.d.set(c=56)
        m = self.d.mark()
        self.d.none("BOMBA_ON", 19, m, "Bomba arrancó con pileta a 30°C")
        self.d.set(p=27)
        m = self.d.mark()
        self.d.none(
            "BOMBA_ON", 8, m, "Arrancó con 27°C sin haber bajado primero a 26°C"
        )
        self.d.set(p=26)
        m = self.d.mark()
        self.d.wait(
            "BOMBA_ON",
            10 + self.slack,
            m,
            "No arrancó al bajar pileta a 26°C (caldera 56°C)",
        )

    def min_runtime(self):
        self.d.set(p=25, c=44)
        time.sleep(19)
        m = self.d.mark()
        self.d.set(c=56)
        started = self.d.wait("BOMBA_ON", 19 + self.slack, m, "No encendió bomba")
        self.d.set(p=30)
        m = self.d.mark()
        # Debe respetar 30 segundos desde ON en la interpretación utilizada
        elapsed = time.monotonic() - started.ts
        remaining = max(0, 30 - elapsed - 1.5)
        self.d.none("BOMBA_OFF", remaining, m, "Apagó antes de 30 segundos mínimos")
        stopped = self.d.wait(
            "BOMBA_OFF",
            13 + self.slack,
            m,
            "No apagó bomba al llegar a 30°C tras tiempo mínimo",
        )
        delta = stopped.ts - started.ts
        self.expect(
            28.5 <= delta <= 40 + self.slack,
            f"Duración bomba {delta:.1f}s; esperados unos 30s desde el encendido",
        )
        m = self.d.mark()
        self.d.none("BOMBA_ON", 7, m, "La bomba volvió a encender con pileta a 30°C")

    def report(self):
        print("\n" + "=" * 65)
        print("RESUMEN")
        print("=" * 65)
        for name, ok, reason in self.results:
            print(
                f"{'APROBADO' if ok else 'ERROR   '} | {name}"
                + (f" | {reason}" if reason else "")
            )
        failed = sum(not ok for _, ok, _ in self.results)
        print(f"\nAprobadas: {len(self.results) - failed}  |  Fallidas: {failed}")
        if failed:
            print("\nATENCION, ERROR DE FUNCIONAMIENTO")
        print(
            "\nNOTAS: La prueba de 30 s asume tiempo mínimo contado desde el encendido."
        )
        print("No se verifican GPIO físicos ni seguridad de una caldera real.")
        print("Los valores de sensores se consumen a 6/15 s: se tolera esa latencia.")
        return 1 if failed else 0


def main():
    p = argparse.ArgumentParser(
        description="Pruebas automáticas de caldera + pileta en ESP32"
    )
    p.add_argument("puerto", help="Ejemplo /dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument(
        "--test",
        default="todos",
        choices=[
            "todos",
            "serial",
            "gas",
            "bomba-55",
            "bomba-45",
            "bomba-60",
            "pileta",
            "tiempo-30",
        ],
    )
    p.add_argument(
        "--tolerancia",
        type=float,
        default=3.0,
        help="Segundos de tolerancia extra para los mensajes (predeterminado 3)",
    )
    args = p.parse_args()
    try:
        d = Device(args.puerto, args.baud)
    except (serial.SerialException, OSError) as exc:
        print(
            f"ERROR: No se pudo abrir {args.puerto}: {exc}\nCerrá el Monitor Serie de Arduino."
        )
        return 2
    suite = Suite(d, args.tolerancia)
    mapping = [
        ("serial", suite.transmission),
        ("gas", suite.gas_limits),
        ("bomba-55", suite.pump_start_55),
        ("bomba-45", suite.pump_low_temp),
        ("bomba-60", suite.pump_high_temp),
        ("pileta", suite.pool_hysteresis),
        ("tiempo-30", suite.min_runtime),
    ]
    try:
        for key, test in mapping:
            if args.test == "todos" or args.test == key:
                suite.run(key, test)
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario")
        return 130
    finally:
        d.close()
    return suite.report()


if __name__ == "__main__":
    sys.exit(main())
