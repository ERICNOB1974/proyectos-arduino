
#!/usr/bin/env python3
import argparse
import sys
import threading
import time
from dataclasses import dataclass

try:
    import serial
except ImportError:
    print("Instalar: sudo pacman -S python-pyserial")
    sys.exit(2)

ON = "Bomba: ENCENDIDA"
OFF = "Bomba: APAGADA"
FLOW_ERROR = "ERROR: Bomba detenida por falta de caudal"
RPM_ERRORS = ("ERROR: Bomba sin rotación", "ERROR: Bomba sin rotacion")


@dataclass
class Event:
    when: float
    line: str


class TestFailure(Exception):
    pass


class Tester:
    def __init__(self, port, baud):
        self.ser = serial.Serial(
            port, baudrate=baud, timeout=0.15, write_timeout=2
        )
        self.start = time.monotonic()
        self.events = []
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.stop = threading.Event()
        self.failures = []
        self.passes = []
        self.pump_on = False

        self.reader = threading.Thread(
            target=self._read, daemon=True
        )
        self.reader.start()

    def _read(self):
        while not self.stop.is_set():
            try:
                raw = self.ser.readline()
            except (serial.SerialException, OSError) as exc:
                with self.lock:
                    self.events.append(
                        Event(time.monotonic(), f"FALLO_SERIAL: {exc}")
                    )
                return

            if not raw:
                continue

            line = raw.decode(
                "utf-8", errors="replace"
            ).strip()

            if not line:
                continue

            now = time.monotonic()

            with self.lock:
                self.events.append(Event(now, line))

                if ON in line:
                    self.pump_on = True
                elif OFF in line:
                    self.pump_on = False

            print(
                f"[{now - self.start:7.1f}s] ESP32 > {line}",
                flush=True
            )

    def send(self, key, value):
        msg = f"{key}:{value}\n"

        try:
            with self.write_lock:
                self.ser.write(msg.encode("ascii"))
                self.ser.flush()
        except (serial.SerialException, OSError) as exc:
            raise TestFailure(
                f"No se pudo enviar {msg.strip()}: {exc}"
            )

        print(f"          PC    > {msg.strip()}", flush=True)

    def sensor_values(self, humidity=None, flow=None, rpm=None):
        for key, value in [
            ("C", flow),
            ("B", rpm),
            ("H", humidity)
        ]:
            if value is not None:
                self.send(key, value)
                time.sleep(0.25)

    def mark(self):
        with self.lock:
            return len(self.events)

    def since(self, idx):
        with self.lock:
            return list(self.events[idx:])

    def wait_for(self, text, timeout, idx):
        deadline = time.monotonic() + timeout

        while time.monotonic() <= deadline:
            events = self.since(idx)

            for event in events:
                if text in event.line:
                    return event

                if "FALLO_SERIAL:" in event.line:
                    raise TestFailure("Se perdió la conexión serial")

            time.sleep(0.05)

        return None

    def wait_rpm_error(self, timeout, idx):
        deadline = time.monotonic() + timeout

        while time.monotonic() <= deadline:
            for event in self.since(idx):
                if any(e in event.line for e in RPM_ERRORS):
                    return event
            time.sleep(0.05)

        return None

    def no_event(self, texts, duration, idx, reason):
        deadline = time.monotonic() + duration

        while time.monotonic() <= deadline:
            for event in self.since(idx):
                if any(t in event.line for t in texts):
                    raise TestFailure(
                        f"{reason}. Mensaje: {event.line}"
                    )
            time.sleep(0.05)

    def check(self, condition, message):
        if not condition:
            raise TestFailure(message)

    def record(self, title, fn):
        print(f"\n===== {title} =====", flush=True)

        try:
            fn()
            self.passes.append(title)
            print(f"APROBADO: {title}", flush=True)

        except TestFailure as exc:
            self.failures.append(f"{title}: {exc}")
            print(
                "ATENCION, ERROR DE FUNCIONAMIENTO"
                f"\nPrueba: {title}\nMotivo: {exc}",
                flush=True
            )

    def prepare(self):
        # Esperar a que la tarea de humedad procese H:95.
        self.sensor_values(humidity=95, flow=1, rpm=800)

        idx = self.mark()

        self.no_event(
            [ON],
            34,
            idx,
            "La bomba se encendió con humedad >90%"
        )

        with self.lock:
            active = self.pump_on

        self.check(
            not active,
            "La bomba sigue encendida al terminar preparación"
        )

    def start_dry(self, rpm=800):
        self.sensor_values(
            humidity=20, flow=1, rpm=rpm
        )

        idx = self.mark()
        event = self.wait_for(ON, 35, idx)

        self.check(
            event is not None,
            "Con humedad <30% no encendió dentro de 35 s"
        )

        return event

    def humidity_limits(self):
        self.prepare()

        # Humedad exactamente 30%
        self.sensor_values(
            humidity=30, flow=1, rpm=800
        )
        idx = self.mark()

        self.no_event(
            [ON], 33, idx,
            "Arrancó con humedad exactamente 30%"
        )

        # Humedad exactamente 90%
        self.sensor_values(humidity=90)
        idx = self.mark()

        self.no_event(
            [ON], 33, idx,
            "Arrancó con humedad exactamente 90%"
        )

    def normal_cycle(self):
        self.prepare()

        on = self.start_dry()
        idx = self.mark()

        off = self.wait_for(OFF, 12.5, idx)

        self.check(
            off is not None,
            "No apagó la bomba después de 10 s"
        )

        elapsed = off.when - on.when

        self.check(
            9 <= elapsed <= 12.5,
            f"Duración incorrecta: {elapsed:.1f} s"
        )

        self.no_event(
            [FLOW_ERROR, *RPM_ERRORS],
            0.4,
            idx,
            "Alarma inesperada con sensores normales"
        )

        # Segundo ciclo periódico
        again_idx = self.mark()

        next_on = self.wait_for(
            ON, 23, again_idx
        )

        self.check(
            next_on is not None,
            "No repitió el riego tras aproximadamente 30 s"
        )

        interval = next_on.when - on.when

        self.check(
            29 <= interval <= 33,
            f"Intervalo de riego incorrecto: {interval:.1f} s"
        )

        next_off = self.wait_for(
            OFF, 12.5, self.mark()
        )

        self.check(
            next_off is not None,
            "El segundo ciclo no se apagó"
        )

    def no_flow(self):
        self.prepare()
        on = self.start_dry()

        idx = self.mark()
        self.send("C", 0)

        off = self.wait_for(OFF, 9.5, idx)

        self.check(
            off is not None,
            "No detuvo la bomba por falta de caudal"
        )

        elapsed = off.when - on.when

        self.check(
            elapsed >= 4,
            f"Se detuvo demasiado pronto: {elapsed:.1f} s"
        )

        self.check(
            elapsed < 9.5,
            f"Apagado demasiado tardío: {elapsed:.1f} s"
        )

        err = self.wait_for(
            FLOW_ERROR, 1.5, idx
        )

        self.check(
            err is not None,
            "No imprimió el error de falta de caudal"
        )

        self.sensor_values(flow=1)

    def transient_flow(self):
        self.prepare()
        self.start_dry()

        idx = self.mark()

        # Interrupción corta de caudal
        self.send("C", 0)
        time.sleep(2.2)
        self.send("C", 1)

        off = self.wait_for(OFF, 12, idx)

        self.check(
            off is not None,
            "No terminó el ciclo normal"
        )

        self.no_event(
            [FLOW_ERROR],
            0.2,
            idx,
            "Se disparó alarma con corte menor a 5 s"
        )

    def rpm_low(self):
        self.prepare()
        on = self.start_dry(rpm=450)

        idx = self.mark()

        err = self.wait_rpm_error(4, idx)

        self.check(
            err is not None,
            "RPM <600 sin mensaje de error"
        )

        elapsed = err.when - on.when

        self.check(
            0.5 <= elapsed <= 4,
            f"Alarma RPM fuera de tiempo: {elapsed:.1f} s"
        )

        off = self.wait_for(OFF, 12.5, idx)

        self.check(
            off is not None,
            "No terminó el ciclo"
        )

        self.sensor_values(rpm=800)

    def rpm_boundary(self):
        self.prepare()
        self.start_dry(rpm=600)

        idx = self.mark()
        off = self.wait_for(OFF, 12.5, idx)

        self.check(
            off is not None,
            "No terminó el ciclo con RPM=600"
        )

        self.no_event(
            RPM_ERRORS,
            0.2,
            idx,
            "RPM=600 se trató como falla"
        )

        self.sensor_values(rpm=800)

    def close(self):
        try:
            self.sensor_values(
                humidity=95, flow=1, rpm=800
            )
        except Exception:
            pass

        self.stop.set()
        self.reader.join(timeout=1)
        self.ser.close()

    def summary(self):
        print("\n" + "=" * 55)
        print("RESUMEN DE PRUEBAS")
        print("=" * 55)

        print(f"Aprobadas: {len(self.passes)}")
        print(f"Fallidas:  {len(self.failures)}")

        if self.failures:
            print("\nATENCION, ERROR DE FUNCIONAMIENTO")

            for failure in self.failures:
                print(f" - {failure}")
        else:
            print("\nTodas las pruebas observables aprobaron.")

        print("\nCOMPROBACIONES PENDIENTES:")
        print(" - Duración alarma caudal: 15 s")
        print(" - Duración alarma RPM: 30 s")
        print(" - Muestreo real de caudal cada 1 s")
        print("Requieren instrumentación adicional en ESP32.")

        return 1 if self.failures else 0


