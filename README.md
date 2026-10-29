# Proyectos Arduino

Tres proyectos independientes desarrollados en Arduino y FreeRTOS.

## Proyectos

- **Proyecto 1: Control de Caldera de Gas.** Se implementó un sistema de control para una caldera de gas. La temperatura del agua en la caldera debía mantenerse entre los 70°C y los 98°C. En caso de que la temperatura superara los 98°C, la válvula eléctrica de gas debía cerrarse inmediatamente. La caldera debía permanecer encendida durante no menos de 10 segundos y descansar durante el mismo período de tiempo antes de volver a encenderse.

- **Proyecto 2: Bomba de Agua y Monitor de Caudal.** Se automatizó el encendido periódico de una bomba de agua para riego cuando se detectaba falta de humedad en el suelo. Si la humedad estaba por debajo del 30%, se debía encender la bomba; si estaba por encima del 90%, el sistema dejaba de regar. La bomba debía encenderse ante la falta de humedad cada 30 minutos (simulados como 30 segundos) y permanecer encendida durante 10 minutos (simulados como 10 segundos). Se debía verificar la existencia de caudal para evitar que el motor trabajara en vacío, realizando mediciones cada 1 segundo. Si no se detectaba caudal durante 5 segundos continuos, la bomba debía detenerse lo antes posible y activar una alarma durante 15 segundos. También se debía verificar que la bomba alcanzara al menos 600 rpm después de transcurridos 10 segundos (simulados como 1 segundo). De no hacerlo, se debía accionar una alarma (buzzer) durante 30 segundos.

- **Proyecto 3: Control de Temperatura de Pileta.** Se implementó el control de temperatura de una pileta mediante una caldera y una bomba de circulación. El sistema contaba con un interruptor de encendido y apagado (ON/OFF). La caldera y la bomba de circulación se encendían y apagaban mediante salidas digitales conectadas a una válvula de gas y a un relé/contactor para el motor eléctrico de la bomba. La temperatura de la pileta debía mantenerse entre los 26°C y los 30°C. Se debía verificar la temperatura del agua de la pileta cada 60 segundos (simulados como 6 segundos) y la de la caldera cada 15 segundos. Una vez encendida la caldera, la temperatura del agua interna debía mantenerse entre los 45°C y los 60°C, permaneciendo en ese rango de operación mientras el sistema estuviera encendido. La bomba de circulación debía encenderse a partir de los 55°C y apagarse por debajo de los 45°C. Se requería que la bomba funcionara durante al menos 3 minutos (simulados como 30 segundos) para lograr recircular toda el agua, y que se apagara si la temperatura de la caldera se encontraba fuera del rango o después de transcurridos 30 segundos desde que la pileta alcanzara los 30°C.

## Entorno

- Placa: ESP32
