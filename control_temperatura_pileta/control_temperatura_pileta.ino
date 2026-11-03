
#include <Arduino.h>
#include <stdlib.h>
#include <string.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>

#if CONFIG_FREERTOS_UNICORE
#define TASK_RUNNING_CORE 0
#else
#define TASK_RUNNING_CORE 1
#endif

// Constantes
#define TEMPERATURA_MINIMA_CALDERA 45   // °C
#define TEMPERATURA_MAXIMA_CALDERA 60   // °C
#define TEMPERATURA_MINIMA_PILETA 26    // °C
#define TEMPERATURA_MAXIMA_PILETA 30    // °C
#define TEMPERATURA_ENCENDIDO_BOMBA 55  // °C
#define TIEMPO_FUNCIONAMIENTO_BOMBA 30  // segundos

// Posiciones del buffer
enum Sensor {
  TEMPERATURA_PILETA = 0,
  TEMPERATURA_CALDERA = 1
};

typedef struct {
  uint32_t wcet;
  uint32_t periodo;
  UBaseType_t prioridad;
} TareaConfig;

// Buffer compartido: temperatura_pileta, temperatura_caldera
int bufferSensores[2] = { 30, 50 };

// Mutex para proteger el buffer
SemaphoreHandle_t mutexBuffer;

// Variables globales
int temperatura_caldera = 50;
int temperatura_pileta = 30;

bool bombaEncendida = false;
bool valvulaGasAbierta = false;
bool necesitaCalefaccion = false;

unsigned long momentoEncendido = 0;
unsigned long momentoAlarmaRotacion = 0;
unsigned long ultimoInicioRiego = 0;

// Configuracion de tareas
static const TareaConfig leerSerial = { 0, 1000, configMAX_PRIORITIES - 1 };

static const TareaConfig evaluarCaldera = { 0, 1000, configMAX_PRIORITIES - 2 };

static const TareaConfig evaluarBomba = { 0, 1000, configMAX_PRIORITIES - 3 };

static const TareaConfig leerSensorTemperaturaPileta = { 0, 6000, configMAX_PRIORITIES - 4 };

static const TareaConfig leerSensorTemperaturaCaldera = { 0, 15000, configMAX_PRIORITIES - 5 };

// Declaraciones
void LeerSerial(void *pvParameters);
void LeerSensorTemperaturaPileta(void *pvParameters);
void LeerSensorTemperaturaCaldera(void *pvParameters);
void EvaluarBomba(void *pvParameters);
void EvaluarCaldera(void *pvParameters);

int obtenerSensor(Sensor sensor) {
  int valor = 0;

  if (xSemaphoreTake(mutexBuffer, portMAX_DELAY) == pdTRUE) {
    valor = bufferSensores[sensor];
    xSemaphoreGive(mutexBuffer);
  }

  return valor;
}

void guardarSensor(Sensor sensor, int valor) {
  if (xSemaphoreTake(mutexBuffer, portMAX_DELAY) == pdTRUE) {
    bufferSensores[sensor] = valor;
    xSemaphoreGive(mutexBuffer);
  }
}

void setup() {
  Serial.begin(115200);

  mutexBuffer = xSemaphoreCreateMutex();

  if (mutexBuffer == NULL) {
    Serial.println("Error al crear mutex");
    while (true) {
      delay(1000);
    }
  }

  // Permite iniciar el primer riego inmediatamente
  // cuando se detecte falta de humedad.
  void LeerSerial(void *pvParameters);
  void LeerSensorTemperaturaPileta(void *pvParameters);
  void LeerSensorTemperaturaCaldera(void *pvParameters);
  void EvaluarBomba(void *pvParameters);
  void EvaluarCaldera(void *pvParameters);

  xTaskCreatePinnedToCore(
    LeerSerial,
    "Leer serial",
    4096,
    (void *)&leerSerial,
    leerSerial.prioridad,
    NULL,
    TASK_RUNNING_CORE);

  xTaskCreatePinnedToCore(
    LeerSensorTemperaturaPileta,
    "Leer sensor temperatura pileta",
    4096,
    (void *)&leerSensorTemperaturaPileta,
    leerSensorTemperaturaPileta.prioridad,
    NULL,
    TASK_RUNNING_CORE);


  xTaskCreatePinnedToCore(
    LeerSensorTemperaturaCaldera,
    "Leer sensor temperatura caldera",
    4096,
    (void *)&leerSensorTemperaturaCaldera,
    leerSensorTemperaturaCaldera.prioridad,
    NULL,
    TASK_RUNNING_CORE);

  xTaskCreatePinnedToCore(
    EvaluarBomba,
    "Evaluar bomba",
    4096,
    (void *)&evaluarBomba,
    evaluarBomba.prioridad,
    NULL,
    TASK_RUNNING_CORE);

  xTaskCreatePinnedToCore(
    EvaluarCaldera,
    "Evaluar caldera",
    4096,
    (void *)&evaluarCaldera,
    evaluarCaldera.prioridad,
    NULL,
    TASK_RUNNING_CORE);
}

void loop() {
  vTaskDelay(pdMS_TO_TICKS(1000));
}