def main():
    parser = argparse.ArgumentParser(
        description="Pruebas ESP32 riego con FreeRTOS"
    )

    parser.add_argument(
        "puerto",
        help="Ejemplo: /dev/ttyUSB0"
    )

    parser.add_argument(
        "--baud",
        type=int,
        default=115200
    )

    parser.add_argument(
        "--test",
        choices=[
            "todos",
            "humedad",
            "normal",
            "caudal",
            "intermitente",
            "rpm-baja",
            "rpm-600"
        ],
        default="todos"
    )

    args = parser.parse_args()

    try:
        tester = Tester(args.puerto, args.baud)
    except (serial.SerialException, OSError) as exc:
        print(f"No se pudo abrir el puerto: {exc}")
        print("Cerrá el monitor serie de Arduino.")
        return 2

    tests = [
        (
            "humedad",
            "Límites de humedad 30% y 90%",
            tester.humidity_limits
        ),
        (
            "normal",
            "Riego 10 s + periodicidad 30 s",
            tester.normal_cycle
        ),
        (
            "caudal",
            "Falta de caudal durante 5 s",
            tester.no_flow
        ),
        (
            "intermitente",
            "Corte de caudal menor a 5 s",
            tester.transient_flow
        ),
        (
            "rpm-baja",
            "Alarma RPM <600",
            tester.rpm_low
        ),
        (
            "rpm-600",
            "Límite RPM=600",
            tester.rpm_boundary
        )
    ]

    try:
        print("Iniciando pruebas automáticas...")

        for key, title, fn in tests:
            if args.test in ("todos", key):
                tester.record(title, fn)

    except KeyboardInterrupt:
        print("\nPruebas interrumpidas.")
        return 130

    finally:
        tester.close()

    return tester.summary()


if __name__ == "__main__":
    sys.exit(main())
