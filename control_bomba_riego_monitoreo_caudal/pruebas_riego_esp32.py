#!/usr/bin/env python3
import argparse
import sys
import threading
import time

import serial

ON = "Bomba: ENCENDIDA"
OFF = "Bomba: APAGADA"
ERROR_CAUDAL = "ERROR: Bomba detenida por falta de caudal"
ERROR_RPM = ("ERROR: Bomba sin rotación", "ERROR: Bomba sin rotacion")


class Pruebas:
    def __init__(self, puerto, baud):
        self.esp = serial.Serial(puerto, baud, timeout=0.1)
        self.eventos = []
        self.lock = threading.Lock()
        self.detener = threading.Event()
        self.errores = []
        self.aprobadas = 0
        self.bomba = None
        self.hilo = threading.Thread(target=self.leer, daemon=True)
        self.hilo.start()

    def leer(self):
        while not self.detener.is_set():
            try:
                linea = self.esp.readline().decode("utf-8", errors="replace").strip()
            except (serial.SerialException, OSError) as e:
                with self.lock:
                    self.eventos.append((time.monotonic(), f"ERROR_SERIAL: {e}"))
                return
            if linea:
                with self.lock:
                    self.eventos.append((time.monotonic(), linea))
                    if ON in linea:
                        self.bomba = True
                    elif OFF in linea:
                        self.bomba = False
                print(f"ESP32 > {linea}", flush=True)

    def enviar(self, **sensores):
        for clave, valor in (("C", sensores.get("caudal")),
                             ("B", sensores.get("rpm")),
                             ("H", sensores.get("humedad"))):
            if valor is not None:
                mensaje = f"{clave}:{valor}\n"
                self.esp.write(mensaje.encode())
                print(f"PYTHON > {mensaje.strip()}", flush=True)
                time.sleep(0.25)

    def marca(self):
        with self.lock:
            return len(self.eventos)

    def buscar(self, textos, desde):
        if isinstance(textos, str):
            textos = (textos,)
        with self.lock:
            nuevos = self.eventos[desde:]
        for instante, linea in nuevos:
            if linea.startswith("ERROR_SERIAL:"):
                raise RuntimeError(linea)
            if any(texto in linea for texto in textos):
                return instante
        return None

    def esperar(self, textos, segundos, desde, debe_aparecer=True):
        limite = time.monotonic() + segundos
        while time.monotonic() < limite:
            instante = self.buscar(textos, desde)
            if instante is not None:
                if not debe_aparecer:
                    raise AssertionError(f"Evento inesperado: {textos}")
                return instante
            time.sleep(0.05)
        if debe_aparecer:
            raise AssertionError(f"No se recibió: {textos}")

    def verificar(self, condicion, mensaje):
        if not condicion:
            raise AssertionError(mensaje)

    def preparar(self):
        # Dejar los sensores normales antes de cada prueba
        self.enviar(humedad=95, caudal=1, rpm=800)
        m = self.marca()
        self.esperar(ON, 34, m, debe_aparecer=False)
        with self.lock:
            self.verificar(self.bomba is not True, "La bomba sigue encendida")

    def arrancar(self, rpm=800):
        m = self.marca()
        self.enviar(humedad=20, caudal=1, rpm=rpm)
        return self.esperar(ON, 35, m)

    def humedad(self):
        self.preparar()
        for valor in (30, 90):
            self.enviar(humedad=valor)
            m = self.marca()
            self.esperar(ON, 33, m, debe_aparecer=False)

    def normal(self):
        self.preparar()
        m = self.marca()
        inicio = self.arrancar()
        apagado = self.esperar(OFF, 12.5, m)
        self.verificar(9 <= apagado - inicio <= 12.5, "Duración de riego incorrecta")
        self.verificar(self.buscar((ERROR_CAUDAL, *ERROR_RPM), m) is None,
                       "Alarma con sensores normales")
        m2 = self.marca()
        siguiente = self.esperar(ON, 23, m2)
        self.verificar(29 <= siguiente - inicio <= 33, "Periodicidad incorrecta")
        self.esperar(OFF, 12.5, m2)

    def caudal(self):
        self.preparar()
        inicio = self.arrancar()
        m = self.marca()
        self.enviar(caudal=0)
        apagado = self.esperar(OFF, 9.5, m)
        self.verificar(4 <= apagado - inicio < 9.5, "Apagado fuera de tiempo")
        self.esperar(ERROR_CAUDAL, 1.5, m)
        self.enviar(caudal=1)

    def intermitente(self):
        self.preparar()
        self.arrancar()
        m = self.marca()
        self.enviar(caudal=0)
        time.sleep(2.2)
        self.enviar(caudal=1)
        self.esperar(OFF, 12, m)
        self.verificar(self.buscar(ERROR_CAUDAL, m) is None,
                       "Alarma con corte de caudal menor a 5 s")

    def rpm_baja(self):
        self.preparar()
        m = self.marca()
        inicio = self.arrancar(rpm=450)
        alarma = self.esperar(ERROR_RPM, 4, m)
        self.verificar(0.5 <= alarma - inicio <= 4, "Alarma RPM fuera de tiempo")
        self.esperar(OFF, 12.5, m)
        self.enviar(rpm=800)

    def rpm_600(self):
        self.preparar()
        m = self.marca()
        self.arrancar(rpm=600)
        self.esperar(OFF, 12.5, m)
        self.verificar(self.buscar(ERROR_RPM, m) is None, "Alarma con RPM=600")
        self.enviar(rpm=800)

    def ejecutar(self, nombre, funcion):
        print(f"\n===== {nombre} =====")
        try:
            funcion()
            self.aprobadas += 1
            print(f"APROBADO: {nombre}")
        except (AssertionError, RuntimeError, serial.SerialException, OSError) as e:
            self.errores.append(f"{nombre}: {e}")
            print(f"ATENCION, ERROR DE FUNCIONAMIENTO: {e}")

    def cerrar(self):
        try:
            self.enviar(humedad=95, caudal=1, rpm=800)
        except (serial.SerialException, OSError):
            pass
        self.detener.set()
        self.hilo.join(timeout=1)
        self.esp.close()


def main():
    p = argparse.ArgumentParser(description="Pruebas de riego ESP32")
    p.add_argument("puerto")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--test", default="todos", choices=[
        "todos", "humedad", "normal", "caudal", "intermitente", "rpm-baja", "rpm-600"
    ])
    args = p.parse_args()

    try:
        pruebas = Pruebas(args.puerto, args.baud)
    except (serial.SerialException, OSError) as e:
        sys.exit(f"No se pudo abrir el puerto: {e}")

    opciones = [
        ("humedad", pruebas.humedad),
        ("normal", pruebas.normal),
        ("caudal", pruebas.caudal),
        ("intermitente", pruebas.intermitente),
        ("rpm-baja", pruebas.rpm_baja),
        ("rpm-600", pruebas.rpm_600),
    ]
    try:
        for nombre, funcion in opciones:
            if args.test in ("todos", nombre):
                pruebas.ejecutar(nombre, funcion)
    except KeyboardInterrupt:
        print("\nPruebas interrumpidas")
        return 130
    finally:
        pruebas.cerrar()

    print(f"\nRESUMEN: {pruebas.aprobadas} aprobadas, {len(pruebas.errores)} fallidas")
    for fallo in pruebas.errores:
        print(f"- {fallo}")
    return 1 if pruebas.errores else 0


if __name__ == "__main__":
    sys.exit(main())
