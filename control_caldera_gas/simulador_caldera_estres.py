
#!/usr/bin/env python3

import argparse
import random
import re
import time
import serial

PLAN = [
    ("inicio_frio", 20),
    ("umbral_70", 25),
    ("saltos_rapidos", 25),
    ("umbral_98", 30),
    ("sobretemperatura", 20),
    ("rebote_critico", 25),
    ("lecturas_invalidas", 20),
    ("sensor_congelado", 20),
    ("ruido_y_deriva", 35),
    ("recuperacion", 20),
]

def temperatura(modo, n, local, anterior, rnd):
    valores = {
        "umbral_70": [69, 70, 71, 70, 69, 68, 70, 72],
        "saltos_rapidos": [55, 115, 64, 105, 72, 120, 50, 99, 69, 98],
        "umbral_98": [96, 97, 98, 99, 100, 98, 97, 99, 95, 98],
        "rebote_critico": [99, 65, 101, 69, 98, 70, 120, 50],
    }

    if modo in valores:
        return f"T:{valores[modo][n % len(valores[modo])]}"

    if modo == "inicio_frio":
        return f"T:{max(50, 68 - n // 6)}"
    if modo == "sobretemperatura":
        return f"T:{120 if local < 9 else 110}"
    if modo == "sensor_congelado":
        return "T:75"
    if modo == "recuperacion":
        return f"T:{65 if local < 10 else 75}"
    if modo == "lecturas_invalidas":
        return rnd.choice(["T:", "T:abc", "T:-20", "T:999", "X:75", "T:98.5", "T:75junk", "T:65"])
    
    base = anterior if anterior is not None and 40 <= anterior <= 130 else 85
    return f"T:{max(40, min(125, round(base + rnd.gauss(0, 6))))}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("puerto")
    p.add_argument("--duracion", type=int, default=240)
    p.add_argument("--intervalo", type=float, default=1)
    p.add_argument("--semilla", type=int, default=42)
    args = p.parse_args()

    if args.duracion <= 0 or args.intervalo <= 0:
        p.error("Duración e intervalo deben ser positivos")

    rnd = random.Random(args.semilla)
    total = sum(d for _, d in PLAN)
    estado = None
    ultimo_valor = None
    ultimo_cierre = None
    inicio_encendido = None
    cierre_pendiente = None
    alertas = []
    n = 0

    def alerta(t, mensaje):
        alertas.append(mensaje)
        print(f"[ALERTA {t:.1f}s] {mensaje}")

    try:
        with serial.Serial(args.puerto, 115200, timeout=0.05) as esp:
            time.sleep(2)
            esp.reset_input_buffer()
            inicio = time.monotonic()

            while time.monotonic() - inicio < args.duracion:
                ahora = time.monotonic()
                t = ahora - inicio
                pos = t % total
                acumulado = 0

                for nombre, dur in PLAN:
                    acumulado += dur
                    if pos < acumulado:
                        local = pos - (acumulado - dur)
                        break

                if n == 0 or nombre != anterior_escenario:
                    print(f"\n--- {nombre} ---")
                anterior_escenario = nombre

                linea = temperatura(nombre, n, local, ultimo_valor, rnd)
                esp.write((linea + "\n").encode())
                print(f"{t:6.1f}s PY > {linea}")

                match_temp = re.fullmatch(r"T:(\d+)", linea)
                if match_temp:
                    ultimo_valor = int(match_temp.group(1))
                    if estado is True and ultimo_valor >= 98:
                        cierre_pendiente = cierre_pendiente or ahora
                    elif ultimo_valor < 98:
                        cierre_pendiente = None

                fin_lectura = time.monotonic() + args.intervalo

                while time.monotonic() < fin_lectura:
                    msg = esp.readline().decode(errors="replace").strip()
                    if msg:
                        print(f"ESP32 > {msg}")
                        match = re.search(r"V[aá]lvula Gas:\s*(ABIERTA|CERRADA)", msg, re.I)

                        if match:
                            nuevo = match.group(1).upper() == "ABIERTA"
                            instante = time.monotonic()

                            if nuevo and ultimo_cierre and instante - ultimo_cierre < 10:
                                alerta(t, "No respetó los 10 segundos de descanso")

                            if not nuevo:
                                if estado is True and inicio_encendido and instante - inicio_encendido < 10:
                                    if ultimo_valor is None or ultimo_valor < 98:
                                        alerta(t, "Encendido menor a 10 segundos")
                                ultimo_cierre = instante
                                cierre_pendiente = None
                            else:
                                inicio_encendido = instante
                                if ultimo_valor is not None and ultimo_valor >= 98:
                                    alerta(t, "Válvula abierta con temperatura crítica")

                            estado = nuevo

                    if cierre_pendiente and estado is True:
                        if time.monotonic() - cierre_pendiente > 1.5:
                            alerta(t, "No se informó cierre ante temperatura crítica")
                            cierre_pendiente = None

                n += 1

    except serial.SerialException as e:
        print(f"Error de puerto: {e}")
        return
    except KeyboardInterrupt:
        print("\nPrueba interrumpida")

    print("\n=== RESULTADOS ===")
    print(f"Muestras: {n}")
    print(f"Alertas: {len(alertas)}")
    for mensaje in alertas:
        print(f"- {mensaje}")


if __name__ == "__main__":
    main()
