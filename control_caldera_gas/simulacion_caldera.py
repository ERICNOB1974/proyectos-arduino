
#!/usr/bin/env python3

import argparse
import random
import time
import serial


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("puerto")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--inicial", type=int, default=None)
    args = parser.parse_args()

    temperatura = float(args.inicial if args.inicial is not None else random.randint(50, 120))
    valvula_abierta = False

    try:
        with serial.Serial(args.puerto, args.baud, timeout=0) as esp:
            print(f"Conectado a {args.puerto}")
            print(f"Temperatura inicial: {temperatura:.0f} °C")

            time.sleep(2)
            esp.reset_input_buffer()

            ultimo_instante = time.monotonic()
            proximo_envio = ultimo_instante
            buffer = b""

            while True:
                ahora = time.monotonic()
                delta = ahora - ultimo_instante
                ultimo_instante = ahora

                temperatura += delta * (0.1 if valvula_abierta else -0.05)

                if esp.in_waiting:
                    buffer += esp.read(esp.in_waiting)

                    while b"\n" in buffer:
                        linea, buffer = buffer.split(b"\n", 1)
                        mensaje = linea.decode("utf-8", errors="replace").strip()

                        if mensaje:
                            print(f"ESP32 > {mensaje}")

                        if "Válvula Gas: ABIERTA" in mensaje:
                            valvula_abierta = True
                        elif "Válvula Gas: CERRADA" in mensaje:
                            valvula_abierta = False

                if ahora >= proximo_envio:
                    lectura = round(temperatura)
                    esp.write(f"T:{lectura}\n".encode("ascii"))

                    estado = "ABIERTA" if valvula_abierta else "CERRADA"
                    print(f"Python > T:{lectura} (Válvula: {estado})")

                    proximo_envio += 1
                    if proximo_envio <= ahora:
                        proximo_envio = ahora + 1

                time.sleep(0.02)

    except serial.SerialException as e:
        print(f"Error de conexión: {e}")
    except KeyboardInterrupt:
        print("\nSimulación finalizada")


if __name__ == "__main__":
    main()
