#!/usr/bin/env python3
"""Pruebas de estrés de caldera ESP32 por Serial (sin hardware externo).

Instalación Arch: sudo pacman -S python-pyserial
Ejemplo: python stress_caldera.py /dev/ttyUSB0 --duracion 240 --semilla 42

ADVERTENCIA: es un inyector de entradas y un monitor de SALIDAS TEXTUALES.
No verifica la válvula eléctrica física ni demuestra un cierre 'inmediato' real.
"""
import argparse
import csv
import math
import random
import re
import sys
import time
from pathlib import Path

try:
    import serial
except ImportError:
    sys.exit("Falta pyserial. En Arch: sudo pacman -S python-pyserial")

ESTADOS = re.compile(r"V[aá]lvula\s+Gas\s*:\s*(ABIERTA|CERRADA)", re.IGNORECASE)

# (etiqueta, duración en s, modo)
PLAN = [
    ("inicio_frio", 20, "cold"),
    ("umbral_70", 25, "low_edge"),
    ("saltos_rapidos", 25, "jump"),
    ("umbral_98", 30, "high_edge"),
    ("sobretemperatura", 20, "overheat"),
    ("rebote_critico", 25, "bounce"),
    ("lecturas_invalidas", 20, "invalid"),
    ("sensor_congelado", 20, "stuck"),
    ("ruido_y_deriva", 35, "noise"),
    ("recuperacion", 20, "recovery"),
]


