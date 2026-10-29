#include <regex>

#if CONFIG_FREERTOS_UNICORE
#define TASK_RUNNING_CORE 0
#else
#define TASK_RUNNING_CORE 1
#endif

// Constantes
#define TEMPERATURA_MINIMA_CALDERA 70       // °C
#define TEMPERATURA_MAXIMA_CALDERA 98       // °C
#define TIEMPO_MINIMO_ENCENDIDO_CALDERA 10  // segundos
#define TIEMPO_DESCANSO_CALDERA 10          // segundos
#define TEMPERATURA_TASA_ENFRIAMIENTO 1     // °C
#define TIEMPO_TASA_ENFRIAMIENTO 20         // segundos
#define TEMPERATURA_TASA_CALENTAMIENTO 1    // °C
#define TIEMPO_TASA_CALENTAMIENTO 10        // segundos

typedef struct {
  uint32_t wcet;
  uint32_t periodo;
  UBaseType_t prioridad;
} TareaConfig;

SemaphoreHandle_t xSerialMutex;
SemaphoreHandle_t xMutexInversion;

// Variables globables
int temperaturaActual = 75;
bool valvulaAbierta = false;
unsigned int segundosEncendido = 0;
unsigned int segundosApagado = millis() - TIEMPO_DESCANSO_CALDERA * 1000;

// Configuracion de tareas
static const TareaConfig leerSensorTemperatura{ 0, 1000, configMAX_PRIORITIES - 1 };
static const TareaConfig evaluarTemperatura = { 0, 5000, configMAX_PRIORITIES - 2 };

void setup() {
  Serial.begin(115200);

  xTaskCreatePinnedToCore(
    LecturaTemperatura,
    "Leer sensor temperatura",
    4096,
    (void *)&leerSensorTemperatura,
    leerSensorTemperatura.prioridad,
    NULL,
    TASK_RUNNING_CORE);

  xTaskCreatePinnedToCore(
    EvaluacionTemperatura,
    "Evaluar temperatura",
    4096,
    (void *)&evaluarTemperatura,
    evaluarTemperatura.prioridad,
    NULL,
    TASK_RUNNING_CORE);
}

void loop() {
}

void delay_ms_activo(uint32_t ms) {
  unsigned int ticks_objetivo = (unsigned int)ms;
  unsigned int ticks = 0;
  unsigned int ticks_anterior = 0;
  int contador = 0;

  while (contador < ticks_objetivo) {
    ticks = (unsigned int)xTaskGetTickCount();
    if (ticks != ticks_anterior) {
      contador++;
    }
    ticks_anterior = ticks;
  }
}



/*
    Lee la temperatura del sensor cada 1 segundo
*/
void LecturaTemperatura(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = xTaskGetTickCount();
  for (;;) {

    if (Serial.available()) {
      String entrada = Serial.readStringUntil('\n');
      if (entrada.startsWith("T:") && entrada.length() > 2) {

        entrada.trim();

        int separador = entrada.indexOf(':');

        String valorStr = entrada.substring(separador + 1);

        if (std::regex_match(valorStr.c_str(), std::regex("\\d+"))) {
          temperaturaActual = valorStr.toInt();
          Serial.printf("Temperatura leida: %d\n", temperaturaActual);
        } else {
          Serial.printf("Temperatura actual: %d\n", temperaturaActual);
        }
      } else {
        Serial.printf("Temperatura actual: %d\n", temperaturaActual);
      }
    } else {
      Serial.printf("Temperatura actual: %d\n", temperaturaActual);
    }

    vTaskDelayUntil(
      &inicio,
      pdMS_TO_TICKS(config->periodo));
  }
}


/*
    Evalua si abrir o cerrar la valvula electrica de gas en base a la temperatura.
    Si la temperatura < 70 ºC entonces se abre la válvula electrica de gas.
    Si la temperatura > 98 ºC entonces la válvula electrica de gas se cierra inmediatamente.
*/
void EvaluacionTemperatura(void *pvParameters) {
  const TareaConfig *config = (const TareaConfig *)pvParameters;

  TickType_t inicio = xTaskGetTickCount();
  for (;;) {

    //TickType_t ahora = xTaskGetTickCount();
    unsigned int ahora = millis();

    if (valvulaAbierta && (temperaturaActual > TEMPERATURA_MAXIMA_CALDERA)) {
      valvulaAbierta = false;
      segundosApagado = ahora;
      Serial.println("Válvula Gas: CERRADA");
    } else if (temperaturaActual <= TEMPERATURA_MINIMA_CALDERA && !valvulaAbierta) {
      if (ahora - segundosApagado >= TIEMPO_MINIMO_ENCENDIDO_CALDERA * 1000) {
        valvulaAbierta = true;
        segundosEncendido = ahora;
        Serial.println("Válvula Gas: ABIERTA");
      }
    }

    vTaskDelayUntil(
      &inicio,
      pdMS_TO_TICKS(config->periodo));
  }
}

void Task3(void *pvParameters) {

  for (;;) {
  }
}