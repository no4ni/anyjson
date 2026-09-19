import torch
import torchvision
from unaiverse.networking.node.node import Node
from unaiverse.agent import Agent

# --- 1. Вставьте сюда ваш токен из аккаунта UNaIVERSE ---
TOKEN = "ВАШ_ТОКЕН"

# --- 2. Загрузите вашу модель (пример с ResNet50) ---
# Если у вас своя модель, замените эту строку на её инициализацию
model = torchvision.models.resnet50(weights="IMAGENET1K_V1").eval()

# --- 3. Создайте агента на основе модели ---
my_agent = Agent(model=model)

# --- 4. Создайте узел в вашем аккаунте и привяжите к нему агента ---
node = Node(
    token=TOKEN,
    node_name="MuzArt Creative",   # Имя узла (можете изменить)
    hosted=my_agent,               # Ваш агент
    hidden=False,                  # False — узел виден другим, True — только вам
    clock_delta=1.0                # Скорость «часов» агента
)

# --- 5. Запустите узел в сеть ---
print("Запуск агента... Нажмите Ctrl+C для остановки.")
node.run()