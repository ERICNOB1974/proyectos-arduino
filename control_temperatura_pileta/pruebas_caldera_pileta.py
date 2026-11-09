#!/usr/bin/env python3
import argparse
import re
import sys
import threading
import time
import unicodedata
import serial


def tipo(linea):
    texto = ''.join(c for c in unicodedata.normalize('NFKD', linea)
                    if not unicodedata.combining(c)).lower()
    for palabra, estados in [('bomba', ('encendida', 'apagada')),
                             ('valvula', ('abierta', 'cerrada'))]:
        if palabra in texto:
            for i, estado in enumerate(estados):
                if estado in texto:
                    return ('BOMBA' if palabra == 'bomba' else 'GAS') + ('_ON' if i == 0 else '_OFF')
    for sensor, nombre in [('P', 'pileta'), ('C', 'caldera')]:
        if f'buffer temperatura {nombre}:' in texto:
            return 'BUFFER_' + sensor
    return None


class Equipo:
    def __init__(self, puerto, baud):
        self.serial = serial.Serial(puerto, baud, timeout=0.15, write_timeout=2)
        self.eventos = []
        self.lock = threading.Lock()
        self.fin = threading.Event()
        self.error = None
        self.inicio = time.monotonic()
        self.hilo = threading.Thread(target=self.leer, daemon=True)
        self.hilo.start()

    def leer(self):
        while not self.fin.is_set():
            try:
                linea = self.serial.readline().decode('utf-8', errors='replace').strip()
            except (serial.SerialException, OSError) as e:
                self.error = str(e)
                return
            if linea:
                instante = time.monotonic()
                with self.lock:
                    self.eventos.append((instante, tipo(linea)))
                print(f'{instante - self.inicio:6.1f}s ESP32 > {linea}', flush=True)

    def enviar(self, p=None, c=None):
        for sensor, valor in [('P', p), ('C', c)]:
            if valor is not None:
                if not 0 <= valor <= 100:
                    raise ValueError('Temperatura fuera de rango')
                mensaje = f'{sensor}:{valor}\n'
                self.serial.write(mensaje.encode())
                print('PC >', mensaje.strip(), flush=True)
                time.sleep(0.12)

    def marca(self):
        with self.lock:
            return len(self.eventos)

    def buscar(self, evento, segundos, marca, presente=True):
        limite = time.monotonic() + segundos
        while time.monotonic() < limite:
            if self.error:
                raise RuntimeError(self.error)
            with self.lock:
                encontrados = [t for t, nombre in self.eventos[marca:] if nombre == evento]
            if encontrados:
                if not presente:
                    raise RuntimeError(f'Evento inesperado: {evento}')
                return encontrados[0]
            time.sleep(0.08)
        if presente:
            raise RuntimeError(f'No se detectó {evento} en {segundos:.1f}s')

    def preparar(self):
        self.enviar(p=30, c=40)
        time.sleep(18)
        self.enviar(c=50, p=30)
        time.sleep(19)

    def cerrar(self):
        self.fin.set()
        self.hilo.join(timeout=1)
        self.serial.close()


def pruebas(d, extra):
    def esperar(evento, segundos, marca):
        return d.buscar(evento, segundos + extra, marca)

    def no(evento, segundos, marca):
        d.buscar(evento, segundos, marca, False)

    def serial_test():
        m = d.marca()
        d.enviar(p=27, c=53)
        esperar('BUFFER_P', 4, m)
        esperar('BUFFER_C', 4, m)

    def gas():
        d.enviar(p=30, c=44)
        m = d.marca()
        esperar('GAS_ON', 19, m)
        m = d.marca()
        d.enviar(c=52)
        no('GAS_OFF', 18, m)
        m = d.marca()
        d.enviar(c=60)
        esperar('GAS_OFF', 19, m)
        m = d.marca()
        d.enviar(c=50)
        no('GAS_ON', 18, m)
        m = d.marca()
        d.enviar(c=45)
        esperar('GAS_ON', 19, m)

    def bomba_55():
        d.enviar(p=25, c=44)
        time.sleep(19)
        m = d.marca()
        d.enviar(c=54)
        no('BOMBA_ON', 19, m)
        m = d.marca()
        d.enviar(c=55)
        esperar('BOMBA_ON', 19, m)

    def bomba_45():
        d.enviar(p=25, c=44)
        time.sleep(19)
        m = d.marca()
        d.enviar(c=56)
        esperar('BOMBA_ON', 19, m)
        m = d.marca()
        d.enviar(c=44)
        esperar('BOMBA_OFF', 19, m)
        no('BOMBA_OFF', 3.1, d.marca())

    def bomba_60():
        d.enviar(p=25, c=44)
        time.sleep(19)
        m = d.marca()
        d.enviar(c=56)
        esperar('BOMBA_ON', 19, m)
        m = d.marca()
        d.enviar(c=61)
        esperar('BOMBA_OFF', 19, m)

    def pileta():
        d.enviar(p=30, c=44)
        time.sleep(19)
        m = d.marca()
        d.enviar(c=56)
        no('BOMBA_ON', 19, m)
        m = d.marca()
        d.enviar(p=27)
        no('BOMBA_ON', 8, m)
        m = d.marca()
        d.enviar(p=26)
        esperar('BOMBA_ON', 10, m)

    def tiempo_30():
        d.enviar(p=25, c=44)
        time.sleep(19)
        m = d.marca()
        d.enviar(c=56)
        inicio = esperar('BOMBA_ON', 19, m)
        m = d.marca()
        d.enviar(p=30)
        no('BOMBA_OFF', max(0, 30 - (time.monotonic() - inicio) - 1.5), m)
        fin = esperar('BOMBA_OFF', 13, m)
        if not 28.5 <= fin - inicio <= 40 + extra:
            raise RuntimeError(f'Duración de bomba: {fin - inicio:.1f}s')
        no('BOMBA_ON', 7, d.marca())

    return {'serial': serial_test, 'gas': gas, 'bomba-55': bomba_55,
            'bomba-45': bomba_45, 'bomba-60': bomba_60,
            'pileta': pileta, 'tiempo-30': tiempo_30}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('puerto')
    p.add_argument('--baud', type=int, default=115200)
    p.add_argument('--test', default='todos', choices=[
        'todos', 'serial', 'gas', 'bomba-55', 'bomba-45',
        'bomba-60', 'pileta', 'tiempo-30'])
    p.add_argument('--tolerancia', type=float, default=3)
    args = p.parse_args()

    try:
        d = Equipo(args.puerto, args.baud)
    except (serial.SerialException, OSError) as e:
        sys.exit(f'Error de conexión: {e}')

    resultados = []
    try:
        for nombre, prueba in pruebas(d, args.tolerancia).items():
            if args.test != 'todos' and nombre != args.test:
                continue
            print(f'\n--- PRUEBA: {nombre} ---')
            try:
                prueba()
                print(f'APROBADO: {nombre}')
                resultados.append((nombre, True))
            except (RuntimeError, serial.SerialException, OSError, ValueError) as e:
                print(f'ERROR: {nombre}: {e}')
                resultados.append((nombre, False))
    except KeyboardInterrupt:
        print('\nPruebas interrumpidas')
        return 130
    finally:
        d.cerrar()

    print(f'\nRESULTADOS: {sum(ok for _, ok in resultados)} aprobadas, '
          f'{sum(not ok for _, ok in resultados)} fallidas')
    return int(any(not ok for _, ok in resultados))


if __name__ == '__main__':
    sys.exit(main())