def lectura(modo, local, n, rnd, valvula, anterior):
    """Devuelve línea a inyectar y temperatura interpretable o None."""
    if modo == "cold":
        t = rnd.choice([50, 55, 60, 65, 68, 69]) if n == 0 else max(50, 68 - n // 6)
    elif modo == "low_edge":
        t = [69, 70, 71, 70, 69, 68, 70, 72][n % 8]
    elif modo == "jump":
        t = [55, 115, 64, 105, 72, 120, 50, 99, 69, 98][n % 10]
    elif modo == "high_edge":
        t = [96, 97, 98, 99, 100, 98, 97, 99, 95, 98][n % 10]
    elif modo == "overheat":
        t = 120 if local < 9 else 110
    elif modo == "bounce":
        t = [99, 65, 101, 69, 98, 70, 120, 50][n % 8]
    elif modo == "invalid":
        # Mensajes malformados para evaluar robustez del parser; no se considera
        # obligatoria ninguna política de cierre ante cada uno de ellos.
        bad = ["T:", "T:abc", "T:-20", "T:999", "X:75", "T:98.5", "T: 99", "T:75junk"]
        if n % 3 != 0:
            return bad[n % len(bad)], None
        t = [65, 98, 110][(n // 3) % 3]
    elif modo == "stuck":
        t = 75
    elif modo == "noise":
        base = (anterior if anterior is not None and 40 <= anterior <= 130 else 85)
        drift = 0.25 if valvula is True else -0.20 if valvula is False else 0
        t = min(125, max(40, int(round(base + drift + rnd.gauss(0, 6)))))
    elif modo == "recovery":
        t = 65 if local < 10 else 75
    else:
        t = rnd.randint(50, 120)
    return f"T:{t}", t


def main():
    p = argparse.ArgumentParser(description="Inyector de estrés y monitor serial para ESP32.")
    p.add_argument("puerto", help="Ejemplo: /dev/ttyUSB0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--duracion", type=float, default=240, help="Duración total, segundos (por defecto: 240)")
    p.add_argument("--intervalo", type=float, default=1.0, help="Intervalo entre temperaturas, segundos")
    p.add_argument("--semilla", type=int, default=42, help="Semilla aleatoria reproducible")
    p.add_argument("--csv", default="stress_caldera.csv", help="Archivo CSV para auditoría")
    p.add_argument("--limite-reaccion", type=float, default=1.5,
                   help="Máximo de segundos desde una muestra T>=98 hasta mensaje de cierre (orientativo)")
    p.add_argument("--min-descanso", type=float, default=10.0)
    p.add_argument("--min-encendido", type=float, default=10.0)
    p.add_argument("--sin-invalidos", action="store_true", help="Sustituye el escenario de entradas inválidas por ruido")
    args = p.parse_args()
    if args.duracion <= 0 or args.intervalo <= 0:
        p.error("duracion e intervalo deben ser positivos")
    rnd = random.Random(args.semilla)
    plan = [(name, length, "noise" if args.sin_invalidos and mode == "invalid" else mode)
            for name, length, mode in PLAN]
    total_plan = sum(x[1] for x in plan)
    eventos = []
    alertas = []
    estado = None  # Desconocido: no inventar estado inicial
    inicio_estado = None
    ultimo_cierre = None
    ultimo_envio = None
    ultimo_valor = None
    pendiente_peligro = None  # (instante, temperatura)
    hubo_respuesta = False
    n = 0

    def escribir(writer, t, tipo, detalle, temp="", st=""):
        writer.writerow([f"{t:.3f}", tipo, detalle, temp, st])

    def alerta(writer, t, mensaje):
        alertas.append((t, mensaje))
        print(f"  [ALERTA] {mensaje}")
        escribir(writer, t, "ALERTA", mensaje)

    try:
        with serial.Serial(args.puerto, args.baud, timeout=0, write_timeout=1) as esp, \
             open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["segundo", "tipo", "mensaje", "temperatura", "valvula_conocida"])
            print(f"Conectado: {args.puerto} a {args.baud} baudios | semilla={args.semilla}")
            print("IMPORTANTE: cerrá el Monitor Serie de Arduino IDE.")
            print("Al abrir el puerto, la ESP32 puede reiniciarse; esperando 2 s...")
            time.sleep(2)
            esp.reset_input_buffer()
            inicio = time.monotonic()
            siguiente = inicio
            buffer = b""
            previo_escenario = None
            while True:
                ahora = time.monotonic()
                t = ahora - inicio
                if t >= args.duracion:
                    break
                # Lectura de respuestas ESP32
                cuantos = esp.in_waiting
                if cuantos:
                    buffer += esp.read(cuantos)
                    if len(buffer) > 16384:
                        buffer = buffer[-4096:]
                        alerta(writer, t, "Buffer serial excesivo, posible mensaje sin salto de línea")
                    while b"\n" in buffer:
                        raw, buffer = buffer.split(b"\n", 1)
                        msg = raw.decode("utf-8", errors="replace").strip()
                        if not msg:
                            continue
                        print(f"{t:7.2f}s  ESP32 > {msg}")
                        escribir(writer, t, "RX", msg)
                        match = ESTADOS.search(msg)
                        if match:
                            hubo_respuesta = True
                            nuevo = match.group(1).upper() == "ABIERTA"
                            if estado is not None and nuevo == estado:
                                alerta(writer, t, "Notificación repetida sin cambio de estado")
                            if nuevo and ultimo_cierre is not None and (ahora - ultimo_cierre) < args.min_descanso - 0.15:
                                alerta(writer, t, f"Reencendido tras {ahora-ultimo_cierre:.2f}s: descanso menor que {args.min_descanso}s")
                            if not nuevo:
                                if estado is True and inicio_estado is not None and (ahora - inicio_estado) < args.min_encendido - 0.15:
                                    # Se permite apagado por seguridad si se reportó T>=98 mientras estaba abierta.
                                    peligro_reciente = any((ts >= inicio_estado and ts <= ahora and temp >= 98) for ts, temp in eventos)
                                    if not peligro_reciente:
                                        alerta(writer, t, f"Cierre normal tras {ahora-inicio_estado:.2f}s: encendido menor que {args.min_encendido}s")
                                ultimo_cierre = ahora
                                pendiente_peligro = None
                            else:
                                if ultimo_valor is not None and ultimo_valor >= 98:
                                    alerta(writer, t, f"Válvula ABIERTA con último sensor T:{ultimo_valor} (revisar carrera entre tareas)")
                            estado = nuevo
                            inicio_estado = ahora
                if pendiente_peligro and estado is True and ahora - pendiente_peligro[0] > args.limite_reaccion:
                    alerta(writer, t, f"Sin cierre reportado {args.limite_reaccion}s después de T:{pendiente_peligro[1]} (puede depender del muestreo y telemetría)")
                    pendiente_peligro = None  # una alerta por inyección crítica

                if ahora >= siguiente:
                    posicion = t % total_plan
                    acumulado = 0
                    for nombre, dur, modo in plan:
                        if posicion < acumulado + dur:
                            local = posicion - acumulado
                            break
                        acumulado += dur
                    else:
                        nombre, modo, local = plan[-1][0], plan[-1][2], 0
                    if nombre != previo_escenario:
                        print(f"\n--- ESCENARIO: {nombre} ---")
                        escribir(writer, t, "ESCENARIO", nombre)
                        previo_escenario = nombre
                    linea, temp = lectura(modo, local, n, rnd, estado, ultimo_valor)
                    esp.write((linea + "\n").encode("ascii"))
                    print(f"{t:7.2f}s  PY    > {linea}  [válvula={('ABIERTA' if estado else 'CERRADA') if estado is not None else 'DESCONOCIDA'}]")
                    escribir(writer, t, "TX", linea, temp if temp is not None else "", estado)
                    if temp is not None:
                        ultimo_valor = temp
                        eventos.append((ahora, temp))
                        # Guardar sólo ventana relevante para comprobar excepción de emergencia.
                        eventos[:] = [(ts, x) for ts, x in eventos if ahora-ts <= args.min_encendido + 5]
                        if estado is True and temp >= 98 and pendiente_peligro is None:
                            pendiente_peligro = (ahora, temp)
                        elif temp < 98:
                            pendiente_peligro = None
                    ultimo_envio = ahora
                    n += 1
                    siguiente += args.intervalo
                    if siguiente < ahora:
                        siguiente = ahora + args.intervalo
                time.sleep(0.015)

            # Breve lectura de mensajes finales, sin enviar más muestras.
            fin = time.monotonic() + 0.3
            while time.monotonic() < fin:
                if esp.in_waiting:
                    for raw in esp.read(esp.in_waiting).decode("utf-8", errors="replace").splitlines():
                        if raw.strip():
                            print("ESP32 >", raw.strip())
                time.sleep(0.03)
    except (serial.SerialException, OSError) as e:
        sys.exit(f"Error de puerto/archivo: {e}\nCerrá Arduino IDE/Monitor Serie y verificá permisos sobre {args.puerto}.")
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
    print("\n=== RESUMEN ===")
    print(f"Muestras enviadas: {n} | alertas: {len(alertas)} | cambios recibidos: {'sí' if hubo_respuesta else 'ninguno'}")
    print(f"Log CSV: {Path(args.csv).resolve()}")
    print("Las alertas son INDICIOS, no pruebas concluyentes: la ESP32 solo informa cambios y no hay confirmación física ni timestamps internos.")
    if not hubo_respuesta:
        print("No se recibieron estados reconocibles. Verificá que el firmware imprima 'Válvula Gas: ABIERTA/CERRADA'.")
    if alertas:
        print("Revisá el CSV para correlacionar muestras y respuestas.")


if __name__ == "__main__":
    main()