/*
 * Recibe mensajes:
 * P:25  -> temperatura pileta
 * C:1   -> temperatura caldera
 *
 * Es la unica tarea que lee Serial.
 */
void LeerSerial(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = xTaskGetTickCount();

  char linea[32];
  size_t posicion = 0;
  bool descartando = false;

  for (;;) {

    while (Serial.available() > 0) {
      char caracter = Serial.read();

      if (caracter == '\n') {
        if (!descartando && posicion > 0) {
          linea[posicion] = '\0';

          // Eliminar retorno de carro si existe
          if (posicion > 0 && linea[posicion - 1] == '\r') {
            linea[posicion - 1] = '\0';
          }

          if (strlen(linea) >= 3 && linea[1] == ':') {
            char *fin;
            long valor = strtol(linea + 2, &fin, 10);

            // Comprobar que el numero sea valido
            if (fin != linea + 2 && *fin == '\0') {

              switch (linea[0]) {
                case 'P':
                  if (valor >= 0 && valor <= 100) {
                    guardarSensor(TEMPERATURA_PILETA, (int)valor);
                    Serial.printf("Buffer temperatura pileta: %ld\n", valor);
                  }
                  break;

                case 'C':
                  if (valor >= 0 && valor <= 100) {
                    guardarSensor(TEMPERATURA_CALDERA, (int)valor);
                    Serial.printf("Buffer temperatura caldera: %ld\n", valor);
                  }
                  break;
              }
            }
          }
        }

        posicion = 0;
        descartando = false;

      } else if (!descartando) {
        if (posicion < sizeof(linea) - 1) {
          linea[posicion++] = caracter;
        } else {
          posicion = 0;
          descartando = true;
        }
      }
    }

    vTaskDelayUntil(&inicio, pdMS_TO_TICKS(config->periodo));
  }
}

/*
 * Lee el valor del caudal desde el buffer cada 1 s.
 */
void LeerSensorTemperaturaCaldera(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = xTaskGetTickCount();

  for (;;) {

    int valor = obtenerSensor(TEMPERATURA_CALDERA);

    temperatura_caldera = valor;

    vTaskDelayUntil(&inicio, pdMS_TO_TICKS(config->periodo));
  }
}

/*
 * Lee el valor del caudal desde el buffer cada 1 s.
 */
void LeerSensorTemperaturaPileta(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = xTaskGetTickCount();

  for (;;) {

    int valor = obtenerSensor(TEMPERATURA_PILETA);

    temperatura_pileta = valor;

    vTaskDelayUntil(&inicio, pdMS_TO_TICKS(config->periodo));
  }
}

void EvaluarBomba(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = xTaskGetTickCount();

  for (;;) {
    unsigned long ahora = millis();
    // Control ON/OFF de la pileta
    if (temperatura_pileta <= TEMPERATURA_MINIMA_PILETA) {
      necesitaCalefaccion = true;
    } else if (temperatura_pileta >= TEMPERATURA_MAXIMA_PILETA) {
      necesitaCalefaccion = false;
    }
    // la bomba de circulacion se prende a partir de los 55 ºC y se apaga por debajo de 45 ºC

    if (!bombaEncendida && necesitaCalefaccion && temperatura_caldera >= TEMPERATURA_ENCENDIDO_BOMBA && temperatura_caldera <= TEMPERATURA_MAXIMA_CALDERA) {

      bombaEncendida = true;
      momentoEncendido = ahora;

      Serial.println("Bomba circulación agua: ENCENDIDA");
    }


    // Apagado
    if (bombaEncendida && (temperatura_caldera < TEMPERATURA_MINIMA_CALDERA ||                                         //si la temperatura de la caldera baja de 45
                           temperatura_caldera > TEMPERATURA_MAXIMA_CALDERA ||                                         //si la temperatura de la caldera sube de 60
                           (!necesitaCalefaccion && ahora - momentoEncendido >= TIEMPO_FUNCIONAMIENTO_BOMBA * 1000UL)  //llegó a 30 grados y pasaron al menos 30 segundos
                           )) {

      bombaEncendida = false;
      Serial.println("Bomba circulación agua: APAGADA");
    }


    // si la temperatura de la pileta es menor a 26 y la de la caldera mayor a 45, prender la bomba de circulacion
    // idem pero la caldera menor a 45, prender caldera, esperar que llegue a 45 y prender bomba de circulacion


    vTaskDelayUntil(&inicio, pdMS_TO_TICKS(config->periodo));
  }
}

void EvaluarCaldera(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = xTaskGetTickCount();

  for (;;) {

    if ((temperatura_caldera <= TEMPERATURA_MINIMA_CALDERA) && !valvulaGasAbierta) {
      valvulaGasAbierta = true;
      Serial.println("Válvula gas: ABIERTA");
    } else if ((temperatura_caldera >= TEMPERATURA_MAXIMA_CALDERA) && valvulaGasAbierta) {
      valvulaGasAbierta = false;
      Serial.println("Válvula gas: CERRADA");
    }

    vTaskDelayUntil(&inicio, pdMS_TO_TICKS(config->periodo));
  }
}