#!/usr/bin/env python3
"""Simulador de sensor de caldera por puerto serie para ESP32.

Requiere: python-pyserial (Arch Linux) o pip install pyserial.
Ejemplo: python simulador_caldera.py /dev/ttyUSB0
"""

import argparse
import random
import time

import serial


def main():
    parser = argparse.ArgumentParser(description="Envía temperaturas T:XX a una ESP32.")
    parser.add_argument("puerto", help="Ej.: /dev/ttyUSB0 o /dev/ttyACM0")
    parser.add_argument(
        "--baud", type=int, default=115200, help="Velocidad serie (115200 por defecto)"
    )
    parser.add_argument(
        "--inicial",
        type=int,
        default=None,
        help="Temperatura inicial fija (por defecto aleatoria 50–120)",
    )
    args = parser.parse_args()

    temperatura = float(
        args.inicial if args.inicial is not None else random.randint(50, 120)
    )
    valvula_abierta = False  # Estado seguro asumido hasta recibir confirmación de ESP32

    try:
        with serial.Serial(args.puerto, args.baud, timeout=0) as esp:
            print(f"Conectado a {args.puerto} ({args.baud} baudios)")
            print(f"Temperatura inicial: {temperatura:.0f} °C")
            print(
                "Ctrl+C para detener. No abras simultáneamente el Monitor Serie de Arduino.\n"
            )

            # Al abrir el puerto, muchas placas ESP32 se reinician.
            time.sleep(2)
            esp.reset_input_buffer()
            ultimo_instante = time.monotonic()
            proximo_envio = ultimo_instante
            buffer_entrada = b""

            while True:
                ahora = time.monotonic()
                delta = ahora - ultimo_instante
                ultimo_instante = ahora

                # Evolución continua: +1 °C/10s abierta; -1 °C/20s cerrada.
                temperatura += delta * (0.1 if valvula_abierta else -0.05)

                # Recepción no bloqueante de mensajes del ESP32.
                disponibles = esp.in_waiting
                if disponibles:
                    buffer_entrada += esp.read(disponibles)
                    while b"\n" in buffer_entrada:
                        linea, buffer_entrada = buffer_entrada.split(b"\n", 1)
                        mensaje = linea.decode("utf-8", errors="replace").strip()
                        if not mensaje:
                            continue
                        print(f"ESP32  > {mensaje}")
                        if "Válvula Gas: ABIERTA" in mensaje:
                            valvula_abierta = True
                        elif "Válvula Gas: CERRADA" in mensaje:
                            valvula_abierta = False

                if ahora >= proximo_envio:
                    lectura = round(temperatura)
                    mensaje = f"T:{lectura}\n"
                    esp.write(mensaje.encode("ascii"))
                    estado = "ABIERTA" if valvula_abierta else "CERRADA"
                    print(f"Python > T:{lectura}  (válvula conocida: {estado})")
                    proximo_envio += 1.0
                    if proximo_envio <= ahora:
                        proximo_envio = ahora + 1.0

                time.sleep(0.02)

    except serial.SerialException as exc:
        parser.exit(1, f"Error de conexión serie: {exc}\n")
    except KeyboardInterrupt:
        print("\nSimulación finalizada.")


if __name__ == "__main__":
    main()
